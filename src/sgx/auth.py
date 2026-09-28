# ABOUTME: xAI credential resolution for sgx (native store + Hermes fallback)
# ABOUTME: Designed for testability — no hard runtime coupling to Hermes

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional


DEFAULT_SGX_AUTH_PATH = Path.home() / ".sgx" / "auth.json"
DEFAULT_HERMES_AUTH_PATH = Path.home() / ".hermes" / "auth.json"
DEFAULT_GROK_AUTH_PATH = Path.home() / ".grok" / "auth.json"
HERMES_ENV_PATH = Path.home() / ".hermes" / ".env"


HERMES_UNUSABLE_XAI_AUTH_CODES = {
    "xai_auth_missing_access_token",
    "xai_auth_missing_refresh_token",
    "xai_auth_invalid",
    "xai_auth_malformed",
}


class HermesCredentialsUnavailable(RuntimeError):
    """Hermes xAI OAuth is present but unusable, so lower-priority sources may run."""


class XaiAuthStore:
    """Injectable auth store for testability and future native sgx login."""

    def __init__(
        self,
        sgx_base: Optional[Path] = None,
        hermes_base: Optional[Path] = None,
        grok_base: Optional[Path] = None,
        sgx_auth_path: Optional[Path] = None,
        hermes_auth_path: Optional[Path] = None,
        grok_auth_path: Optional[Path] = None,
    ):
        self.diagnostics: list[str] = []
        if sgx_auth_path:
            self.sgx_auth_path = sgx_auth_path
        else:
            self.sgx_auth_path = (sgx_base or DEFAULT_SGX_AUTH_PATH.parent) / "auth.json"

        if hermes_auth_path:
            self.hermes_auth_path = hermes_auth_path
        else:
            self.hermes_auth_path = (hermes_base or DEFAULT_HERMES_AUTH_PATH.parent) / "auth.json"

        if grok_auth_path:
            self.grok_auth_path = grok_auth_path
        else:
            self.grok_auth_path = (grok_base or DEFAULT_GROK_AUTH_PATH.parent) / "auth.json"

    def load_sgx_xai_oauth(self) -> Optional[Dict[str, Any]]:
        data = _load_json(self.sgx_auth_path)
        providers = data.get("providers", {}) or {}
        if "xai-oauth" in providers:
            entry = providers["xai-oauth"]
            token = entry.get("access_token") or entry.get("api_key")
            if token:
                return {
                    "provider": "xai-oauth",
                    "source": "sgx",
                    "api_key": str(token).strip(),
                    "base_url": entry.get("base_url") or "https://api.x.ai/v1",
                }
        return None

    def refresh_sgx_access_token(self) -> bool:
        """Force a refresh of the native sgx access token via its refresh_token.

        Returns True when fresh tokens were obtained and persisted. Returns
        False when there is nothing to refresh with or the provider refused.
        Never raises for provider-side failures — callers decide the fallback.
        """
        data = _load_json(self.sgx_auth_path)
        providers = data.get("providers", {}) or {}
        entry = providers.get("xai-oauth")
        if not isinstance(entry, dict):
            return False
        refresh_token = str(entry.get("refresh_token") or "").strip()
        if not refresh_token:
            self.diagnostics.append(
                "sgx xAI OAuth token needs refresh but no refresh_token is stored"
            )
            return False
        # Lazy import: auth_login imports this module at its top level.
        from .auth_login import refresh_xai_tokens

        try:
            payload = refresh_xai_tokens(refresh_token=refresh_token)
        except RuntimeError as exc:
            self.diagnostics.append(
                f"sgx xAI OAuth token refresh failed: {_redact_auth_error_text(str(exc))}"
            )
            return False
        self.save_xai_oauth(payload, base_url=entry.get("base_url"))
        return True

    def resolve_sgx_xai_oauth(self) -> Optional[Dict[str, Any]]:
        """Load the native sgx xAI OAuth credentials, refreshing when expired.

        Unlike load_sgx_xai_oauth() (kept as a pure loader for tests), this is
        the runtime path: a provably-expired access token is refreshed before
        it is handed out. When expiry cannot be determined from the stored
        entry, the token is returned as-is and the client's 401/403 retry path
        recovers instead.
        """
        data = _load_json(self.sgx_auth_path)
        providers = data.get("providers", {}) or {}
        entry = providers.get("xai-oauth")
        if not isinstance(entry, dict):
            return None
        token = str(entry.get("access_token") or entry.get("api_key") or "").strip()
        if not token:
            return None
        if _sgx_access_token_expired(entry):
            self.diagnostics.append("sgx xAI OAuth access token expired — refreshing")
            if not self.refresh_sgx_access_token():
                return None
            # Re-read the freshly saved entry
            data = _load_json(self.sgx_auth_path)
            entry = (data.get("providers", {}) or {}).get("xai-oauth") or {}
            token = str(entry.get("access_token") or "").strip()
            if not token:
                return None
        return {
            "provider": "xai-oauth",
            "source": "sgx",
            "api_key": token,
            "base_url": entry.get("base_url") or "https://api.x.ai/v1",
        }

    def load_hermes_xai_oauth(self) -> Optional[Dict[str, Any]]:
        try:
            hermes_runtime = _resolve_hermes_xai_runtime_credentials(self.hermes_auth_path)
        except HermesCredentialsUnavailable as exc:
            self.diagnostics.append(str(exc))
            return None
        if hermes_runtime:
            return hermes_runtime

        data = _load_json(self.hermes_auth_path)
        providers = data.get("providers", {}) or {}
        if "xai-oauth" in providers:
            entry = providers["xai-oauth"]
            if not isinstance(entry, dict):
                self.diagnostics.append(
                    "Skipped unusable Hermes xAI OAuth credentials: invalid provider shape"
                )
                return None
            raw_tokens = entry.get("tokens")
            tokens = raw_tokens if isinstance(raw_tokens, dict) else entry
            token = tokens.get("access_token") or tokens.get("api_key")
            if token:
                return {
                    "provider": "xai-oauth",
                    "source": "hermes",
                    "api_key": str(token).strip(),
                    "base_url": entry.get("base_url") or "https://api.x.ai/v1",
                }
            if _is_default_hermes_auth_path(self.hermes_auth_path):
                self.diagnostics.append(
                    "Skipped unusable Hermes xAI OAuth credentials: missing access_token"
                )
                return None
        # credential_pool fallback
        pool = data.get("credential_pool", {}) or {}
        for key in ("xai-oauth", "xai"):
            if key in pool and isinstance(pool[key], list) and pool[key]:
                entry = pool[key][0]
                token = entry.get("access_token") or entry.get("api_key")
                if token:
                    return {
                        "provider": "xai-oauth" if key == "xai-oauth" else "xai",
                        "source": "hermes",
                        "api_key": str(token).strip(),
                        "base_url": entry.get("base_url") or "https://api.x.ai/v1",
                    }
        return None

    def load_grok_cli_xai_oauth(self) -> Optional[Dict[str, Any]]:
        """Load credentials from the official Grok CLI store (~/.grok/auth.json).

        The official CLI stores tokens under a composite key like
        "https://auth.x.ai::<client_id>" with "key" as the access token
        and a separate "refresh_token" field.
        """
        data = _load_json(self.grok_auth_path)
        if not data:
            return None

        # Look for the xAI OAuth entry (the key contains the issuer + client id)
        for entry_key, entry in data.items():
            if not isinstance(entry, dict):
                continue
            if "auth.x.ai" not in entry_key:
                continue

            access_token = entry.get("key")
            refresh_token = entry.get("refresh_token")

            if access_token:
                result = {
                    "provider": "xai-oauth",
                    "source": "grok-cli",
                    "api_key": str(access_token).strip(),
                    "base_url": "https://api.x.ai/v1",
                }
                if refresh_token:
                    result["refresh_token"] = str(refresh_token).strip()
                return result

        return None

    # --------------------------------------------------------------------- #
    # Write-side API (used by `sgx auth login` and `sgx auth logout`)
    # --------------------------------------------------------------------- #

    def save_xai_oauth(
        self,
        tokens: Dict[str, Any],
        *,
        base_url: Optional[str] = None,
    ) -> None:
        """Persist an xai-oauth entry into the native sgx auth store.

        Accepts the shape returned by a successful token exchange:
            {"access_token": "...", "refresh_token": "...", ...}
        Only the access_token is required for immediate use. obtained_at is
        always stamped so the entry's expiry can be computed later; a stored
        refresh_token is preserved across refreshes that don't rotate one.
        """
        self.sgx_auth_path.parent.mkdir(parents=True, exist_ok=True)

        existing = _load_json(self.sgx_auth_path)
        providers = existing.setdefault("providers", {})
        previous = providers.get("xai-oauth")
        previous = previous if isinstance(previous, dict) else {}

        entry: Dict[str, Any] = {
            "access_token": str(tokens.get("access_token", "")).strip(),
            "obtained_at": int(time.time()),
        }
        if tokens.get("refresh_token"):
            entry["refresh_token"] = str(tokens["refresh_token"]).strip()
        elif previous.get("refresh_token"):
            entry["refresh_token"] = str(previous["refresh_token"]).strip()
        if base_url:
            entry["base_url"] = base_url
        elif previous.get("base_url"):
            entry["base_url"] = previous["base_url"]
        else:
            entry["base_url"] = "https://api.x.ai/v1"

        # Preserve other fields if the user somehow had them
        for k in ("id_token", "expires_in", "token_type"):
            if k in tokens and tokens[k] is not None:
                entry[k] = tokens[k]
            elif k in previous and previous[k] is not None:
                entry[k] = previous[k]

        providers["xai-oauth"] = entry

        self.sgx_auth_path.write_text(json.dumps(existing, indent=2))

    def clear(self) -> None:
        """Remove the native sgx xai-oauth entry (or the whole file if empty after removal).

        Hermes fallback and environment variables are unaffected.
        Safe to call when nothing exists.
        """
        if not self.sgx_auth_path.exists():
            return

        data = _load_json(self.sgx_auth_path)
        providers = data.get("providers", {}) or {}
        providers.pop("xai-oauth", None)

        if not providers and len(data) <= 1:
            # Only had providers (now empty) or was trivial — delete the file
            try:
                self.sgx_auth_path.unlink()
            except FileNotFoundError:
                pass
        else:
            data["providers"] = providers
            self.sgx_auth_path.write_text(json.dumps(data, indent=2))


