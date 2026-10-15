# ABOUTME: Self-contained xAI OAuth PKCE login flow for sgx (no Hermes coupling)
# ABOUTME: Pure helpers + full loopback login orchestrator used by `sgx auth login`

from __future__ import annotations

import base64
import hashlib
import os
import threading
import time
import uuid
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .auth import XaiAuthStore, resolve_credentials

# --------------------------------------------------------------------------- #
# xAI OAuth constants (exact values used by Hermes + official Grok CLI)
# These must stay in sync with the allowlisted client on accounts.x.ai
# --------------------------------------------------------------------------- #

XAI_OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_OAUTH_SCOPE = "openid profile email offline_access grok-cli:access api:access"
XAI_OAUTH_ISSUER = "https://auth.x.ai"
XAI_OAUTH_DISCOVERY_URL = f"{XAI_OAUTH_ISSUER}/.well-known/openid-configuration"
XAI_DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"

# Redirect host is deliberately localhost so xAI accepts it for this client_id
XAI_REDIRECT_HOST = "127.0.0.1"
XAI_REDIRECT_PATH = "/callback"
XAI_PREFERRED_REDIRECT_PORT = 56121


def _require_xai_oauth_endpoint(name: str, value: str) -> str:
    """Require a TLS endpoint on xAI's fixed OAuth origin."""
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f"xAI {name} is invalid.") from exc

    if (
        parsed.scheme != "https"
        or parsed.hostname != "auth.x.ai"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise RuntimeError(f"xAI {name} must use the trusted https://auth.x.ai origin.")
    return value


def _require_xai_verification_uri(name: str, value: str) -> str:
    """Require a TLS verification link on xAI-owned web origins."""
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f"xAI {name} is invalid.") from exc

    hostname = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not (hostname == "x.ai" or hostname.endswith(".x.ai"))
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise RuntimeError(f"xAI {name} must use a trusted x.ai HTTPS origin.")
    return value


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
    authorization_endpoint = _require_xai_oauth_endpoint(
        "authorization_endpoint", authorization_endpoint
    )
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
) -> Dict[str, Any]:
    """Fetch OIDC discovery and return the endpoints sgx auth flows need.

    The browser PKCE login requires authorization_endpoint + token_endpoint.
    Device login additionally requires device_authorization_endpoint and, when
    discovery advertises grant_types_supported, the RFC 8628 device-code grant.
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
        raise RuntimeError(f"xAI OIDC discovery returned status {resp.status_code}.")

    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"xAI OIDC discovery returned invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("xAI OIDC discovery response was not a JSON object.")

    authz = str(payload.get("authorization_endpoint", "") or "").strip()
    token = str(payload.get("token_endpoint", "") or "").strip()
    device = str(payload.get("device_authorization_endpoint", "") or "").strip()
    grant_types_raw = payload.get("grant_types_supported") or []
    grant_types = (
        [str(item) for item in grant_types_raw] if isinstance(grant_types_raw, list) else []
    )

    if not authz or not token:
        raise RuntimeError("xAI OIDC discovery missing required endpoints.")

    authz = _require_xai_oauth_endpoint("authorization_endpoint", authz)
    token = _require_xai_oauth_endpoint("token_endpoint", token)
    if device:
        device = _require_xai_oauth_endpoint("device_authorization_endpoint", device)

    endpoints: Dict[str, Any] = {
        "authorization_endpoint": authz,
        "token_endpoint": token,
    }
    if device:
        endpoints["device_authorization_endpoint"] = device
    if grant_types:
        endpoints["grant_types_supported"] = grant_types
    return endpoints


def require_xai_device_oauth_endpoints(
    discovery: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return discovery metadata required for xAI OAuth device login.

    Raises a clear error when the provider no longer advertises RFC 8628 device
    authorization support.
    """
    endpoints = discovery or discover_xai_oauth_endpoints()
    device_endpoint = str(endpoints.get("device_authorization_endpoint", "") or "").strip()
    token_endpoint = str(endpoints.get("token_endpoint", "") or "").strip()

    if not device_endpoint:
        raise RuntimeError("xAI OIDC discovery did not advertise a device_authorization_endpoint.")
    if not token_endpoint:
        raise RuntimeError("xAI OIDC discovery did not advertise a token_endpoint.")

    device_endpoint = _require_xai_oauth_endpoint("device_authorization_endpoint", device_endpoint)
    token_endpoint = _require_xai_oauth_endpoint("token_endpoint", token_endpoint)
    endpoints = {
        **endpoints,
        "device_authorization_endpoint": device_endpoint,
        "token_endpoint": token_endpoint,
    }

    grant_types = endpoints.get("grant_types_supported") or []
    if grant_types and XAI_DEVICE_CODE_GRANT_TYPE not in grant_types:
        raise RuntimeError("xAI OIDC discovery did not advertise the OAuth device-code grant.")

    return endpoints


