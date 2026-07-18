# ABOUTME: Unit tests for the pure, network-free pieces of the xAI OAuth login flow
# ABOUTME: PKCE generation, authorize URL construction, and discovery response parsing

import pytest

# These imports will fail until auth_login.py is created — expected in TDD
from sgx.auth_login import (
    XAI_DEVICE_CODE_GRANT_TYPE,
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
        "device_authorization_endpoint": "https://auth.x.ai/oauth/device/code",
        "token_endpoint": "https://auth.x.ai/oauth/token",
        "grant_types_supported": ["authorization_code", XAI_DEVICE_CODE_GRANT_TYPE],
        "issuer": "https://auth.x.ai",
    }
    httpx_mock.add_response(
        url="https://auth.x.ai/.well-known/openid-configuration",
        json=fake_payload,
        status_code=200,
    )

    endpoints = discover_xai_oauth_endpoints()
    assert endpoints["authorization_endpoint"] == "https://auth.x.ai/oauth/authorize"
    assert endpoints["device_authorization_endpoint"] == "https://auth.x.ai/oauth/device/code"
    assert endpoints["token_endpoint"] == "https://auth.x.ai/oauth/token"
    assert XAI_DEVICE_CODE_GRANT_TYPE in endpoints["grant_types_supported"]


def test_discover_xai_oauth_endpoints_rejects_untrusted_device_endpoint(httpx_mock):
    from sgx.auth_login import discover_xai_oauth_endpoints

    httpx_mock.add_response(
        url="https://auth.x.ai/.well-known/openid-configuration",
        json={
            "authorization_endpoint": "https://auth.x.ai/oauth/authorize",
            "device_authorization_endpoint": "https://attacker.example/device/code",
            "token_endpoint": "https://auth.x.ai/oauth/token",
            "grant_types_supported": [XAI_DEVICE_CODE_GRANT_TYPE],
        },
        status_code=200,
    )

    with pytest.raises(RuntimeError, match="device_authorization_endpoint"):
        discover_xai_oauth_endpoints()


def test_require_xai_device_oauth_endpoints_rejects_missing_device_endpoint():
    from sgx.auth_login import require_xai_device_oauth_endpoints

    with pytest.raises(RuntimeError, match="device_authorization_endpoint"):
        require_xai_device_oauth_endpoints(
            {
                "authorization_endpoint": "https://auth.x.ai/oauth/authorize",
                "token_endpoint": "https://auth.x.ai/oauth/token",
                "grant_types_supported": [XAI_DEVICE_CODE_GRANT_TYPE],
            }
        )


def test_require_xai_device_oauth_endpoints_rejects_untrusted_token_endpoint():
    from sgx.auth_login import require_xai_device_oauth_endpoints

    with pytest.raises(RuntimeError, match="token_endpoint"):
        require_xai_device_oauth_endpoints(
            {
                "device_authorization_endpoint": "https://auth.x.ai/oauth/device/code",
                "token_endpoint": "https://attacker.example/oauth/token",
                "grant_types_supported": [XAI_DEVICE_CODE_GRANT_TYPE],
            }
        )


def test_request_xai_device_authorization_happy_path(httpx_mock):
    from sgx.auth_login import request_xai_device_authorization

    httpx_mock.add_response(
        url="https://auth.x.ai/oauth/device/code",
        json={
            "device_code": "device-secret-code",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://auth.x.ai/device",
            "verification_uri_complete": "https://auth.x.ai/device?user_code=ABCD-EFGH",
            "expires_in": 900,
            "interval": 5,
        },
        status_code=200,
    )

    payload = request_xai_device_authorization(
        device_authorization_endpoint="https://auth.x.ai/oauth/device/code"
    )

    assert payload["device_code"] == "device-secret-code"
    assert payload["user_code"] == "ABCD-EFGH"
    assert payload["verification_uri"] == "https://auth.x.ai/device"


def test_request_xai_device_authorization_rejects_untrusted_verification_uri(httpx_mock):
    from sgx.auth_login import request_xai_device_authorization

    httpx_mock.add_response(
        url="https://auth.x.ai/oauth/device/code",
        json={
            "device_code": "device-secret-code",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://attacker.example/phish",
            "expires_in": 900,
            "interval": 5,
        },
        status_code=200,
    )

    with pytest.raises(RuntimeError, match="verification_uri"):
        request_xai_device_authorization(
            device_authorization_endpoint="https://auth.x.ai/oauth/device/code"
        )


