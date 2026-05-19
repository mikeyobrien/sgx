# ABOUTME: Tests for sgx auth resolution and storage (fully isolated)
# ABOUTME: Uses temporary directories so tests never touch real ~/.sgx or ~/.hermes

import json
import tempfile
from pathlib import Path

from sgx.auth import XaiAuthStore, resolve_credentials


def test_prefers_sgx_store_over_hermes():
    with tempfile.TemporaryDirectory() as tmp:
        sgx_dir = Path(tmp) / "sgx"
        hermes_dir = Path(tmp) / "hermes"

        sgx_store = XaiAuthStore(sgx_base=sgx_dir, hermes_base=hermes_dir)

        # Write a fake Hermes entry
        hermes_dir.mkdir(parents=True)
        (hermes_dir / "auth.json").write_text(
            json.dumps({"providers": {"xai-oauth": {"access_token": "hermes-token"}}})
        )

        # Write a native sgx entry (should win)
        sgx_dir.mkdir(parents=True)
        (sgx_dir / "auth.json").write_text(
            json.dumps({"providers": {"xai-oauth": {"access_token": "sgx-token"}}})
        )

        result = resolve_credentials(store=sgx_store)
        assert result["api_key"] == "sgx-token"
        assert result["provider"] == "xai-oauth"


def test_falls_back_to_hermes_when_no_sgx_store():
    with tempfile.TemporaryDirectory() as tmp:
        sgx_dir = Path(tmp) / "sgx"
        hermes_dir = Path(tmp) / "hermes"

        store = XaiAuthStore(sgx_base=sgx_dir, hermes_base=hermes_dir)

        hermes_dir.mkdir(parents=True)
        (hermes_dir / "auth.json").write_text(
            json.dumps({"providers": {"xai-oauth": {"access_token": "hermes-fallback"}}})
        )

        result = resolve_credentials(store=store)
        assert result["api_key"] == "hermes-fallback"


def test_falls_back_to_env_when_nothing_on_disk():
    with tempfile.TemporaryDirectory() as tmp:
        store = XaiAuthStore(
            sgx_base=Path(tmp) / "sgx",
            hermes_base=Path(tmp) / "hermes",
            grok_base=Path(tmp) / "grok",
        )

        result = resolve_credentials(
            store=store,
            env={"XAI_API_KEY": "env-key-123", "XAI_BASE_URL": "https://custom.x.ai"},
        )
        assert result["api_key"] == "env-key-123"
        assert result["base_url"] == "https://custom.x.ai"


def test_raises_when_no_credentials_anywhere():
    with tempfile.TemporaryDirectory() as tmp:
        store = XaiAuthStore(
            sgx_base=Path(tmp) / "sgx",
            hermes_base=Path(tmp) / "hermes",
            grok_base=Path(tmp) / "grok",
        )

        try:
            resolve_credentials(store=store, env={})
            assert False, "Should have raised"
        except RuntimeError as e:
            assert "No xAI credentials found" in str(e)


# --------------------------------------------------------------------------- #
# Tests for native store write (save) and clear — required for `sgx auth login`
# --------------------------------------------------------------------------- #


def test_save_xai_oauth_then_resolve_prefers_it():
    with tempfile.TemporaryDirectory() as tmp:
        sgx_dir = Path(tmp) / "sgx"
        hermes_dir = Path(tmp) / "hermes"
        store = XaiAuthStore(sgx_base=sgx_dir, hermes_base=hermes_dir)

        # Pre-populate a Hermes entry (should be ignored after native save)
        hermes_dir.mkdir(parents=True)
        (hermes_dir / "auth.json").write_text(
            json.dumps({"providers": {"xai-oauth": {"access_token": "hermes-should-lose"}}})
        )

        store.save_xai_oauth(
            {"access_token": "native-saved-token-xyz", "refresh_token": "rt-123"},
            base_url="https://api.x.ai/v1",
        )

        result = resolve_credentials(store=store)
        assert result["api_key"] == "native-saved-token-xyz"
        assert result["provider"] == "xai-oauth"


def test_save_xai_oauth_overwrites_previous_native_entry():
    with tempfile.TemporaryDirectory() as tmp:
        store = XaiAuthStore(sgx_base=Path(tmp) / "sgx")

        store.save_xai_oauth({"access_token": "first-token"})
        store.save_xai_oauth({"access_token": "second-token"})

        result = resolve_credentials(store=store)
        assert result["api_key"] == "second-token"


def test_clear_removes_native_entry_and_falls_back():
    with tempfile.TemporaryDirectory() as tmp:
        sgx_dir = Path(tmp) / "sgx"
        hermes_dir = Path(tmp) / "hermes"
        store = XaiAuthStore(sgx_base=sgx_dir, hermes_base=hermes_dir)

        # Native entry
        sgx_dir.mkdir(parents=True)
        (sgx_dir / "auth.json").write_text(
            json.dumps({"providers": {"xai-oauth": {"access_token": "native-to-be-cleared"}}})
        )
        # Hermes fallback
        hermes_dir.mkdir(parents=True)
        (hermes_dir / "auth.json").write_text(
            json.dumps(
                {"providers": {"xai-oauth": {"access_token": "hermes-fallback-after-clear"}}}
            )
        )

        store.clear()

        result = resolve_credentials(store=store)
        assert result["api_key"] == "hermes-fallback-after-clear"


def test_clear_on_empty_store_is_safe():
    with tempfile.TemporaryDirectory() as tmp:
        store = XaiAuthStore(sgx_base=Path(tmp) / "sgx")
        # Should not raise
        store.clear()
        # After clear, the native sgx loader itself reports nothing
        assert store.load_sgx_xai_oauth() is None


def test_loads_official_grok_cli_credentials():
    with tempfile.TemporaryDirectory() as tmp:
        grok_dir = Path(tmp) / "grok"
        store = XaiAuthStore(
            sgx_base=Path(tmp) / "sgx",
            hermes_base=Path(tmp) / "hermes",
            grok_base=grok_dir,
        )

        grok_dir.mkdir(parents=True)
        # Simulate the real shape used by the official Grok CLI
        (grok_dir / "auth.json").write_text(
            json.dumps(
                {
                    "https://auth.x.ai::b1a00492-073a-47ea-816f-4c329264a828": {
                        "key": "grok-cli-access-token-xyz",
                        "refresh_token": "grok-cli-refresh-abc",
                        "auth_mode": "oidc",
                        "email": "user@example.com",
                    }
                }
            )
        )

        result = resolve_credentials(store=store)
        assert result["api_key"] == "grok-cli-access-token-xyz"
        assert result["provider"] == "xai-oauth"