def exchange_code_for_tokens(
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    token_endpoint: str,
    timeout_seconds: float = 20.0,
) -> Dict[str, Any]:
    """Perform the authorization_code grant and return the token payload."""
    token_endpoint = _require_xai_oauth_endpoint("token_endpoint", token_endpoint)
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
            "xAI token exchange failed." + (f" Response: {detail}" if detail else "")
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


def refresh_xai_tokens(
    *,
    refresh_token: str,
    token_endpoint: Optional[str] = None,
    timeout_seconds: float = 20.0,
) -> Dict[str, Any]:
    """Exchange a refresh_token for a fresh token pair (RFC 6749 section 6).

    When token_endpoint is omitted it is resolved via OIDC discovery, matching
    the login flows. Returns the raw token payload; callers persist it with
    XaiAuthStore.save_xai_oauth(), which preserves the previous refresh_token
    when the provider does not rotate one.
    """
    if not refresh_token.strip():
        raise RuntimeError("Cannot refresh xAI tokens without a refresh_token.")

    if not token_endpoint:
        token_endpoint = str(discover_xai_oauth_endpoints()["token_endpoint"]).strip()
    token_endpoint = _require_xai_oauth_endpoint("token_endpoint", token_endpoint)

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token.strip(),
        "client_id": XAI_OAUTH_CLIENT_ID,
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
        raise RuntimeError(f"xAI token refresh network error: {exc}") from exc

    if resp.status_code != 200:
        detail = _oauth_error_detail(resp)
        raise RuntimeError("xAI token refresh failed." + (f" Response: {detail}" if detail else ""))

    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"xAI token refresh returned invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("xAI token refresh response was not a JSON object.")

    access = str(payload.get("access_token", "") or "").strip()
    if not access:
        raise RuntimeError("xAI token refresh did not return an access_token.")

    return payload


def request_xai_device_authorization(
    *,
    device_authorization_endpoint: str,
    scope: str = XAI_OAUTH_SCOPE,
    timeout_seconds: float = 20.0,
) -> Dict[str, Any]:
    """Request an RFC 8628 device code from xAI.

    The returned device_code is intentionally kept inside auth_login helpers; CLI
    output should show only the user-facing verification URI and user code.
    """
    device_authorization_endpoint = _require_xai_oauth_endpoint(
        "device_authorization_endpoint", device_authorization_endpoint
    )
    data = {
        "client_id": XAI_OAUTH_CLIENT_ID,
        "scope": scope,
    }

    try:
        resp = httpx.post(
            device_authorization_endpoint,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data=data,
            timeout=timeout_seconds,
        )
    except Exception as exc:
        raise RuntimeError(f"xAI device authorization network error: {exc}") from exc

    if resp.status_code != 200:
        detail = _oauth_error_detail(resp)
        raise RuntimeError(
            "xAI device authorization failed." + (f" Response: {detail}" if detail else "")
        )

    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"xAI device authorization returned invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("xAI device authorization response was not a JSON object.")

    device_code = str(payload.get("device_code", "") or "").strip()
    user_code = str(payload.get("user_code", "") or "").strip()
    verification_uri = str(payload.get("verification_uri", "") or "").strip()

    if not device_code:
        raise RuntimeError("xAI device authorization did not return a device_code.")
    if not user_code:
        raise RuntimeError("xAI device authorization did not return a user_code.")
    if not verification_uri:
        raise RuntimeError("xAI device authorization did not return a verification_uri.")

    payload["verification_uri"] = _require_xai_verification_uri(
        "verification_uri", verification_uri
    )
    verification_uri_complete = str(payload.get("verification_uri_complete", "") or "").strip()
    if verification_uri_complete:
        payload["verification_uri_complete"] = _require_xai_verification_uri(
            "verification_uri_complete", verification_uri_complete
        )

    return payload


