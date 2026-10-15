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
            "source": "grok-cli",
            "api_key": "test-access-token",
            "base_url": "https://api.x.ai/v1",
            "diagnostics": ["Skipped unusable Hermes xAI OAuth credentials: missing access_token"],
        },
    )
    monkeypatch.setattr(
        cli_mod,
        "probe_connectivity",
        lambda: {"checked": True, "ok": True, "status_code": 200, "detail": "Token valid"},
    )

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "credential status" in result.output or "Credentials resolved" in result.output
    assert "grok-cli" in result.output
    assert "missing access_token" in result.output


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


# --- Batch fixes: #1 thread show, #2 slim output, #3 doctor probe,
# --- #6 JSON error contract, #7 thread-new guard, #8 papercuts


def test_version_flag():
    from sgx import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def _fake_search_result(query, **kwargs):
    return {
        "results": [{"url": "https://x.com/a/status/1", "text": "hi"}],
        "citations": [],
        "raw_text": '{"results": [{"url": "https://x.com/a/status/1", "text": "hi"}]}',
        "model": "grok-4.3",
        "query": query,
        "web": False,
    }


def test_search_json_slim_status_and_raw_opt_in(monkeypatch):
    import sgx.cli as cli_mod

    monkeypatch.setattr(cli_mod, "x_search", _fake_search_result)
    result = runner.invoke(app, ["search", "q", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "success"
    assert "raw_text" not in data  # #2: duplicative payload dropped

    result = runner.invoke(app, ["search", "q", "--json", "--raw"])
    assert json.loads(result.output)["raw_text"].startswith("{")


def test_search_runtime_and_unexpected_errors_are_json(monkeypatch):
    import sgx.cli as cli_mod

    def boom_runtime(*a, **k):
        raise RuntimeError("xAI API error 500")

    monkeypatch.setattr(cli_mod, "x_search", boom_runtime)
    result = runner.invoke(app, ["search", "q", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["status"] == "error" and data["type"] == "RuntimeError"

    def boom_unexpected(*a, **k):
        raise KeyError("wat")

    monkeypatch.setattr(cli_mod, "x_search", boom_unexpected)
    result = runner.invoke(app, ["search", "q", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.output)["status"] == "error"


def test_search_html_with_json_rejected(monkeypatch):
    import sgx.cli as cli_mod

    monkeypatch.setattr(cli_mod, "x_search", _fake_search_result)
    result = runner.invoke(app, ["search", "q", "--json", "--html"])
    assert result.exit_code == 1
    assert "cannot be combined" in json.loads(result.output)["error"]


def _fake_research_result(query, **kwargs):
    return {
        "output_text": "the answer",
        "model": "grok-4.20-multi-agent",
        "effort": "low",
        "tools_used": ["web_search"],
        "usage": {"total_tokens": 42},
        "raw_response": {"huge": True},
        "query": query,
    }


def test_research_json_slim_status_and_raw_opt_in(monkeypatch):
    import sgx.cli as cli_mod

    monkeypatch.setattr(cli_mod, "multi_agent_research", _fake_research_result)
    result = runner.invoke(app, ["research", "why?", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "success" and data["output_text"] == "the answer"
    assert "raw_response" not in data

    result = runner.invoke(app, ["research", "why?", "--json", "--raw"])
    assert json.loads(result.output)["raw_response"] == {"huge": True}


def test_research_invalid_agents_and_runtime_error_are_json(monkeypatch):
    import sgx.cli as cli_mod

    monkeypatch.setattr(cli_mod, "multi_agent_research", _fake_research_result)
    result = runner.invoke(app, ["research", "why?", "--agents", "7", "--json"])
    assert result.exit_code == 1
    assert "4 or 16" in json.loads(result.output)["error"]

    monkeypatch.setattr(
        cli_mod, "multi_agent_research", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    result = runner.invoke(app, ["research", "why?", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["type"] == "RuntimeError"


def test_doctor_json_probes_connectivity(monkeypatch):
    import sgx.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "resolve_credentials",
        lambda: {
            "provider": "xai-oauth",
            "source": "sgx",
            "api_key": "test-access-token-1234",
            "base_url": "https://api.x.ai/v1",
        },
    )
    monkeypatch.setattr(
        cli_mod,
        "probe_connectivity",
        lambda: {"checked": True, "ok": False, "status_code": 403, "detail": "rejected"},
    )
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ready"] is False  # #3: no false confidence for a dead token
    assert data["connectivity"]["status_code"] == 403

    monkeypatch.setattr(
        cli_mod,
        "probe_connectivity",
        lambda: {"checked": True, "ok": True, "status_code": 200, "detail": "ok"},
    )
    result = runner.invoke(app, ["doctor", "--json"])
    assert json.loads(result.output)["ready"] is True


def test_thread_new_duplicate_guard_and_show_stored_turns(tmp_path, monkeypatch):
    import sgx.cli as cli_mod
    from sgx.threads import ThreadStorage

    monkeypatch.setattr(cli_mod, "ThreadStorage", lambda: ThreadStorage(base_path=tmp_path))
    monkeypatch.setattr(
        cli_mod,
        "create_response",
        lambda **kw: {"id": "resp_1", "output_text": "smoke ok", "usage": {}},
    )

    assert runner.invoke(app, ["thread", "new", "demo", "--json"]).exit_code == 0

    dup = runner.invoke(app, ["thread", "new", "demo", "--json"])
    assert dup.exit_code == 1  # #7: no silent wipe
    assert "already exists" in json.loads(dup.output)["error"]

    assert runner.invoke(app, ["thread", "send", "demo", "say hi", "--json"]).exit_code == 0

    shown = runner.invoke(app, ["thread", "show", "demo", "--json"])
    assert shown.exit_code == 0
    turns = json.loads(shown.output)["turns"]
    assert turns == [{"response_id": "resp_1", "user": "say hi", "grok": "smoke ok"}]  # #1

    assert runner.invoke(app, ["thread", "new", "demo", "--force", "--json"]).exit_code == 0


def test_html_fence_stripped(tmp_path, monkeypatch):
    import sgx.cli as cli_mod

    monkeypatch.setattr(
        cli_mod,
        "create_response",
        lambda **kw: {"output_text": "```html\n<!DOCTYPE html>\n<html></html>\n```"},
    )
    path = cli_mod._generate_visual_html_explainer("content", "Title", html_dir=str(tmp_path))
    text = open(path).read()
    assert text.startswith("<!DOCTYPE html>")  # #8
    assert "```" not in text
