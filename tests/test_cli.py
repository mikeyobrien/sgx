# ABOUTME: Basic CLI surface tests for sgx (search + doctor commands)
# ABOUTME: Ensures the Typer app wires up correctly and produces expected exit codes / output shapes

import json

from typer.testing import CliRunner

from sgx.cli import app

runner = CliRunner()


def test_search_help_works():
    result = runner.invoke(app, ["search", "--help"])
    assert result.exit_code == 0
    assert "Search X" in result.output or "hybrid" in result.output.lower()


def test_doctor_runs_without_crashing(monkeypatch):
    import sgx.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "resolve_credentials",
        lambda: {
            "provider": "xai-oauth",
            "api_key": "test-access-token",
            "base_url": "https://api.x.ai/v1",
        },
    )

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "credential status" in result.output or "Credentials resolved" in result.output


def test_research_help_works():
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert (
        "multi-agent research" in result.output.lower() or "deep research" in result.output.lower()
    )


def test_thread_help_works():
    result = runner.invoke(app, ["thread", "--help"])
    assert result.exit_code == 0
    assert "thread" in result.output.lower() or "research thread" in result.output.lower()


def test_auth_login_help_works():
    result = runner.invoke(app, ["auth", "login", "--help"])
    assert result.exit_code == 0
    assert (
        "login" in result.output.lower()
        or "super grok" in result.output.lower()
        or "oauth" in result.output.lower()
    )