def _oauth_error_detail(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        return resp.text.strip()

    if not isinstance(payload, dict):
        return resp.text.strip()

    error = str(payload.get("error", "") or "").strip()
    description = str(payload.get("error_description", "") or "").strip()
    if error and description:
        return f"{error}: {description}"
    return description or error or resp.text.strip()


def poll_xai_device_tokens(
    *,
    token_endpoint: str,
    device_code: str,
    interval_seconds: float = 5.0,
    expires_in: float | None = None,
    timeout_seconds: float = 900.0,
    sleep_func: Callable[[float], None] = time.sleep,
    monotonic_func: Callable[[], float] = time.monotonic,
    on_status: Callable[[str, float], None] | None = None,
) -> Dict[str, Any]:
    """Poll xAI's token endpoint until device authorization succeeds or fails.

    Implements the RFC 8628 polling states used by device-code providers:
    authorization_pending, slow_down, expired_token, and access_denied.
    """
    if not device_code.strip():
        raise RuntimeError("Cannot poll xAI device login without a device_code.")

    token_endpoint = _require_xai_oauth_endpoint("token_endpoint", token_endpoint)
    now = monotonic_func()
    effective_timeout = timeout_seconds
    if expires_in is not None:
        effective_timeout = min(timeout_seconds, max(1.0, float(expires_in)))
    deadline = now + max(1.0, effective_timeout)
    interval = max(1.0, float(interval_seconds or 5.0))

    while True:
        if monotonic_func() >= deadline:
            raise RuntimeError("xAI device authorization timed out or expired.")

        data = {
            "grant_type": XAI_DEVICE_CODE_GRANT_TYPE,
            "device_code": device_code,
            "client_id": XAI_OAUTH_CLIENT_ID,
        }

        try:
            resp = httpx.post(
                token_endpoint,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data=data,
                timeout=20.0,
            )
        except Exception as exc:
            raise RuntimeError(f"xAI device token polling network error: {exc}") from exc

        try:
            payload = resp.json()
        except Exception:
            payload = {}

        if resp.status_code == 200:
            if not isinstance(payload, dict):
                raise RuntimeError("xAI device token response was not a JSON object.")
            access = str(payload.get("access_token", "") or "").strip()
            if not access:
                raise RuntimeError("xAI device token response did not return an access_token.")
            return payload

        error = ""
        if isinstance(payload, dict):
            error = str(payload.get("error", "") or "").strip()

        if error == "authorization_pending":
            if on_status:
                on_status(error, interval)
            _sleep_until_next_device_poll(interval, deadline, sleep_func, monotonic_func)
            continue

        if error == "slow_down":
            interval += 5.0
            if on_status:
                on_status(error, interval)
            _sleep_until_next_device_poll(interval, deadline, sleep_func, monotonic_func)
            continue

        if error == "expired_token":
            raise RuntimeError("xAI device authorization expired. Start device-login again.")

        if error == "access_denied":
            raise RuntimeError("xAI device authorization was denied by the user.")

        detail = _oauth_error_detail(resp)
        raise RuntimeError(
            "xAI device token polling failed." + (f" Response: {detail}" if detail else "")
        )


def _sleep_until_next_device_poll(
    interval: float,
    deadline: float,
    sleep_func: Callable[[float], None],
    monotonic_func: Callable[[], float],
) -> None:
    remaining = deadline - monotonic_func()
    if remaining <= 0:
        return
    sleep_func(min(interval, remaining))


def login_xai_device_oauth(
    *,
    store: XaiAuthStore | None = None,
    timeout_seconds: float = 900.0,
    on_device_code: Callable[[Dict[str, Any]], None] | None = None,
    on_poll_status: Callable[[str, float], None] | None = None,
) -> Dict[str, Any]:
    """Perform the complete xAI OAuth device authorization flow.

    Returns resolved sgx credentials plus user-facing device-login metadata. The
    internal device_code and token payload are not returned.
    """
    the_store = store or XaiAuthStore()

    discovery = require_xai_device_oauth_endpoints()
    device_endpoint = str(discovery["device_authorization_endpoint"])
    token_endpoint = str(discovery["token_endpoint"])

    device = request_xai_device_authorization(
        device_authorization_endpoint=device_endpoint,
    )

    if on_device_code:
        on_device_code(
            {
                "verification_uri": str(device.get("verification_uri", "") or ""),
                "verification_uri_complete": str(device.get("verification_uri_complete", "") or ""),
                "user_code": str(device.get("user_code", "") or ""),
                "expires_in": device.get("expires_in"),
                "interval": device.get("interval"),
            }
        )

    interval = float(device.get("interval") or 5.0)
    expires_in = float(device.get("expires_in") or timeout_seconds)
    token_payload = poll_xai_device_tokens(
        token_endpoint=token_endpoint,
        device_code=str(device["device_code"]),
        interval_seconds=interval,
        expires_in=expires_in,
        timeout_seconds=timeout_seconds,
        on_status=on_poll_status,
    )

    the_store.save_xai_oauth(
        token_payload,
        base_url=DEFAULT_XAI_BASE_URL,
    )

    result = resolve_credentials(store=the_store)
    return {
        **result,
        "verification_uri": str(device.get("verification_uri", "") or ""),
        "verification_uri_complete": str(device.get("verification_uri_complete", "") or ""),
        "user_code": str(device.get("user_code", "") or ""),
        "expires_in": int(expires_in),
    }


# --------------------------------------------------------------------------- #
# Local loopback callback server (stdlib http.server + threading)
# Pattern copied from Hermes but kept minimal and self-contained.
# --------------------------------------------------------------------------- #


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
        raise RuntimeError(
            f"Could not bind xAI callback server on {host}:{preferred_port}: {last_err}"
        )

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
