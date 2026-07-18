# ABOUTME: Tests for sgx auth resolution and storage (fully isolated)
# ABOUTME: Uses temporary directories so tests never touch real ~/.sgx or ~/.hermes

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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


# --------------------------------------------------------------------------- #
# Tests for Hermes xAI runtime resolver integration (hermetic — no real I/O)  #
# --------------------------------------------------------------------------- #


def _fake_hermes_auth(api_key="hermes-resolved-token-abc", base_url=None):
    def resolver(*, refresh_if_expiring=False, force_refresh=False, refresh_skew_seconds=None):
        assert refresh_if_expiring is True
        return {"api_key": api_key, "base_url": base_url}

    return SimpleNamespace(resolve_xai_oauth_runtime_credentials=resolver)


def _allow_runtime_resolver_for_test(monkeypatch):
    monkeypatch.setattr("sgx.auth._is_default_hermes_auth_path", lambda path: True)


def test_resolve_hermes_xai_uses_installed_hermes_without_sibling_checkout(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials

    _allow_runtime_resolver_for_test(monkeypatch)
    fake_hermes_auth = tmp_path / "hermes" / "auth.json"
    fake_hermes_auth.parent.mkdir(parents=True)

    def fake_import(name):
        assert name == "hermes_cli.auth"
        return _fake_hermes_auth(api_key="installed-token")

    monkeypatch.setattr("sgx.auth.import_module", fake_import)

    result = _resolve_hermes_xai_runtime_credentials(fake_hermes_auth)
    assert result is not None
    assert result["api_key"] == "installed-token"


def test_resolve_hermes_xai_uses_sibling_checkout_when_installed_missing(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials

    _allow_runtime_resolver_for_test(monkeypatch)
    hermes_base = tmp_path / "hermes"
    (hermes_base / "hermes-agent").mkdir(parents=True)
    fake_hermes_auth = hermes_base / "auth.json"

    calls = []

    def fake_import(name):
        calls.append(name)
        if len(calls) == 1:
            raise ImportError("installed package missing")
        return _fake_hermes_auth(api_key="checkout-token")

    monkeypatch.setattr("sgx.auth.import_module", fake_import)

    result = _resolve_hermes_xai_runtime_credentials(fake_hermes_auth)
    assert result is not None
    assert result["api_key"] == "checkout-token"
    assert calls == ["hermes_cli.auth", "hermes_cli.auth"]


def test_resolve_hermes_xai_returns_none_when_import_and_checkout_missing(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials

    _allow_runtime_resolver_for_test(monkeypatch)
    fake_hermes_auth = tmp_path / "hermes" / "auth.json"
    fake_hermes_auth.parent.mkdir(parents=True)
    monkeypatch.setattr(
        "sgx.auth.import_module",
        lambda name: (_ for _ in ()).throw(ImportError("no hermes_cli")),
    )

    result = _resolve_hermes_xai_runtime_credentials(fake_hermes_auth)
    assert result is None


def test_custom_hermes_store_does_not_call_runtime_resolver(monkeypatch, tmp_path):
    hermes_dir = tmp_path / "hermes"
    hermes_dir.mkdir(parents=True)
    (hermes_dir / "auth.json").write_text(
        json.dumps({"providers": {"xai-oauth": {"tokens": {"access_token": "isolated-token"}}}})
    )

    def fail_import(name):
        raise AssertionError("custom test stores must not import active Hermes runtime")

    monkeypatch.setattr("sgx.auth.import_module", fail_import)

    store = XaiAuthStore(sgx_base=tmp_path / "sgx", hermes_base=hermes_dir)
    result = resolve_credentials(store=store)
    assert result["api_key"] == "isolated-token"


def test_resolve_hermes_xai_returns_none_when_module_missing_attr(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials

    _allow_runtime_resolver_for_test(monkeypatch)
    fake_hermes_auth = tmp_path / "hermes" / "auth.json"
    fake_hermes_auth.parent.mkdir(parents=True)

    monkeypatch.setattr("sgx.auth.import_module", lambda name: SimpleNamespace())

    result = _resolve_hermes_xai_runtime_credentials(fake_hermes_auth)
    assert result is None


def test_resolve_hermes_xai_surfaces_empty_resolver_result(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials

    _allow_runtime_resolver_for_test(monkeypatch)
    fake_hermes_auth = tmp_path / "hermes" / "auth.json"
    fake_hermes_auth.parent.mkdir(parents=True)

    monkeypatch.setattr(
        "sgx.auth.import_module",
        lambda name: SimpleNamespace(resolve_xai_oauth_runtime_credentials=lambda **kwargs: {}),
    )

    try:
        _resolve_hermes_xai_runtime_credentials(fake_hermes_auth)
        assert False, "resolver success without api_key should remain visible"
    except RuntimeError as exc:
        assert "missing api_key" in str(exc)


def _hermes_auth_error_type(code: str):
    return type(
        "AuthError",
        (RuntimeError,),
        {"code": code, "provider": "xai-oauth", "__module__": "hermes_cli.auth"},
    )


def test_resolve_hermes_xai_missing_grant_falls_through(monkeypatch):
    from sgx.auth import DEFAULT_HERMES_AUTH_PATH, _resolve_hermes_xai_runtime_credentials

    AuthError = _hermes_auth_error_type("xai_auth_missing")

    def fail(**kwargs):
        raise AuthError("No xAI OAuth credentials stored")

    monkeypatch.setattr(
        "sgx.auth.import_module",
        lambda name: SimpleNamespace(
            resolve_xai_oauth_runtime_credentials=fail, AuthError=AuthError
        ),
    )

    result = _resolve_hermes_xai_runtime_credentials(DEFAULT_HERMES_AUTH_PATH)
    assert result is None


def test_hermes_missing_access_token_falls_back_to_grok_cli(monkeypatch, tmp_path):
    AuthError = _hermes_auth_error_type("xai_auth_missing_access_token")

    def fail(**kwargs):
        raise AuthError("Hermes token missing access_token for opaque-secret-value-123456")

    monkeypatch.setattr("sgx.auth._is_default_hermes_auth_path", lambda path: True)
    monkeypatch.setattr(
        "sgx.auth.import_module",
        lambda name: SimpleNamespace(
            resolve_xai_oauth_runtime_credentials=fail, AuthError=AuthError
        ),
    )
    grok_dir = tmp_path / "grok"
    grok_dir.mkdir(parents=True)
    (grok_dir / "auth.json").write_text(
        json.dumps({"https://auth.x.ai::client": {"key": "grok-fallback-access-token"}})
    )

    store = XaiAuthStore(
        sgx_base=tmp_path / "sgx", hermes_base=tmp_path / "hermes", grok_base=grok_dir
    )
    result = resolve_credentials(store=store, env={})

    assert result["api_key"] == "grok-fallback-access-token"
    assert result["provider"] == "xai-oauth"
    assert result["source"] == "grok-cli"
    assert store.diagnostics
    diagnostic_text = "\n".join(store.diagnostics)
    assert "xai_auth_missing_access_token" in diagnostic_text
    assert "opaque-secret-value-123456" not in diagnostic_text


def test_hermes_missing_refresh_token_falls_back_to_env(monkeypatch, tmp_path):
    AuthError = _hermes_auth_error_type("xai_auth_missing_refresh_token")

    def fail(**kwargs):
        raise AuthError("Hermes token missing refresh_token")

    monkeypatch.setattr("sgx.auth._is_default_hermes_auth_path", lambda path: True)
    monkeypatch.setattr(
        "sgx.auth.import_module",
        lambda name: SimpleNamespace(
            resolve_xai_oauth_runtime_credentials=fail, AuthError=AuthError
        ),
    )
    store = XaiAuthStore(
        sgx_base=tmp_path / "sgx", hermes_base=tmp_path / "hermes", grok_base=tmp_path / "grok"
    )

    result = resolve_credentials(store=store, env={"XAI_API_KEY": "env-fallback-token"})

    assert result["api_key"] == "env-fallback-token"
    assert result["provider"] == "xai"
    assert result["source"] == "env"


def test_hermes_unusable_runtime_error_skips_raw_hermes_token(monkeypatch, tmp_path):
    AuthError = _hermes_auth_error_type("xai_auth_missing_refresh_token")

    def fail(**kwargs):
        raise AuthError("missing refresh_token")

    monkeypatch.setattr("sgx.auth._is_default_hermes_auth_path", lambda path: True)
    monkeypatch.setattr(
        "sgx.auth.import_module",
        lambda name: SimpleNamespace(
            resolve_xai_oauth_runtime_credentials=fail, AuthError=AuthError
        ),
    )
    hermes_dir = tmp_path / "hermes"
    hermes_dir.mkdir(parents=True)
    (hermes_dir / "auth.json").write_text(
        json.dumps({"providers": {"xai-oauth": {"access_token": "raw-hermes-must-skip"}}})
    )
    store = XaiAuthStore(
        sgx_base=tmp_path / "sgx", hermes_base=hermes_dir, grok_base=tmp_path / "grok"
    )

    result = resolve_credentials(store=store, env={"XAI_API_KEY": "env-after-unusable-hermes"})

    assert result["api_key"] == "env-after-unusable-hermes"
    assert result["source"] == "env"


def test_hermes_missing_access_token_without_lower_source_reports_useful_error(
    monkeypatch, tmp_path
):
    AuthError = _hermes_auth_error_type("xai_auth_missing_access_token")

    def fail(**kwargs):
        raise AuthError("missing access_token")

    monkeypatch.setattr("sgx.auth._is_default_hermes_auth_path", lambda path: True)
    monkeypatch.setattr(
        "sgx.auth.import_module",
        lambda name: SimpleNamespace(
            resolve_xai_oauth_runtime_credentials=fail, AuthError=AuthError
        ),
    )
    store = XaiAuthStore(
        sgx_base=tmp_path / "sgx", hermes_base=tmp_path / "hermes", grok_base=tmp_path / "grok"
    )

    try:
        resolve_credentials(store=store, env={})
        assert False, "missing Hermes token with no fallback should raise final credential error"
    except RuntimeError as exc:
        message = str(exc)
        assert "No xAI credentials found" in message
        assert "Skipped unusable Hermes xAI OAuth credentials" in message
        assert "xai_auth_missing_access_token" in message


def test_non_hermes_missing_code_does_not_fall_through(monkeypatch):
    from sgx.auth import DEFAULT_HERMES_AUTH_PATH, _resolve_hermes_xai_runtime_credentials

    FakeMissing = type(
        "AuthError",
        (RuntimeError,),
        {"code": "xai_auth_missing", "provider": "xai-oauth", "__module__": "not_hermes"},
    )

    def fail(**kwargs):
        raise FakeMissing("resolver wrapper failed with xai_auth_missing")

    monkeypatch.setattr(
        "sgx.auth.import_module",
        lambda name: SimpleNamespace(resolve_xai_oauth_runtime_credentials=fail),
    )

    try:
        _resolve_hermes_xai_runtime_credentials(DEFAULT_HERMES_AUTH_PATH)
        assert False, "only Hermes AuthError xai_auth_missing should fall through"
    except RuntimeError as exc:
        assert "Hermes xAI OAuth resolver failed" in str(exc)


def test_resolve_hermes_xai_checkout_subprocess_uses_venv_python(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials_from_checkout

    hermes_base = tmp_path / "hermes"
    python_bin = hermes_base / "hermes-agent" / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/usr/bin/env python\n")

    completed = Mock(
        returncode=0,
        stdout=json.dumps({"ok": True, "api_key": "checkout-runtime-token", "base_url": None}),
    )
    run_mock = Mock(return_value=completed)
    monkeypatch.setattr("sgx.auth.subprocess.run", run_mock)

    result = _resolve_hermes_xai_runtime_credentials_from_checkout(hermes_base / "auth.json")

    assert result is not None
    assert result["api_key"] == "checkout-runtime-token"
    assert result["base_url"] == "https://api.x.ai/v1"
    assert run_mock.call_args.args[0][:2] == [str(python_bin), "-c"]


def test_resolve_hermes_xai_checkout_success_without_api_key_is_visible(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials_from_checkout

    hermes_base = tmp_path / "hermes"
    python_bin = hermes_base / "hermes-agent" / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/usr/bin/env python\n")
    monkeypatch.setattr(
        "sgx.auth.subprocess.run",
        Mock(return_value=Mock(returncode=0, stdout=json.dumps({"ok": True, "base_url": None}))),
    )

    try:
        _resolve_hermes_xai_runtime_credentials_from_checkout(hermes_base / "auth.json")
        assert False, "checkout resolver success without api_key should remain visible"
    except RuntimeError as exc:
        assert "missing api_key" in str(exc)


def test_resolve_hermes_xai_checkout_subprocess_failure_is_visible_and_redacted(
    monkeypatch, tmp_path
):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials_from_checkout

    hermes_base = tmp_path / "hermes"
    python_bin = hermes_base / "hermes-agent" / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/usr/bin/env python\n")
    leaked = "abcdefghijklmnopqrstuvwxyzabcdef"
    monkeypatch.setattr(
        "sgx.auth.subprocess.run",
        Mock(return_value=Mock(returncode=1, stdout="", stderr=f"failed for {leaked}")),
    )

    try:
        _resolve_hermes_xai_runtime_credentials_from_checkout(hermes_base / "auth.json")
        assert False, "checkout runtime failure should remain visible"
    except RuntimeError as exc:
        message = str(exc)
        assert "Hermes xAI OAuth resolver failed" in message
        assert leaked not in message
        assert "[redacted]" in message


def test_resolve_hermes_xai_checkout_malformed_json_is_visible(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials_from_checkout

    hermes_base = tmp_path / "hermes"
    python_bin = hermes_base / "hermes-agent" / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/usr/bin/env python\n")
    monkeypatch.setattr(
        "sgx.auth.subprocess.run", Mock(return_value=Mock(returncode=0, stdout="not json"))
    )

    try:
        _resolve_hermes_xai_runtime_credentials_from_checkout(hermes_base / "auth.json")
        assert False, "malformed checkout resolver output should remain visible"
    except RuntimeError as exc:
        assert "malformed JSON" in str(exc)


def test_resolve_hermes_xai_checkout_autherror_missing_falls_through(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials_from_checkout

    hermes_base = tmp_path / "hermes"
    python_bin = hermes_base / "hermes-agent" / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/usr/bin/env python\n")
    payload = {
        "ok": False,
        "type": "AuthError",
        "code": "xai_auth_missing",
        "provider": "xai-oauth",
        "module": "hermes_cli.auth",
        "message": "No xAI OAuth credentials stored",
    }
    monkeypatch.setattr(
        "sgx.auth.subprocess.run",
        Mock(return_value=Mock(returncode=0, stdout=json.dumps(payload))),
    )

    assert _resolve_hermes_xai_runtime_credentials_from_checkout(hermes_base / "auth.json") is None


def test_resolve_hermes_xai_checkout_non_hermes_autherror_is_visible(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials_from_checkout

    hermes_base = tmp_path / "hermes"
    python_bin = hermes_base / "hermes-agent" / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/usr/bin/env python\n")
    payload = {
        "ok": False,
        "type": "AuthError",
        "code": "xai_auth_missing",
        "provider": "xai-oauth",
        "module": "not_hermes",
        "message": "wrapper failed with xai_auth_missing",
    }
    monkeypatch.setattr(
        "sgx.auth.subprocess.run",
        Mock(return_value=Mock(returncode=0, stdout=json.dumps(payload))),
    )

    try:
        _resolve_hermes_xai_runtime_credentials_from_checkout(hermes_base / "auth.json")
        assert False, "non-Hermes AuthError-shaped payload should remain visible"
    except RuntimeError as exc:
        assert "Hermes xAI OAuth resolver failed" in str(exc)


def test_resolve_hermes_xai_strips_trailing_slash_from_base_url(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials

    _allow_runtime_resolver_for_test(monkeypatch)
    fake_hermes_auth = tmp_path / "hermes" / "auth.json"
    fake_hermes_auth.parent.mkdir(parents=True)
    monkeypatch.setattr(
        "sgx.auth.import_module",
        lambda name: _fake_hermes_auth(api_key="token", base_url="https://custom.x.ai/v1///"),
    )

    result = _resolve_hermes_xai_runtime_credentials(fake_hermes_auth)
    assert result is not None
    assert result["base_url"] == "https://custom.x.ai/v1"


def test_resolve_hermes_xai_uses_default_base_url_when_none(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials

    _allow_runtime_resolver_for_test(monkeypatch)
    fake_hermes_auth = tmp_path / "hermes" / "auth.json"
    fake_hermes_auth.parent.mkdir(parents=True)
    monkeypatch.setattr(
        "sgx.auth.import_module",
        lambda name: _fake_hermes_auth(api_key="token-only", base_url=None),
    )

    result = _resolve_hermes_xai_runtime_credentials(fake_hermes_auth)
    assert result is not None
    assert result["base_url"] == "https://api.x.ai/v1"


def test_resolve_hermes_xai_prefers_hermes_resolver_over_nested_raw_json(monkeypatch, tmp_path):
    _allow_runtime_resolver_for_test(monkeypatch)
    hermes_dir = tmp_path / "hermes"
    hermes_dir.mkdir(parents=True)
    (hermes_dir / "auth.json").write_text(
        json.dumps({"providers": {"xai-oauth": {"tokens": {"access_token": "raw-json-token"}}}})
    )
    monkeypatch.setattr(
        "sgx.auth.import_module", lambda name: _fake_hermes_auth(api_key="runtime-token")
    )

    store = XaiAuthStore(sgx_base=tmp_path / "sgx", hermes_base=hermes_dir)
    result = resolve_credentials(store=store)
    assert result["api_key"] == "runtime-token"


def test_load_hermes_reads_current_nested_token_shape_when_resolver_unavailable(
    monkeypatch, tmp_path
):
    hermes_dir = tmp_path / "hermes"
    hermes_dir.mkdir(parents=True)
    (hermes_dir / "auth.json").write_text(
        json.dumps(
            {
                "providers": {
                    "xai-oauth": {
                        "tokens": {"access_token": "nested-token"},
                        "base_url": "https://api.x.ai/v1",
                    }
                }
            }
        )
    )
    monkeypatch.setattr(
        "sgx.auth.import_module",
        lambda name: (_ for _ in ()).throw(ImportError("not installed")),
    )

    store = XaiAuthStore(sgx_base=tmp_path / "sgx", hermes_base=hermes_dir)
    result = resolve_credentials(store=store)
    assert result["api_key"] == "nested-token"


def test_load_hermes_retains_legacy_flat_token_fallback(monkeypatch, tmp_path):
    hermes_dir = tmp_path / "hermes"
    hermes_dir.mkdir(parents=True)
    (hermes_dir / "auth.json").write_text(
        json.dumps({"providers": {"xai-oauth": {"access_token": "legacy-token"}}})
    )
    monkeypatch.setattr(
        "sgx.auth.import_module",
        lambda name: (_ for _ in ()).throw(ImportError("not installed")),
    )

    store = XaiAuthStore(sgx_base=tmp_path / "sgx", hermes_base=hermes_dir)
    result = resolve_credentials(store=store)
    assert result["api_key"] == "legacy-token"


def test_default_hermes_malformed_state_falls_back_with_redacted_diagnostic(monkeypatch, tmp_path):
    hermes_dir = tmp_path / "hermes"
    hermes_dir.mkdir(parents=True)
    secret_like = "abcdefghijklmnopqrstuvwxyzabcdef"
    (hermes_dir / "auth.json").write_text(
        json.dumps({"providers": {"xai-oauth": {"tokens": {"token_type": secret_like}}}})
    )
    monkeypatch.setattr("sgx.auth._is_default_hermes_auth_path", lambda path: True)
    monkeypatch.setattr("sgx.auth._resolve_hermes_xai_runtime_credentials", lambda path: None)

    store = XaiAuthStore(
        sgx_base=tmp_path / "sgx", hermes_base=hermes_dir, grok_base=tmp_path / "grok"
    )
    result = resolve_credentials(store=store, env={"XAI_API_KEY": "env-token-should-work"})

    assert result["api_key"] == "env-token-should-work"
    assert result["provider"] == "xai"
    assert result["source"] == "env"
    diagnostic_text = "\n".join(result.get("diagnostics", []))
    assert "Skipped unusable Hermes xAI OAuth credentials" in diagnostic_text
    assert secret_like not in diagnostic_text


def test_missing_default_hermes_xai_provider_falls_through(monkeypatch, tmp_path):
    hermes_dir = tmp_path / "hermes"
    hermes_dir.mkdir(parents=True)
    (hermes_dir / "auth.json").write_text(json.dumps({"providers": {}}))
    monkeypatch.setattr("sgx.auth._is_default_hermes_auth_path", lambda path: True)
    monkeypatch.setattr("sgx.auth._resolve_hermes_xai_runtime_credentials", lambda path: None)

    store = XaiAuthStore(
        sgx_base=tmp_path / "sgx", hermes_base=hermes_dir, grok_base=tmp_path / "grok"
    )
    result = resolve_credentials(store=store, env={"XAI_API_KEY": "env-token"})
    assert result["api_key"] == "env-token"


def test_resolver_failure_is_visible_and_redacts_token_text(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials

    _allow_runtime_resolver_for_test(monkeypatch)
    leaked_token = "tok_1234567890abcdef1234567890abcdef"
    leaked_digitless = "abcdefghijklmnopqrstuvwxyzabcdef"
    leaked_xai_prefixed = "xai_auth_abcdefghijklmnopqrstuvwxyz"
    fake_hermes_auth = tmp_path / "hermes" / "auth.json"
    fake_hermes_auth.parent.mkdir(parents=True)

    class ResolverFailure(RuntimeError):
        code = "xai_auth_missing_access_token"
        provider = "xai-oauth"

    def fail(**kwargs):
        raise ResolverFailure(
            f"refresh failed for {leaked_token} and {leaked_digitless} and {leaked_xai_prefixed}"
        )

    monkeypatch.setattr(
        "sgx.auth.import_module",
        lambda name: SimpleNamespace(resolve_xai_oauth_runtime_credentials=fail),
    )

    try:
        _resolve_hermes_xai_runtime_credentials(fake_hermes_auth)
        assert False, "resolver failure should remain visible"
    except RuntimeError as exc:
        message = str(exc)
        assert "Hermes xAI OAuth resolver failed" in message
        assert "xai_auth_missing_access_token" in message
        assert leaked_token not in message
        assert leaked_digitless not in message
        assert leaked_xai_prefixed not in message
        assert "[redacted]" in message
        assert exc.__cause__ is None


def test_resolve_hermes_xai_does_not_leak_sys_path_on_success(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials

    _allow_runtime_resolver_for_test(monkeypatch)
    hermes_base = tmp_path / "hermes"
    hermes_root = hermes_base / "hermes-agent"
    hermes_root.mkdir(parents=True)
    fake_hermes_auth = hermes_base / "auth.json"
    hermes_root_str = str(hermes_root)

    def fake_import(name):
        if hermes_root_str not in sys.path:
            raise ImportError("installed missing")
        return _fake_hermes_auth(api_key="token")

    monkeypatch.setattr("sgx.auth.import_module", fake_import)

    before = list(sys.path)
    _resolve_hermes_xai_runtime_credentials(fake_hermes_auth)
    after = list(sys.path)

    assert before == after


def test_resolve_hermes_xai_does_not_leak_sys_path_on_failure(monkeypatch, tmp_path):
    from sgx.auth import _resolve_hermes_xai_runtime_credentials

    _allow_runtime_resolver_for_test(monkeypatch)
    hermes_base = tmp_path / "hermes"
    hermes_root = hermes_base / "hermes-agent"
    hermes_root.mkdir(parents=True)
    fake_hermes_auth = hermes_base / "auth.json"
    hermes_root_str = str(hermes_root)

    def fake_import(name):
        if hermes_root_str not in sys.path:
            raise ImportError("installed missing")
        raise ImportError("checkout import failed")

    monkeypatch.setattr("sgx.auth.import_module", fake_import)

    before = list(sys.path)
    result = _resolve_hermes_xai_runtime_credentials(fake_hermes_auth)
    after = list(sys.path)

    assert result is None
    assert before == after
