# ABOUTME: Unit tests for the pure, network-free pieces of the xAI OAuth login flow
# ABOUTME: PKCE generation, authorize URL construction, and discovery response parsing

import pytest

# These imports will fail until auth_login.py is created — expected in TDD
from sgx.auth_login import (
    build_xai_authorize_url,
    generate_pkce,
)


def test_generate_pkce_produces_valid_s256_pair():
    verifier, challenge = generate_pkce()

    # verifier must be 43..128 chars after b64 (Hermes uses 64 bytes raw → ~86 chars)
    assert 43 <= len(verifier) <= 128
    # challenge is base64url of sha256, no padding
    assert 43 <= len(challenge) <= 128
    # Both must be url-safe (no + / =)
    assert all(c not in verifier for c in "+/=")
    assert all(c not in challenge for c in "+/=")


def test_build_xai_authorize_url_contains_required_params_and_plan_generic():
    verifier, challenge = generate_pkce()
    state = "test-state-123"
    nonce = "test-nonce-456"
    redirect_uri = "http://127.0.0.1:56121/callback"

    url = build_xai_authorize_url(
        authorization_endpoint="https://auth.x.ai/oauth/authorize",
        redirect_uri=redirect_uri,
        code_challenge=challenge,
        state=state,
        nonce=nonce,
    )

    assert url.startswith("https://auth.x.ai/oauth/authorize?")
    assert "response_type=code" in url
    assert "client_id=b1a00492-073a-47ea-816f-4c329264a828" in url
    assert f"redirect_uri={redirect_uri}" in url or "redirect_uri=" in url
    assert "code_challenge_method=S256" in url
    assert f"code_challenge={challenge}" in url
    assert f"state={state}" in url
    assert f"nonce={nonce}" in url
    # These two are critical for xAI to accept loopback from third-party clients
    assert "plan=generic" in url
    assert "referrer=" in url


def test_build_xai_authorize_url_is_idempotent_for_same_inputs():
    _, challenge = generate_pkce()
    url1 = build_xai_authorize_url(
        authorization_endpoint="https://auth.x.ai/oauth/authorize",
        redirect_uri="http://127.0.0.1:12345/cb",
        code_challenge=challenge,
        state="s",
        nonce="n",
    )
    url2 = build_xai_authorize_url(
        authorization_endpoint="https://auth.x.ai/oauth/authorize",
        redirect_uri="http://127.0.0.1:12345/cb",
        code_challenge=challenge,
        state="s",
        nonce="n",
    )
    assert url1 == url2


# --------------------------------------------------------------------------- #
# Tests that exercise network helpers via pytest-httpx (no real network)
# --------------------------------------------------------------------------- #


def test_discover_xai_oauth_endpoints_happy_path(httpx_mock):
    from sgx.auth_login import discover_xai_oauth_endpoints

    fake_payload = {
        "authorization_endpoint": "https://auth.x.ai/oauth/authorize",
        "token_endpoint": "https://auth.x.ai/oauth/token",
        "issuer": "https://auth.x.ai",
    }
    httpx_mock.add_response(
        url="https://auth.x.ai/.well-known/openid-configuration",
        json=fake_payload,
        status_code=200,
    )

    endpoints = discover_xai_oauth_endpoints()
    assert endpoints["authorization_endpoint"] == "https://auth.x.ai/oauth/authorize"
    assert endpoints["token_endpoint"] == "https://auth.x.ai/oauth/token"


def test_exchange_code_for_tokens_happy_path(httpx_mock):
    from sgx.auth_login import exchange_code_for_tokens

    fake_token_response = {
        "access_token": "at-abc123",
        "refresh_token": "rt-def456",
        "id_token": "id-xyz",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    httpx_mock.add_response(
        url="https://auth.x.ai/oauth/token",
        json=fake_token_response,
        status_code=200,
    )

    payload = exchange_code_for_tokens(
        code="the-code",
        code_verifier="the-verifier",
        redirect_uri="http://127.0.0.1:12345/callback",
        token_endpoint="https://auth.x.ai/oauth/token",
    )
    assert payload["access_token"] == "at-abc123"
    assert payload["refresh_token"] == "rt-def456"


def test_exchange_code_for_tokens_missing_refresh_raises(httpx_mock):
    from sgx.auth_login import exchange_code_for_tokens

    httpx_mock.add_response(
        url="https://auth.x.ai/oauth/token",
        json={"access_token": "only-access-no-refresh"},
        status_code=200,
    )

    with pytest.raises(RuntimeError, match="refresh_token"):
        exchange_code_for_tokens(
            code="c",
            code_verifier="v",
            redirect_uri="http://127.0.0.1:1/cb",
            token_endpoint="https://auth.x.ai/oauth/token",
        )


def test_callback_server_receives_code_and_state():
    """Integration-style test: start real server on random port and drive it."""
    from sgx.auth_login import start_xai_callback_server, wait_for_xai_callback

    server, thread, result, redirect_uri = start_xai_callback_server(preferred_port=0)

    # Simulate what the browser / xAI would do
    import urllib.request

    try:
        # Fire a request containing the expected params
        test_url = f"{redirect_uri}?code=the-real-auth-code&state=the-state-xyz"
        urllib.request.urlopen(test_url, timeout=2).read()
    finally:
        # The wait will also shut it down
        captured = wait_for_xai_callback(server, thread, result, timeout_seconds=5.0)

    assert captured["code"] == "the-real-auth-code"
    assert captured["state"] == "the-state-xyz"
    assert captured["error"] is None
