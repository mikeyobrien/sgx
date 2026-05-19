# ABOUTME: Tests for the core xAI Responses API client that invokes the server-side x_search tool
# ABOUTME: These tests drive the implementation that actually talks to api.x.ai using OAuth or API key

import pytest

from sgx.client import create_response, multi_agent_research, x_search


def test_x_search_raises_clear_error_on_missing_credentials():
    """When no credentials can be resolved, the client must give a helpful error."""
    with pytest.raises(RuntimeError, match="No xAI credentials"):
        x_search("some query", credentials={"provider": "xai", "api_key": "", "base_url": ""})


def test_x_search_accepts_injected_credentials_and_query():
    """Basic smoke: the function accepts the expected parameters without immediate explosion."""
    # This will fail at the network layer (no real key), but proves the shape works.
    # A future test will fully mock the HTTP response.
    with pytest.raises(RuntimeError):  # expected: will hit the real API or bad key
        x_search(
            "test query about nothing important",
            count=2,
            credentials={"provider": "xai", "api_key": "xai-fake-for-shape-test", "base_url": "https://api.x.ai/v1"},
        )


def test_multi_agent_research_validates_effort():
    """Bad effort value should fail fast with clear error."""
    with pytest.raises(ValueError, match="effort must be one of"):
        multi_agent_research(
            "test query",
            effort="insane",
            credentials={"provider": "xai", "api_key": "xai-fake", "base_url": "https://api.x.ai/v1"},
        )


def test_multi_agent_research_accepts_valid_parameters():
    """The function signature and basic validation should work."""
    # Will fail on the actual HTTP call because the key is fake, but proves wiring.
    with pytest.raises(RuntimeError):
        multi_agent_research(
            "Compare Rust and Go for systems programming",
            effort="low",
            tools=["web_search"],
            credentials={"provider": "xai", "api_key": "xai-fake", "base_url": "https://api.x.ai/v1"},
        )


def test_create_response_supports_previous_response_id():
    """The client should accept previous_response_id for stateful continuation."""
    # This will fail on network (bad key), but proves the function accepts the parameter.
    with pytest.raises(RuntimeError):
        create_response(
            input=[{"role": "user", "content": "Follow up question"}],
            model="grok-4.3",
            previous_response_id="resp_fake_12345",
            credentials={"provider": "xai", "api_key": "xai-fake", "base_url": "https://api.x.ai/v1"},
        )
