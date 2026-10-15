# ABOUTME: Tests for the single-user OAuth provider backing the remote MCP server
# ABOUTME: Covers DCR persistence, authorize->login->code->token, refresh rotation, static token

import asyncio
import time

import pytest

pytest.importorskip("mcp")

from pydantic import AnyHttpUrl

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull

from sgx.mcp_auth import SgxOAuthProvider


def make_provider(tmp_path, **kw):
    return SgxOAuthProvider(
        password="hunter2",
        issuer_url="https://example.ts.net",
        store_path=tmp_path / "oauth.json",
        **kw,
    )


def make_client():
    return OAuthClientInformationFull(
        client_id="client-1",
        client_name="Claude",
        redirect_uris=[AnyHttpUrl("https://claude.ai/api/mcp/auth_callback")],
    )


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_password_required(tmp_path):
    with pytest.raises(ValueError):
        SgxOAuthProvider(password="", issuer_url="https://x", store_path=tmp_path / "o.json")


def test_client_registration_persists_across_instances(tmp_path):
    provider = make_provider(tmp_path)
    run(provider.register_client(make_client()))

    fresh = make_provider(tmp_path)
    loaded = run(fresh.get_client("client-1"))
    assert loaded is not None
    assert loaded.client_name == "Claude"


def _full_flow(provider):
    """Drive register -> authorize -> login callback -> code exchange. Returns OAuthToken."""
    client = make_client()
    run(provider.register_client(client))
    params = AuthorizationParams(
        state="state-123",
        scopes=["sgx"],
        code_challenge="challenge",
        redirect_uri=AnyHttpUrl("https://claude.ai/api/mcp/auth_callback"),
        redirect_uri_provided_explicitly=True,
        resource="https://example.ts.net/mcp",
    )
    login_url = run(provider.authorize(client, params))
    assert login_url == "https://example.ts.net/login?state=state-123"

    # Simulate the consent form POST.
    class FakeRequest:
        headers: dict = {}
        client = None

        async def form(self):
            return {"password": "hunter2", "state": "state-123"}

    response = run(provider.login_callback(FakeRequest()))  # type: ignore[arg-type]
    assert response.status_code == 302
    location = response.headers["location"]
    assert "code=sgx_code_" in location
    code = location.split("code=")[1].split("&")[0]

    auth_code = run(provider.load_authorization_code(client, code))
    assert auth_code is not None
    return run(provider.exchange_authorization_code(client, auth_code)), client


def test_oauth_flow_issues_working_tokens(tmp_path):
    provider = make_provider(tmp_path)
    token, _client = _full_flow(provider)

    assert token.access_token.startswith("sgx_at_")
    assert token.refresh_token and token.refresh_token.startswith("sgx_rt_")

    loaded = run(provider.load_access_token(token.access_token))
    assert loaded is not None
    assert loaded.scopes == ["sgx"]

    # Codes are single-use.
    assert run(provider.load_authorization_code(_client, "sgx_code_nope")) is None


def test_refresh_rotates_and_old_refresh_dies(tmp_path):
    provider = make_provider(tmp_path)
    token, client = _full_flow(provider)

    loaded_refresh = run(provider.load_refresh_token(client, token.refresh_token))
    assert loaded_refresh is not None

    rotated = run(provider.exchange_refresh_token(client, loaded_refresh, ["sgx"]))
    assert rotated.refresh_token != token.refresh_token
    assert run(provider.load_refresh_token(client, token.refresh_token)) is None
    assert run(provider.load_refresh_token(client, rotated.refresh_token)) is not None


def test_tokens_survive_restart(tmp_path):
    provider = make_provider(tmp_path)
    token, client = _full_flow(provider)

    fresh = make_provider(tmp_path)
    assert run(fresh.load_access_token(token.access_token)) is not None
    assert run(fresh.load_refresh_token(client, token.refresh_token)) is not None


def test_expired_access_token_rejected(tmp_path):
    provider = make_provider(tmp_path)
    token, _ = _full_flow(provider)

    # Force expiry in the provider's token store.
    import hashlib

    digest = hashlib.sha256(token.access_token.encode()).hexdigest()
    provider._data["access_tokens"][digest]["expires_at"] = int(time.time()) - 10

    assert run(provider.load_access_token(token.access_token)) is None


def test_wrong_password_rejected_and_throttled(tmp_path):
    from starlette.exceptions import HTTPException

    provider = make_provider(tmp_path)
    provider._pending["s"] = {
        "client_id": "client-1",
        "client_name": None,
        "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
        "redirect_uri_provided_explicitly": True,
        "code_challenge": "c",
        "scopes": ["sgx"],
        "resource": None,
        "expires_at": time.time() + 600,
    }

    class BadRequest:
        headers = {"x-forwarded-for": "1.2.3.4"}
        client = None

        async def form(self):
            return {"password": "wrong", "state": "s"}

    for _ in range(10):
        with pytest.raises(HTTPException) as exc:
            run(provider.login_callback(BadRequest()))  # type: ignore[arg-type]
        assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as exc:
        run(provider.login_callback(BadRequest()))  # type: ignore[arg-type]
    assert exc.value.status_code == 429


def test_static_token_accepted(tmp_path):
    provider = make_provider(tmp_path, static_token="s3cret")
    loaded = run(provider.load_access_token("s3cret"))
    assert loaded is not None and loaded.client_id == "static-token"
    assert run(provider.load_access_token("nope")) is None


def test_login_page_shows_redirect_host(tmp_path):
    provider = make_provider(tmp_path)
    client = make_client()
    run(provider.register_client(client))
    params = AuthorizationParams(
        state="st",
        scopes=["sgx"],
        code_challenge="c",
        redirect_uri=AnyHttpUrl("https://claude.ai/api/mcp/auth_callback"),
        redirect_uri_provided_explicitly=True,
    )
    run(provider.authorize(client, params))
    page = run(provider.login_page("st"))
    assert b"claude.ai" in page.body
    assert b"password" in page.body