def test_login_xai_device_oauth_callback_excludes_device_code(monkeypatch):
    import sgx.auth_login as auth_mod

    class FakeStore:
        def save_xai_oauth(self, payload, *, base_url):
            assert payload["access_token"] == "device-access"
            assert base_url == "https://api.x.ai/v1"

    monkeypatch.setattr(
        auth_mod,
        "require_xai_device_oauth_endpoints",
        lambda: {
            "device_authorization_endpoint": "https://auth.x.ai/oauth/device/code",
            "token_endpoint": "https://auth.x.ai/oauth/token",
        },
    )
    monkeypatch.setattr(
        auth_mod,
        "request_xai_device_authorization",
        lambda **_kwargs: {
            "device_code": "device-secret-code",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://auth.x.ai/device",
            "verification_uri_complete": "https://auth.x.ai/device?user_code=ABCD-EFGH",
            "expires_in": 900,
            "interval": 5,
        },
    )
    monkeypatch.setattr(
        auth_mod,
        "poll_xai_device_tokens",
        lambda **kwargs: (
            {"access_token": "device-access"}
            if kwargs["device_code"] == "device-secret-code"
            else pytest.fail("device code was not preserved internally")
        ),
    )
    monkeypatch.setattr(
        auth_mod,
        "resolve_credentials",
        lambda **_kwargs: {
            "provider": "xai-oauth",
            "api_key": "device-access",
            "base_url": "https://api.x.ai/v1",
        },
    )

    callbacks = []
    auth_mod.login_xai_device_oauth(store=FakeStore(), on_device_code=callbacks.append)

    assert callbacks == [
        {
            "verification_uri": "https://auth.x.ai/device",
            "verification_uri_complete": "https://auth.x.ai/device?user_code=ABCD-EFGH",
            "user_code": "ABCD-EFGH",
            "expires_in": 900,
            "interval": 5,
        }
    ]


def test_poll_xai_device_tokens_pending_then_success(httpx_mock):
    from sgx.auth_login import poll_xai_device_tokens

    httpx_mock.add_response(
        url="https://auth.x.ai/oauth/token",
        json={"error": "authorization_pending"},
        status_code=400,
    )
    httpx_mock.add_response(
        url="https://auth.x.ai/oauth/token",
        json={"access_token": "device-access", "refresh_token": "device-refresh"},
        status_code=200,
    )

    sleeps = []
    statuses = []
    payload = poll_xai_device_tokens(
        token_endpoint="https://auth.x.ai/oauth/token",
        device_code="device-secret-code",
        interval_seconds=5,
        expires_in=900,
        sleep_func=sleeps.append,
        on_status=lambda status, interval: statuses.append((status, interval)),
    )

    assert payload["access_token"] == "device-access"
    assert sleeps == [5]
    assert statuses == [("authorization_pending", 5.0)]


def test_poll_xai_device_tokens_slow_down_increases_interval(httpx_mock):
    from sgx.auth_login import poll_xai_device_tokens

    httpx_mock.add_response(
        url="https://auth.x.ai/oauth/token",
        json={"error": "slow_down"},
        status_code=400,
    )
    httpx_mock.add_response(
        url="https://auth.x.ai/oauth/token",
        json={"access_token": "device-access"},
        status_code=200,
    )

    sleeps = []
    statuses = []
    payload = poll_xai_device_tokens(
        token_endpoint="https://auth.x.ai/oauth/token",
        device_code="device-secret-code",
        interval_seconds=5,
        sleep_func=sleeps.append,
        on_status=lambda status, interval: statuses.append((status, interval)),
    )

    assert payload["access_token"] == "device-access"
    assert sleeps == [10]
    assert statuses == [("slow_down", 10.0)]


def test_poll_xai_device_tokens_access_denied_raises(httpx_mock):
    from sgx.auth_login import poll_xai_device_tokens

    httpx_mock.add_response(
        url="https://auth.x.ai/oauth/token",
        json={"error": "access_denied"},
        status_code=400,
    )

    with pytest.raises(RuntimeError, match="denied"):
        poll_xai_device_tokens(
            token_endpoint="https://auth.x.ai/oauth/token",
            device_code="device-secret-code",
            sleep_func=lambda _seconds: None,
        )


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
