# ABOUTME: Single-user OAuth 2.0 authorization-server provider for the sgx remote MCP server
# ABOUTME: DCR + PKCE + rotating refresh tokens, gated by one operator password; state persists on disk

from __future__ import annotations

import hashlib
import html
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import AnyHttpUrl
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

DEFAULT_STORE_PATH = Path.home() / ".sgx" / "mcp_oauth.json"

SCOPE = "sgx"
_ACCESS_TTL_SECONDS = 3600
_REFRESH_TTL_SECONDS = 90 * 24 * 3600
_CODE_TTL_SECONDS = 300
_PENDING_TTL_SECONDS = 600
# Global-ish login throttle: this is a single-operator server, so a simple
# failed-attempt counter per source key is enough to blunt password guessing.
_MAX_FAILED_LOGINS = 10
_FAILED_LOGIN_WINDOW_SECONDS = 600


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SgxOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """OAuth provider that lets exactly one operator (password holder) connect clients.

    Anyone may dynamically register a client (required for Claude custom
    connectors), but an authorization code is only minted after the operator
    enters SGX_MCP_PASSWORD on the consent page. Registered clients and tokens
    persist to disk (tokens hashed at rest) so a server restart does not force
    every connector to re-onboard. Authorization codes and pending login
    states are short-lived and kept in memory only.

    An optional static bearer token (SGX_MCP_TOKEN) is also accepted for
    scripted clients (MCP Inspector, Claude Code header auth, smoke tests).
    """

    def __init__(
        self,
        *,
        password: str,
        issuer_url: str,
        store_path: Optional[Path] = None,
        static_token: Optional[str] = None,
    ):
        if not password:
            raise ValueError("SgxOAuthProvider requires a non-empty password")
        self._password = password
        self._static_token = static_token or None
        self._issuer_url = issuer_url.rstrip("/")
        self._store_path = store_path or DEFAULT_STORE_PATH
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, Any]] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._failed_logins: dict[str, list[float]] = {}
        self._data: dict[str, Any] = self._load()

    # ------------------------------------------------------------------ #
    # Persistence (clients + hashed tokens only)
    # ------------------------------------------------------------------ #

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._store_path.read_text())
            if isinstance(data, dict):
                data.setdefault("clients", {})
                data.setdefault("access_tokens", {})
                data.setdefault("refresh_tokens", {})
                return data
        except Exception:
            pass
        return {"clients": {}, "access_tokens": {}, "refresh_tokens": {}}

    def _save(self) -> None:
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._store_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self._store_path)

    def _prune_expired(self) -> None:
        now = time.time()
        changed = False
        for bucket in ("access_tokens", "refresh_tokens"):
            stale = [
                k
                for k, v in self._data[bucket].items()
                if v.get("expires_at") and v["expires_at"] < now
            ]
            for k in stale:
                del self._data[bucket][k]
                changed = True
        if changed:
            self._save()

    # ------------------------------------------------------------------ #
    # SDK provider interface
    # ------------------------------------------------------------------ #

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self._lock:
            raw = self._data["clients"].get(client_id)
        if not raw:
            return None
        try:
            return OAuthClientInformationFull.model_validate(raw)
        except Exception:
            return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("No client_id provided")
        with self._lock:
            self._data["clients"][client_info.client_id] = json.loads(client_info.model_dump_json())
            self._save()

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        state = params.state or secrets.token_hex(16)
        with self._lock:
            # Drop stale pending logins opportunistically.
            now = time.time()
            self._pending = {k: v for k, v in self._pending.items() if v["expires_at"] > now}
            self._pending[state] = {
                "client_id": client.client_id,
                "client_name": client.client_name,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "code_challenge": params.code_challenge,
                "scopes": list(params.scopes or []),
                "resource": params.resource,
                "expires_at": now + _PENDING_TTL_SECONDS,
            }
        return f"{self._issuer_url}/login?state={state}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        with self._lock:
            code = self._codes.get(authorization_code)
            if code and code.expires_at < time.time():
                del self._codes[authorization_code]
                return None
            return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if not client.client_id:
            raise ValueError("No client_id provided")
        with self._lock:
            if authorization_code.code not in self._codes:
                raise ValueError("Invalid authorization code")
            del self._codes[authorization_code.code]
            return self._issue_tokens(
                client_id=client.client_id,
                scopes=authorization_code.scopes,
                resource=authorization_code.resource,
                subject=authorization_code.subject,
            )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        with self._lock:
            self._prune_expired()
            raw = self._data["refresh_tokens"].get(_hash(refresh_token))
        if not raw or raw.get("client_id") != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=raw["client_id"],
            scopes=raw.get("scopes", [SCOPE]),
            expires_at=raw.get("expires_at"),
            resource=raw.get("resource"),
            subject=raw.get("subject"),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if not client.client_id:
            raise ValueError("No client_id provided")
        granted = [s for s in scopes if s in refresh_token.scopes] or list(refresh_token.scopes)
        with self._lock:
            # Rotate: the presented refresh token dies with this exchange.
            self._data["refresh_tokens"].pop(_hash(refresh_token.token), None)
            return self._issue_tokens(
                client_id=client.client_id,
                scopes=granted,
                resource=refresh_token.resource,
                subject=refresh_token.subject,
            )

    async def load_access_token(self, token: str) -> AccessToken | None:
        if self._static_token and secrets.compare_digest(token, self._static_token):
            return AccessToken(
                token=token, client_id="static-token", scopes=[SCOPE], subject="operator"
            )
        with self._lock:
            raw = self._data["access_tokens"].get(_hash(token))
            if raw and raw.get("expires_at") and raw["expires_at"] < time.time():
                del self._data["access_tokens"][_hash(token)]
                self._save()
                return None
        if not raw:
            return None
        return AccessToken(
            token=token,
            client_id=raw["client_id"],
            scopes=raw.get("scopes", [SCOPE]),
            expires_at=raw.get("expires_at"),
            resource=raw.get("resource"),
            subject=raw.get("subject"),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with self._lock:
            self._data["access_tokens"].pop(_hash(token.token), None)
            self._data["refresh_tokens"].pop(_hash(token.token), None)
            self._save()

    # ------------------------------------------------------------------ #
    # Token issuance (caller holds the lock)
    # ------------------------------------------------------------------ #

    def _issue_tokens(
        self,
        *,
        client_id: str,
        scopes: list[str],
        resource: Optional[str],
        subject: Optional[str],
    ) -> OAuthToken:
        granted = [s for s in scopes if s == SCOPE] or [SCOPE]
        now = int(time.time())
        access_token = f"sgx_at_{secrets.token_urlsafe(32)}"
        refresh_token = f"sgx_rt_{secrets.token_urlsafe(32)}"
        self._data["access_tokens"][_hash(access_token)] = {
            "client_id": client_id,
            "scopes": granted,
            "expires_at": now + _ACCESS_TTL_SECONDS,
            "resource": resource,
            "subject": subject,
        }
        self._data["refresh_tokens"][_hash(refresh_token)] = {
            "client_id": client_id,
            "scopes": granted,
            "expires_at": now + _REFRESH_TTL_SECONDS,
            "resource": resource,
            "subject": subject,
        }
        self._prune_expired()
        self._save()
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=_ACCESS_TTL_SECONDS,
            scope=" ".join(granted),
            refresh_token=refresh_token,
        )

    # ------------------------------------------------------------------ #
    # Operator login / consent pages
    # ------------------------------------------------------------------ #

    def _get_pending(self, state: str) -> dict[str, Any]:
        with self._lock:
            pending = self._pending.get(state)
        if not pending or pending["expires_at"] < time.time():
            raise HTTPException(400, "Invalid or expired login state. Start the connection again.")
        return pending

    async def login_page(self, state: str) -> HTMLResponse:
        pending = self._get_pending(state)
        redirect_uri = pending["redirect_uri"]
        redirect_host = urlparse(redirect_uri).hostname or redirect_uri
        client_label = pending.get("client_name") or pending["client_id"]
        is_loopback = redirect_host in ("localhost", "127.0.0.1", "::1")
        loopback_warning = (
            "<p class='warn'><strong>Warning:</strong> this client redirects to a "
            "loopback address on your own machine. Only approve if you just started "
            "this connection from a local app (e.g. Claude Code).</p>"
            if is_loopback
            else ""
        )
        content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect sgx</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#0b1020; color:#e5e7eb;
         display:flex; justify-content:center; padding:40px 16px; }}
  main {{ max-width:460px; width:100%; background:#111827; border:1px solid #1f2937;
         border-radius:12px; padding:28px; }}
  h1 {{ font-size:20px; margin:0 0 12px; }}
  code {{ background:#1f2937; padding:2px 6px; border-radius:6px; word-break:break-all; }}
  label {{ display:block; margin:18px 0 6px; font-size:14px; }}
  input {{ width:100%; box-sizing:border-box; padding:10px; border-radius:8px;
          border:1px solid #374151; background:#0b1020; color:#e5e7eb; }}
  button {{ margin-top:18px; width:100%; padding:11px; border:0; border-radius:8px;
           background:#0ea5e9; color:white; font-weight:600; cursor:pointer; }}
  .warn {{ color:#fbbf24; font-size:14px; }}
  .muted {{ color:#9ca3af; font-size:14px; }}
</style>
</head>
<body>
<main>
  <h1>Connect to sgx</h1>
  <p class="muted"><strong>{html.escape(str(client_label))}</strong> wants to use your
  sgx server (X search, multi-agent research, threads). Approving lets it run tools
  that spend your xAI quota.</p>
  <p class="muted">After approval the client is redirected to
  <code>{html.escape(redirect_host)}</code>.</p>
  {loopback_warning}
  <form action="/login/callback" method="post">
    <input type="hidden" name="state" value="{html.escape(state)}">
    <label for="password">sgx MCP password</label>
    <input type="password" id="password" name="password" required autofocus
           autocomplete="current-password">
    <button type="submit">Approve connection</button>
  </form>
</main>
</body>
</html>"""
        return HTMLResponse(content=content)

    async def login_callback(self, request: Request) -> Response:
        form = await request.form()
        password = form.get("password")
        state = form.get("state")
        if not isinstance(password, str) or not isinstance(state, str) or not password or not state:
            raise HTTPException(400, "Missing password or state")

        source = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
            request.client.host if request.client else "unknown"
        )
        now = time.time()
        with self._lock:
            attempts = [
                t
                for t in self._failed_logins.get(source, [])
                if now - t < _FAILED_LOGIN_WINDOW_SECONDS
            ]
            self._failed_logins[source] = attempts
        if len(attempts) >= _MAX_FAILED_LOGINS:
            raise HTTPException(429, "Too many failed attempts. Try again later.")

        if not secrets.compare_digest(password, self._password):
            with self._lock:
                self._failed_logins[source].append(now)
            raise HTTPException(401, "Invalid password")

        pending = self._get_pending(state)
        code = f"sgx_code_{secrets.token_urlsafe(24)}"
        granted = [s for s in pending["scopes"] if s == SCOPE] or [SCOPE]
        with self._lock:
            self._codes[code] = AuthorizationCode(
                code=code,
                client_id=pending["client_id"],
                redirect_uri=AnyHttpUrl(pending["redirect_uri"]),
                redirect_uri_provided_explicitly=bool(pending["redirect_uri_provided_explicitly"]),
                expires_at=time.time() + _CODE_TTL_SECONDS,
                scopes=granted,
                code_challenge=pending["code_challenge"],
                resource=pending.get("resource"),
                subject="operator",
            )
            del self._pending[state]
            self._failed_logins.pop(source, None)
        redirect = construct_redirect_uri(pending["redirect_uri"], code=code, state=state)
        return RedirectResponse(url=redirect, status_code=302)
