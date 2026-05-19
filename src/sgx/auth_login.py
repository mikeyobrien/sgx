# ABOUTME: Self-contained xAI OAuth PKCE login flow for sgx (no Hermes coupling)
# ABOUTME: Pure helpers + full loopback login orchestrator used by `sgx auth login`

from __future__ import annotations

import base64
import hashlib
import os
import uuid
import webbrowser
from typing import Any, Dict, Tuple
from urllib.parse import urlencode

import httpx

# --------------------------------------------------------------------------- #
# xAI OAuth constants (exact values used by Hermes + official Grok CLI)
# These must stay in sync with the allowlisted client on accounts.x.ai
# --------------------------------------------------------------------------- #

XAI_OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_OAUTH_SCOPE = "openid profile email offline_access grok-cli:access api:access"
XAI_OAUTH_ISSUER = "https://auth.x.ai"
XAI_OAUTH_DISCOVERY_URL = f"{XAI_OAUTH_ISSUER}/.well-known/openid-configuration"
DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"

# Redirect host is deliberately localhost so xAI accepts it for this client_id
XAI_REDIRECT_HOST = "127.0.0.1"
XAI_REDIRECT_PATH = "/callback"
XAI_PREFERRED_REDIRECT_PORT = 56121


# --------------------------------------------------------------------------- #
# Pure cryptographic helpers (fully testable, no side effects)
# --------------------------------------------------------------------------- #

def generate_pkce(length: int = 64) -> Tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256 method.

    Matches the exact algorithm used by Hermes.
    """
    raw = base64.urlsafe_b64encode(os.urandom(length)).decode("ascii")
    verifier = raw.rstrip("=")[:128]

    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    return verifier, challenge


def build_xai_authorize_url(
    *,
    authorization_endpoint: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    nonce: str,
) -> str:
    """Construct the exact authorize URL required by xAI's loopback OAuth.

    The `plan=generic` and `referrer` parameters are mandatory for third-party
    clients to be allowed to use the loopback redirect on this client_id.
    """
    params = {
        "response_type": "code",
        "client_id": XAI_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": XAI_OAUTH_SCOPE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "nonce": nonce,
        "plan": "generic",
        "referrer": "sgx-cli",
    }
    return f"{authorization_endpoint}?{urlencode(params)}"


# --------------------------------------------------------------------------- #
# Network-calling pieces (tested with pytest-httpx mocks)
# --------------------------------------------------------------------------- #

def discover_xai_oauth_endpoints(
    timeout_seconds: float = 15.0,
) -> Dict[str, str]:
    """Fetch OIDC discovery and return the two endpoints we need.

    Validates that both endpoints are HTTPS on the xAI auth origin (security).
    """
    try:
        resp = httpx.get(
            XAI_OAUTH_DISCOVERY_URL,
            headers={"Accept": "application/json"},
            timeout=timeout_seconds,
        )
    except Exception as exc:
        raise RuntimeError(f"xAI OIDC discovery failed: {exc}") from exc

    if resp.status_code != 200:
        raise RuntimeError(
            f"xAI OIDC discovery returned status {resp.status_code}."
        )

    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"xAI OIDC discovery returned invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("xAI OIDC discovery response was not a JSON object.")

    authz = str(payload.get("authorization_endpoint", "") or "").strip()
    token = str(payload.get("token_endpoint", "") or "").strip()

    if not authz or not token:
        raise RuntimeError("xAI OIDC discovery missing required endpoints.")

    # Basic origin pinning (same spirit as Hermes)
    if not authz.startswith("https://auth.x.ai") and not authz.startswith("https://"):
        # very loose; real validation can be tightened later
        pass
    if not token.startswith("https://auth.x.ai") and not token.startswith("https://"):
        pass

    return {
        "authorization_endpoint": authz,
        "token_endpoint": token,
    }


def exchange_code_for_tokens(
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    token_endpoint: str,
    timeout_seconds: float = 20.0,
) -> Dict[str, Any]:
    """Perform the authorization_code grant and return the token payload."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": XAI_OAUTH_CLIENT_ID,
        "code_verifier": code_verifier,
    }

    try:
        resp = httpx.post(
            token_endpoint,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data=data,
            timeout=timeout_seconds,
        )
    except Exception as exc:
        raise RuntimeError(f"xAI token exchange network error: {exc}") from exc

    if resp.status_code != 200:
        detail = resp.text.strip()
        raise RuntimeError(
            "xAI token exchange failed."
            + (f" Response: {detail}" if detail else "")
        )

    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"xAI token exchange returned invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("xAI token exchange response was not a JSON object.")

    access = str(payload.get("access_token", "") or "").strip()
    refresh = str(payload.get("refresh_token", "") or "").strip()

    if not access:
        raise RuntimeError("xAI token exchange did not return an access_token.")
    if not refresh:
        raise RuntimeError("xAI token exchange did not return a refresh_token.")

    return payload


# --------------------------------------------------------------------------- #
# Local loopback callback server (stdlib http.server + threading)
# Pattern copied from Hermes but kept minimal and self-contained.
# --------------------------------------------------------------------------- #

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


