# ABOUTME: xAI credential resolution for sgx (native store + Hermes fallback)
# ABOUTME: Designed for testability — no hard runtime coupling to Hermes

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_SGX_AUTH_PATH = Path.home() / ".sgx" / "auth.json"
DEFAULT_HERMES_AUTH_PATH = Path.home() / ".hermes" / "auth.json"
DEFAULT_GROK_AUTH_PATH = Path.home() / ".grok" / "auth.json"
HERMES_ENV_PATH = Path.home() / ".hermes" / ".env"


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
                    "api_key": str(token).strip(),
                    "base_url": entry.get("base_url") or "https://api.x.ai/v1",
                }
        return None

    def load_hermes_xai_oauth(self) -> Optional[Dict[str, Any]]:
        data = _load_json(self.hermes_auth_path)
        providers = data.get("providers", {}) or {}
        if "xai-oauth" in providers:
            entry = providers["xai-oauth"]
            token = entry.get("access_token") or entry.get("api_key")
            if token:
                return {
                    "provider": "xai-oauth",
                    "api_key": str(token).strip(),
                    "base_url": entry.get("base_url") or "https://api.x.ai/v1",
                }
        # credential_pool fallback
        pool = data.get("credential_pool", {}) or {}
        for key in ("xai-oauth", "xai"):
            if key in pool and isinstance(pool[key], list) and pool[key]:
                entry = pool[key][0]
                token = entry.get("access_token") or entry.get("api_key")
                if token:
                    return {
                        "provider": "xai-oauth" if key == "xai-oauth" else "xai",
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
        Only the access_token is required for immediate use.
        """
        self.sgx_auth_path.parent.mkdir(parents=True, exist_ok=True)

        existing = _load_json(self.sgx_auth_path)
        providers = existing.setdefault("providers", {})

        entry: Dict[str, Any] = {
            "access_token": str(tokens.get("access_token", "")).strip(),
        }
        if tokens.get("refresh_token"):
            entry["refresh_token"] = str(tokens["refresh_token"]).strip()
        if base_url:
            entry["base_url"] = base_url
        elif "base_url" not in entry:
            entry["base_url"] = "https://api.x.ai/v1"

        # Preserve other fields if the user somehow had them
        for k in ("id_token", "expires_in", "token_type", "obtained_at"):
            if k in tokens and tokens[k] is not None:
                entry[k] = tokens[k]

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


def resolve_credentials(
    *,
    store: Optional[XaiAuthStore] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """
    Resolve xAI credentials with this priority:

    1. Native sgx store (~/.sgx/auth.json)          ← preferred going forward
    2. Hermes store (~/.hermes/auth.json)           ← compatibility fallback
    3. XAI_API_KEY from environment or ~/.hermes/.env

    The store is injectable so tests can run in complete isolation.
    """
    store = store or XaiAuthStore()
    env_dict: Dict[str, str] = env if env is not None else dict(os.environ)

    # 1. Native sgx store (future primary path)
    sgx = store.load_sgx_xai_oauth()
    if sgx:
        return sgx

    # 2. Hermes fallback (pure file read)
    hermes = store.load_hermes_xai_oauth()
    if hermes:
        return hermes

    # 3. Official Grok CLI (~/.grok/auth.json)
    grok = store.load_grok_cli_xai_oauth()
    if grok:
        return grok

    # 4. Environment
    key = env_dict.get("XAI_API_KEY")
    if not key:
        dotenv = _load_dotenv_values(HERMES_ENV_PATH)
        key = dotenv.get("XAI_API_KEY")

    if key:
        return {
            "provider": "xai",
            "api_key": key,
            "base_url": env_dict.get("XAI_BASE_URL", "https://api.x.ai/v1"),
        }

    raise RuntimeError(
        "No xAI credentials found.\nRun `sgx auth login` (once Phase 2 is done) or set XAI_API_KEY."
    )


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
