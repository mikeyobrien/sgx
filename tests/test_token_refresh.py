# ABOUTME: Tests for the native sgx xAI OAuth refresh-token lifecycle.
# ABOUTME: Fully isolated — temp dirs only, refresh HTTP is monkeypatched.

import json
import tempfile
import time
from pathlib import Path

import sgx.auth_login as auth_login_module
from sgx.auth import (
    SGX_TOKEN_REFRESH_SKEW_SECONDS,
    XaiAuthStore,
    _sgx_access_token_expired,
    resolve_credentials,
)


def _store(tmp: str) -> XaiAuthStore:
    return XaiAuthStore(sgx_base=Path(tmp) / "sgx")


# --- expiry predicate ------------------------------------------------------- #


def test_fresh_token_is_not_expired():
    entry = {"obtained_at": int(time.time()), "expires_in": 21600}
    assert _sgx_access_token_expired(entry) is False


def test_old_token_is_expired():
    entry = {"obtained_at": int(time.time()) - 21600 - 120, "expires_in": 21600}
    assert _sgx_access_token_expired(entry) is True


def test_token_inside_skew_window_counts_as_expired():
    # Dies in 30s, inside the 60s skew — refresh now rather than race it.
    entry = {
        "obtained_at": int(time.time()) - 21600 + 30,
        "expires_in": 21600,
    }
    assert SGX_TOKEN_REFRESH_SKEW_SECONDS == 60
    assert _sgx_access_token_expired(entry) is True


def test_missing_expiry_info_is_not_treated_as_expired():
    # No lifetime known at all: use as-is, let the 401/403 retry path recover.
    assert _sgx_access_token_expired({"access_token": "x"}) is False
    assert _sgx_access_token_expired({"obtained_at": int(time.time())}) is False


def test_lifetime_without_obtained_at_counts_as_expired():
    # Legacy entry (saved before obtained_at was stamped): refresh is cheap,
    # so assume stale rather than serve a dead token.
    assert _sgx_access_token_expired({"expires_in": 21600}) is True


def test_nonsense_expiry_values_count_as_expired():
    assert _sgx_access_token_expired({"obtained_at": 0, "expires_in": 21600}) is True
    assert _sgx_access_token_expired(
        {"obtained_at": int(time.time()), "expires_in": -5}
    ) is True


# --- save behavior ---------------------------------------------------------- #


def test_save_stamps_obtained_at_and_preserves_refresh_token():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        store.save_xai_oauth(
            {"access_token": "a1", "refresh_token": "rt-keep", "expires_in": 21600}
        )
        # Refresh response without a rotated refresh_token must not lose it.
        store.save_xai_oauth({"access_token": "a2", "expires_in": 21600})

        data = json.loads((Path(tmp) / "sgx" / "auth.json").read_text())
        entry = data["providers"]["xai-oauth"]
        assert entry["access_token"] == "a2"
        assert entry["refresh_token"] == "rt-keep"
        assert entry["expires_in"] == 21600
        assert entry["obtained_at"] <= int(time.time())
        assert entry["obtained_at"] > int(time.time()) - 60


# --- refresh paths ---------------------------------------------------------- #


def test_resolve_refreshes_expired_token(monkeypatch):
    seen = {}

    def fake_refresh(*, refresh_token, token_endpoint=None, timeout_seconds=20.0):
        seen["refresh_token"] = refresh_token
        return {
            "access_token": "fresh-access",
            "refresh_token": "fresh-refresh",
            "expires_in": 21600,
        }

    monkeypatch.setattr(auth_login_module, "refresh_xai_tokens", fake_refresh)

    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        sgx_dir = Path(tmp) / "sgx"
        sgx_dir.mkdir(parents=True)
        (sgx_dir / "auth.json").write_text(
            json.dumps(
                {
                    "providers": {
                        "xai-oauth": {
                            "access_token": "stale-access",
                            "refresh_token": "old-rt",
                            "obtained_at": int(time.time()) - 30000,
                            "expires_in": 21600,
                        }
                    }
                }
            )
        )

        result = resolve_credentials(store=store)
        assert result["api_key"] == "fresh-access"
        assert result["source"] == "sgx"
        assert seen["refresh_token"] == "old-rt"

        # Persisted for next time, with a fresh obtained_at.
        entry = json.loads((sgx_dir / "auth.json").read_text())["providers"][
            "xai-oauth"
        ]
        assert entry["access_token"] == "fresh-access"
        assert entry["refresh_token"] == "fresh-refresh"
        assert entry["obtained_at"] > int(time.time()) - 60


def test_resolve_falls_through_when_refresh_fails(monkeypatch):
    def boom(*, refresh_token, token_endpoint=None, timeout_seconds=20.0):
        raise RuntimeError("xAI token refresh failed. Response: invalid_grant")

    monkeypatch.setattr(auth_login_module, "refresh_xai_tokens", boom)

    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        sgx_dir = Path(tmp) / "sgx"
        sgx_dir.mkdir(parents=True)
        (sgx_dir / "auth.json").write_text(
            json.dumps(
                {
                    "providers": {
                        "xai-oauth": {
                            "access_token": "stale-access",
                            "refresh_token": "dead-rt",
                            "obtained_at": int(time.time()) - 30000,
                            "expires_in": 21600,
                        }
                    }
                }
            )
        )

        # No Hermes/env fallback configured either → clear "no credentials" error.
        try:
            resolve_credentials(store=store, env={})
        except RuntimeError as exc:
            assert "No xAI credentials found" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_force_refresh_returns_false_without_refresh_token():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        store.save_xai_oauth({"access_token": "only-access"})
        assert store.refresh_sgx_access_token() is False


def test_fresh_token_does_not_trigger_refresh(monkeypatch):
    def boom(*, refresh_token, token_endpoint=None, timeout_seconds=20.0):
        raise AssertionError("refresh must not be called for a fresh token")

    monkeypatch.setattr(auth_login_module, "refresh_xai_tokens", boom)

    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        store.save_xai_oauth(
            {
                "access_token": "still-good",
                "refresh_token": "rt",
                "expires_in": 21600,
            }
        )
        result = resolve_credentials(store=store)
        assert result["api_key"] == "still-good"