# Refresh a bit before the token actually dies so a slow call doesn't race it.
SGX_TOKEN_REFRESH_SKEW_SECONDS = 60


def _sgx_access_token_expired(entry: Dict[str, Any]) -> bool:
    """True when the stored entry provably outlived its access token.

    Also True when a lifetime is known but the entry's age is not (legacy
    entries saved before obtained_at was stamped): a refresh is cheap, so
    assume stale rather than serve a dead token. Returns False only when no
    lifetime is known at all — then the token is used as-is and the client's
    401/403 retry path recovers instead of guessing.
    """
    try:
        lifetime = float(entry.get("expires_in"))
    except (TypeError, ValueError):
        return False
    if lifetime <= 0:
        return True
    try:
        obtained = float(entry.get("obtained_at"))
    except (TypeError, ValueError):
        return True
    if obtained <= 0:
        return True
    return time.time() + SGX_TOKEN_REFRESH_SKEW_SECONDS >= obtained + lifetime


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def _load_dotenv_values(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    values: Dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def _is_default_hermes_auth_path(hermes_auth_path: Path) -> bool:
    return hermes_auth_path.expanduser().resolve(strict=False) == DEFAULT_HERMES_AUTH_PATH.resolve(
        strict=False
    )


def _import_hermes_auth_from_checkout(hermes_auth_path: Path) -> Optional[ModuleType]:
    hermes_root = hermes_auth_path.parent / "hermes-agent"
    if not hermes_root.exists():
        return None

    inserted = False
    hermes_root_str = str(hermes_root)
    if hermes_root_str not in sys.path:
        sys.path.insert(0, hermes_root_str)
        inserted = True

    try:
        return import_module("hermes_cli.auth")
    except ImportError:
        return None
    finally:
        if inserted:
            try:
                sys.path.remove(hermes_root_str)
            except ValueError:
                pass


def _import_hermes_auth(hermes_auth_path: Path) -> Optional[ModuleType]:
    """Import Hermes auth from the installed package, then the sibling checkout.

    The installed package path is the portable case for sgx users. Mikey's
    checkout layout also has ~/.hermes/hermes-agent, so keep that as a fallback
    without leaving any sys.path mutation behind.
    """
    try:
        return import_module("hermes_cli.auth")
    except ImportError:
        return _import_hermes_auth_from_checkout(hermes_auth_path)


def _redact_auth_error_text(text: str) -> str:
    # Keep human-readable diagnostics, but strip token-shaped chunks even when
    # they are digitless (base64/JWT/OAuth implementations are not guaranteed
    # to include numerals in every opaque segment).
    redacted = re.sub(
        r"\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]{10,})?\b",
        "[redacted]",
        text,
    )
    redacted = re.sub(
        r"\b[A-Za-z0-9+/=._-]{24,}\b",
        "[redacted]",
        redacted,
    )
    return redacted


def _format_hermes_resolver_error(exc: Exception) -> RuntimeError:
    code = getattr(exc, "code", None)
    provider = getattr(exc, "provider", None)
    prefix = "Hermes xAI OAuth resolver failed"
    details = _redact_auth_error_text(str(exc).strip())
    parts = [prefix]
    if code:
        parts.append(f"code={code}")
    if provider:
        parts.append(f"provider={provider}")
    if details:
        parts.append(details)
    return RuntimeError(": ".join(parts))


def _format_hermes_checkout_resolver_failure(message: str) -> RuntimeError:
    details = _redact_auth_error_text(message.strip())
    if details:
        return RuntimeError(f"Hermes xAI OAuth resolver failed: {details}")
    return RuntimeError("Hermes xAI OAuth resolver failed")


def _is_missing_hermes_xai_auth(exc: Exception, hermes_auth: Optional[ModuleType]) -> bool:
    auth_error = getattr(hermes_auth, "AuthError", None)
    code = str(getattr(exc, "code", "") or "")
    return (
        auth_error is not None
        and isinstance(exc, auth_error)
        and exc.__class__.__module__ == "hermes_cli.auth"
        and code == "xai_auth_missing"
    )


def _is_unusable_hermes_xai_auth(exc: Exception, hermes_auth: Optional[ModuleType]) -> bool:
    # Candidate credential absence/incompleteness/corruption should not block
    # documented lower-priority sources. Arbitrary resolver/runtime failures
    # still surface as hard errors.
    auth_error = getattr(hermes_auth, "AuthError", None)
    code = str(getattr(exc, "code", "") or "")
    return (
        auth_error is not None
        and isinstance(exc, auth_error)
        and exc.__class__.__module__ == "hermes_cli.auth"
        and code in HERMES_UNUSABLE_XAI_AUTH_CODES
    )


def _format_hermes_unavailable(exc: Exception) -> HermesCredentialsUnavailable:
    formatted = _format_hermes_resolver_error(exc)
    return HermesCredentialsUnavailable(
        f"Skipped unusable Hermes xAI OAuth credentials: {formatted}"
    )


def _coerce_hermes_runtime_credentials(creds: Dict[str, Any]) -> Dict[str, Any]:
    token = str(creds.get("api_key", "") or "").strip()
    if not token:
        raise RuntimeError(
            "Hermes xAI OAuth resolver failed: missing api_key in runtime credentials"
        )
    return {
        "provider": "xai-oauth",
        "source": "hermes",
        "api_key": token,
        "base_url": str(creds.get("base_url") or "https://api.x.ai/v1").rstrip("/"),
    }


def _resolve_hermes_xai_runtime_credentials_from_checkout(
    hermes_auth_path: Path,
) -> Optional[Dict[str, Any]]:
    hermes_root = hermes_auth_path.parent / "hermes-agent"
    python_bin = hermes_root / ".venv" / "bin" / "python"
    if not python_bin.exists():
        return None

    script = """
import json
from hermes_cli.auth import resolve_xai_oauth_runtime_credentials

try:
    creds = resolve_xai_oauth_runtime_credentials(refresh_if_expiring=True)
except Exception as exc:
    print(json.dumps({
        "ok": False,
        "type": exc.__class__.__name__,
        "code": getattr(exc, "code", None),
        "provider": getattr(exc, "provider", None),
        "module": exc.__class__.__module__,
        "message": str(exc),
    }))
else:
    print(json.dumps({
        "ok": True,
        "api_key": creds.get("api_key"),
        "base_url": creds.get("base_url"),
    }))
"""
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(hermes_root)
        if not existing_pythonpath
        else f"{hermes_root}{os.pathsep}{existing_pythonpath}"
    )
    try:
        proc = subprocess.run(
            [str(python_bin), "-c", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _format_hermes_checkout_resolver_failure(str(exc)) from None
    if proc.returncode != 0:
        raise _format_hermes_checkout_resolver_failure(
            proc.stderr or proc.stdout or f"checkout resolver exited {proc.returncode}"
        ) from None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise _format_hermes_checkout_resolver_failure(
            "checkout resolver returned malformed JSON"
        ) from None
    if payload.get("ok"):
        return _coerce_hermes_runtime_credentials(payload)

    if (
        payload.get("type") == "AuthError"
        and payload.get("module") == "hermes_cli.auth"
        and str(payload.get("code") or "") == "xai_auth_missing"
    ):
        return None

    err = RuntimeError(str(payload.get("message") or "Hermes xAI OAuth resolver failed"))
    if payload.get("code"):
        setattr(err, "code", payload["code"])
    if payload.get("provider"):
        setattr(err, "provider", payload["provider"])
    if (
        payload.get("type") == "AuthError"
        and payload.get("module") == "hermes_cli.auth"
        and str(payload.get("code") or "") in HERMES_UNUSABLE_XAI_AUTH_CODES
    ):
        raise _format_hermes_unavailable(err) from None
    raise _format_hermes_resolver_error(err) from None


def _resolve_hermes_xai_runtime_credentials(hermes_auth_path: Path) -> Optional[Dict[str, Any]]:
    """Use Hermes' xAI OAuth resolver when available.

    Hermes owns xAI OAuth refresh-token lifecycle and stores canonical refreshed
    tokens under providers["xai-oauth"]["tokens"]. Prefer the resolver from an
    installed Hermes package, falling back to Mikey's ~/.hermes/hermes-agent
    checkout. Import/shape absence is safe to fall back from; resolver runtime
    auth/configuration failures are surfaced so doctor does not hide them behind
    stale raw credential_pool entries.
    """
    if not _is_default_hermes_auth_path(hermes_auth_path):
        return None

    hermes_auth = _import_hermes_auth(hermes_auth_path)
    if hermes_auth is None:
        return _resolve_hermes_xai_runtime_credentials_from_checkout(hermes_auth_path)

    try:
        resolve_xai_oauth_runtime_credentials = getattr(
            hermes_auth, "resolve_xai_oauth_runtime_credentials"
        )
    except AttributeError:
        return None

    try:
        creds = resolve_xai_oauth_runtime_credentials(refresh_if_expiring=True)
    except Exception as exc:
        if _is_missing_hermes_xai_auth(exc, hermes_auth):
            return None
        if _is_unusable_hermes_xai_auth(exc, hermes_auth):
            raise _format_hermes_unavailable(exc) from None
        raise _format_hermes_resolver_error(exc) from None

    return _coerce_hermes_runtime_credentials(creds)


def _with_diagnostics(creds: Dict[str, Any], diagnostics: list[str]) -> Dict[str, Any]:
    if diagnostics:
        return {**creds, "diagnostics": list(diagnostics)}
    return creds


def _credential_not_found_error(diagnostics: list[str]) -> RuntimeError:
    message = (
        "No xAI credentials found.\nRun `sgx auth login` (once Phase 2 is done) or set XAI_API_KEY."
    )
    if diagnostics:
        message += "\n" + "\n".join(diagnostics)
    return RuntimeError(message)


def resolve_credentials(
    *,
    store: Optional[XaiAuthStore] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Resolve xAI credentials with this priority:

    1. Native sgx store (~/.sgx/auth.json)          ← preferred going forward
    2. Hermes store (~/.hermes/auth.json)           ← compatibility fallback
    3. XAI_API_KEY from environment or ~/.hermes/.env

    The store is injectable so tests can run in complete isolation.
    """
    store = store or XaiAuthStore()
    env_dict: Dict[str, str] = env if env is not None else dict(os.environ)

    # 1. Native sgx store (future primary path) — refreshes when expired
    sgx = store.resolve_sgx_xai_oauth()
    if sgx:
        return _with_diagnostics(sgx, store.diagnostics)

    # 2. Hermes fallback (runtime resolver, then pure file read)
    hermes = store.load_hermes_xai_oauth()
    if hermes:
        return _with_diagnostics(hermes, store.diagnostics)

    # 3. Official Grok CLI (~/.grok/auth.json)
    grok = store.load_grok_cli_xai_oauth()
    if grok:
        return _with_diagnostics(grok, store.diagnostics)

    # 4. Environment
    key = env_dict.get("XAI_API_KEY")
    if not key:
        dotenv = _load_dotenv_values(HERMES_ENV_PATH)
        key = dotenv.get("XAI_API_KEY")

    if key:
        return _with_diagnostics(
            {
                "provider": "xai",
                "source": "env",
                "api_key": key,
                "base_url": env_dict.get("XAI_BASE_URL", "https://api.x.ai/v1"),
            },
            store.diagnostics,
        )

    raise _credential_not_found_error(store.diagnostics)


# --------------------------------------------------------------------------- #
# Public convenience helpers (used by client.py and CLI)
# --------------------------------------------------------------------------- #


def get_xai_auth(
    credentials: Optional[Dict[str, str]] = None,
) -> tuple[str, str]:
    """Return (api_key, base_url) after running the full resolution chain.

    Raises RuntimeError with a helpful message if no valid credentials are found.
    """
    if credentials:
        api_key = (credentials.get("api_key") or "").strip()
        base_url = (credentials.get("base_url") or "https://api.x.ai/v1").strip().rstrip("/")
    else:
        resolved = resolve_credentials()
        api_key = resolved["api_key"]
        base_url = resolved["base_url"]

    if not api_key:
        raise RuntimeError("No xAI credentials found.\nRun `sgx auth login` or set XAI_API_KEY.")

    if not base_url.startswith("http"):
        raise RuntimeError(f"Invalid base_url for xAI: {base_url or '(empty)'}")

    return api_key, base_url