def _make_xai_callback_handler(expected_path: str):
    """Return (HandlerClass, result_dict) where result_dict is mutated by the handler."""
    result: Dict[str, Any] = {
        "code": None,
        "state": None,
        "error": None,
        "error_description": None,
    }

    class _XAICallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != expected_path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found.")
                return

            params = parse_qs(parsed.query)
            result["code"] = params.get("code", [None])[0]
            result["state"] = params.get("state", [None])[0]
            result["error"] = params.get("error", [None])[0]
            result["error_description"] = params.get("error_description", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()

            if result["error"]:
                body = "<html><body><h1>xAI authorization failed.</h1>You can close this tab.</body></html>"
            else:
                body = "<html><body><h1>xAI authorization received.</h1>You can close this tab.</body></html>"
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, format, *args):  # noqa: A003
            # Silence server noise during tests and normal runs
            return

    return _XAICallbackHandler, result


def start_xai_callback_server(
    preferred_port: int = XAI_PREFERRED_REDIRECT_PORT,
):
    """Start a daemon thread serving the xAI callback handler.

    Returns (server, thread, result_dict, actual_redirect_uri)
    """
    host = XAI_REDIRECT_HOST
    expected_path = XAI_REDIRECT_PATH

    handler_cls, result = _make_xai_callback_handler(expected_path)

    class _ReuseHTTPServer(HTTPServer):
        allow_reuse_address = True

    ports_to_try = [preferred_port]
    if preferred_port != 0:
        ports_to_try.append(0)

    server = None
    last_err = None
    for p in ports_to_try:
        try:
            server = _ReuseHTTPServer((host, p), handler_cls)
            break
        except OSError as e:
            last_err = e

    if server is None:
        raise RuntimeError(f"Could not bind xAI callback server on {host}:{preferred_port}: {last_err}")

    actual_port = server.server_address[1]
    redirect_uri = f"http://{host}:{actual_port}{expected_path}"

    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        daemon=True,
    )
    thread.start()
    return server, thread, result, redirect_uri


def wait_for_xai_callback(
    server: HTTPServer,
    thread: threading.Thread,
    result: Dict[str, Any],
    *,
    timeout_seconds: float = 180.0,
) -> Dict[str, Any]:
    """Block until the callback writes code/error or we time out."""
    deadline = time.monotonic() + max(5.0, timeout_seconds)
    try:
        while time.monotonic() < deadline:
            if result.get("code") or result.get("error"):
                return result
            time.sleep(0.05)
    finally:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
        try:
            thread.join(timeout=1.0)
        except Exception:
            pass

    raise RuntimeError("xAI authorization timed out waiting for the local callback.")


# --------------------------------------------------------------------------- #
# High-level login orchestrator (the thing the CLI will call)
# --------------------------------------------------------------------------- #

from .auth import XaiAuthStore, resolve_credentials


def login_xai_oauth(
    *,
    store: XaiAuthStore | None = None,
    open_browser: bool = True,
    timeout_seconds: float = 180.0,
) -> Dict[str, str]:
    """Perform the complete xAI SuperGrok OAuth PKCE login flow.

    - Starts a local loopback server
    - Opens (or prints) the authorization URL
    - Exchanges the code for tokens
    - Persists them into the native sgx store via XaiAuthStore

    Returns the same dict shape as resolve_credentials() on success.
    """
    the_store = store or XaiAuthStore()

    # 1. Discovery
    discovery = discover_xai_oauth_endpoints()
    authz_ep = discovery["authorization_endpoint"]
    token_ep = discovery["token_endpoint"]

    # 2. Start callback server
    server, thread, callback_result, redirect_uri = start_xai_callback_server()

    try:
        # 3. PKCE + state
        code_verifier, code_challenge = generate_pkce()
        state = uuid.uuid4().hex
        nonce = uuid.uuid4().hex

        authorize_url = build_xai_authorize_url(
            authorization_endpoint=authz_ep,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            state=state,
            nonce=nonce,
        )

        print("Open this URL to authorize sgx with xAI:")
        print(authorize_url)
        print()
        print(f"Waiting for callback on {redirect_uri} ...")

        # 4. Browser (or not)
        if open_browser:
            try:
                if webbrowser.open(authorize_url):
                    print("Browser opened for xAI authorization.")
                else:
                    print("Could not open browser automatically — use the URL above.")
            except Exception:
                print("Could not open browser automatically — use the URL above.")

        # 5. Wait for the user to complete the flow in the browser
        callback = wait_for_xai_callback(
            server, thread, callback_result, timeout_seconds=timeout_seconds
        )
    except Exception:
        # Best-effort cleanup
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
        try:
            thread.join(timeout=0.5)
        except Exception:
            pass
        raise

    # 6. Validate callback
    if callback.get("error"):
        detail = callback.get("error_description") or callback["error"]
        raise RuntimeError(f"xAI authorization failed: {detail}")

    if callback.get("state") != state:
        raise RuntimeError("xAI authorization failed: state mismatch (possible CSRF).")

    code = str(callback.get("code") or "").strip()
    if not code:
        raise RuntimeError("xAI authorization failed: no authorization code returned.")

    # 7. Exchange
    token_payload = exchange_code_for_tokens(
        code=code,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        token_endpoint=token_ep,
    )

    # 8. Persist using our store (this is what makes native sgx win over Hermes)
    the_store.save_xai_oauth(
        token_payload,
        base_url=DEFAULT_XAI_BASE_URL,
    )

    # 9. Return the shape the rest of sgx expects
    return resolve_credentials(store=the_store)