def test_search_rejects_blank_query_without_prompt():
    result = runner.invoke(app, ["search", "   ", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["status"] == "error"
    assert data["type"] == "ValueError"
    assert "Search query is required unless --prompt supplies one" in data["error"]


def test_search_rejects_blank_query_when_prompt_preparser_returns_blank(monkeypatch):
    import sgx.cli as cli_mod

    monkeypatch.setattr(
        "sgx.search_preparser.parse_search_intent",
        lambda prompt: type(
            "Parsed",
            (),
            {
                "query": "   ",
                "count": 5,
                "after": None,
                "before": None,
                "web": False,
                "full": False,
            },
        )(),
    )
    monkeypatch.setattr(cli_mod, "x_search", lambda *args, **kwargs: {"results": []})

    result = runner.invoke(app, ["search", "--prompt", "find nothing", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["status"] == "error"
    assert data["type"] == "ValueError"
    assert "Search query is required unless --prompt supplies one" in data["error"]


def test_search_accepts_valid_preparsed_prompt(monkeypatch):
    import sgx.cli as cli_mod

    seen = {}

    monkeypatch.setattr(
        "sgx.search_preparser.parse_search_intent",
        lambda prompt: type(
            "Parsed",
            (),
            {
                "query": "Hermes Agent",
                "count": 1,
                "after": "2026-01-01",
                "before": None,
                "web": False,
                "full": True,
            },
        )(),
    )

    def fake_x_search(query, **kwargs):
        seen["query"] = query
        seen.update(kwargs)
        return {"model": "fake", "results": []}

    monkeypatch.setattr(cli_mod, "x_search", fake_x_search)

    result = runner.invoke(app, ["search", "--prompt", "latest Hermes", "--count", "1", "--json"])
    assert result.exit_code == 0
    assert seen["query"] == "Hermes Agent"
    assert seen["count"] == 1
    assert seen["after"] == "2026-01-01"


def test_search_accepts_valid_positional_query(monkeypatch):
    import sgx.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "x_search",
        lambda query, **kwargs: {"model": "fake", "results": [{"url": "u", "text": query}]},
    )
    result = runner.invoke(app, ["search", "Hermes Agent", "--count", "1", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["results"][0]["text"] == "Hermes Agent"


def test_auth_login_detects_grok_cli_credentials(tmp_path, monkeypatch):
    """If ~/.grok/auth.json exists with valid tokens, login should offer to import them."""

    grok_dir = tmp_path / "grok"
    grok_dir.mkdir()
    (grok_dir / "auth.json").write_text(
        json.dumps(
            {
                "https://auth.x.ai::b1a00492-073a-47ea-816f-4c329264a828": {
                    "key": "imported-from-grok-cli-token",
                    "refresh_token": "imported-refresh",
                }
            }
        )
    )

    # Point the store at our temp grok dir
    import sgx.cli as cli_mod

    original_store = cli_mod.XaiAuthStore

    def patched_store(**kwargs):
        kwargs.setdefault("grok_base", grok_dir)
        # also isolate sgx/hermes
        kwargs.setdefault("sgx_base", tmp_path / "sgx")
        kwargs.setdefault("hermes_base", tmp_path / "hermes")
        return original_store(**kwargs)

    monkeypatch.setattr(cli_mod, "XaiAuthStore", patched_store)

    # Answer "y" to the import prompt
    result = runner.invoke(
        app,
        ["auth", "login", "--no-browser"],
        input="y\n",
    )

    # It should have taken the import path and exited cleanly
    assert result.exit_code == 0
    assert "Imported credentials from official Grok CLI" in result.output

    # Verify it actually wrote to the sgx store
    sgx_auth = tmp_path / "sgx" / "auth.json"
    assert sgx_auth.exists()
    data = json.loads(sgx_auth.read_text())
    assert data["providers"]["xai-oauth"]["access_token"] == "imported-from-grok-cli-token"


def test_auth_device_login_help_works():
    result = runner.invoke(app, ["auth", "device-login", "--help"])
    assert result.exit_code == 0
    assert "device" in result.output.lower()


def test_top_level_device_login_help_works():
    result = runner.invoke(app, ["device-login", "--help"])
    assert result.exit_code == 0
    assert "device" in result.output.lower()


def test_auth_device_login_json_streams_code_then_masks_token(monkeypatch):
    import sgx.cli as cli_mod

    def fake_device_login(**kwargs):
        kwargs["on_device_code"](
            {
                "verification_uri": "https://auth.x.ai/device",
                "verification_uri_complete": "https://auth.x.ai/device?user_code=ABCD-EFGH",
                "user_code": "ABCD-EFGH",
                "expires_in": 900,
            }
        )
        return {
            "provider": "xai-oauth",
            "api_key": "access-token-123456",
            "base_url": "https://api.x.ai/v1",
            "verification_uri": "https://auth.x.ai/device",
            "verification_uri_complete": "https://auth.x.ai/device?user_code=ABCD-EFGH",
            "user_code": "ABCD-EFGH",
        }

    monkeypatch.setattr(cli_mod, "login_xai_device_oauth", fake_device_login)

    result = runner.invoke(app, ["auth", "device-login", "--json"])

    assert result.exit_code == 0
    assert "access-token-123456" not in result.output
    records = [json.loads(line) for line in result.output.splitlines()]
    assert records[0] == {
        "status": "authorization_required",
        "verification_uri": "https://auth.x.ai/device",
        "verification_uri_complete": "https://auth.x.ai/device?user_code=ABCD-EFGH",
        "user_code": "ABCD-EFGH",
        "expires_in": 900,
    }
    assert records[1]["status"] == "success"
    assert records[1]["provider"] == "xai-oauth"
    assert records[1]["api_key_masked"] == "access-t...3456"
    assert records[1]["verification_uri"] == "https://auth.x.ai/device"
    assert records[1]["user_code"] == "ABCD-EFGH"


def test_auth_device_login_json_streams_terminal_error(monkeypatch):
    import sgx.cli as cli_mod

    def fake_device_login(**kwargs):
        kwargs["on_device_code"](
            {
                "verification_uri": "https://auth.x.ai/device",
                "verification_uri_complete": "",
                "user_code": "ABCD-EFGH",
                "expires_in": 900,
            }
        )
        raise RuntimeError("authorization expired")

    monkeypatch.setattr(cli_mod, "login_xai_device_oauth", fake_device_login)

    result = runner.invoke(app, ["auth", "device-login", "--json"])

    assert result.exit_code == 1
    records = [json.loads(line) for line in result.output.splitlines()]
    assert records[0]["status"] == "authorization_required"
    assert records[1] == {
        "status": "error",
        "error": "authorization expired",
        "type": "RuntimeError",
    }


def test_auth_device_login_human_output_shows_url_and_code(monkeypatch):
    import sgx.cli as cli_mod

    def fake_device_login(**kwargs):
        kwargs["on_device_code"](
            {
                "verification_uri": "https://auth.x.ai/device",
                "user_code": "ABCD-EFGH",
                "expires_in": 900,
            }
        )
        return {
            "provider": "xai-oauth",
            "api_key": "access-token-123456",
            "base_url": "https://api.x.ai/v1",
            "verification_uri": "https://auth.x.ai/device",
            "verification_uri_complete": "",
            "user_code": "ABCD-EFGH",
        }

    monkeypatch.setattr(cli_mod, "login_xai_device_oauth", fake_device_login)

    result = runner.invoke(app, ["auth", "device-login"])

    assert result.exit_code == 0
    assert "https://auth.x.ai/device" in result.output
    assert "ABCD-EFGH" in result.output
    assert "Device login successful" in result.output
    assert "access-token-123456" not in result.output
