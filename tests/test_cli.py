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


def test_doctor_runs_without_crashing():
    # Will use real credentials on this machine
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
