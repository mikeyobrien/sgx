# ABOUTME: Tests for the sgx MCP server: config guards, tool registry, tool behavior
# ABOUTME: xAI client functions are monkeypatched — no network, no quota

import asyncio
import json

import pytest

pytest.importorskip("mcp")

import sgx.mcp_server as mcp_server
from sgx.mcp_server import McpConfig, build_server


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_http_requires_password():
    with pytest.raises(ValueError, match="SGX_MCP_PASSWORD"):
        McpConfig(transport="http").validate()


def test_http_static_token_alone_rejected():
    with pytest.raises(ValueError, match="SGX_MCP_PASSWORD"):
        McpConfig(transport="http", static_token="x").validate()


def test_http_no_auth_allowed():
    McpConfig(transport="http", no_auth=True).validate()


def test_stdio_needs_no_auth():
    McpConfig(transport="stdio").validate()


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return build_server(McpConfig(transport="stdio"))


def test_tool_registry(server):
    tools = run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "sgx_status",
        "x_search",
        "research",
        "thread_new",
        "thread_send",
        "thread_list",
        "thread_show",
    }


def _call(server, name, args):
    _content, structured = run(server.call_tool(name, args))
    return (
        structured["result"]
        if isinstance(structured, dict) and "result" in structured
        else structured
    )


def test_x_search_tool(monkeypatch, server):
    captured = {}

    def fake_x_search(query, **kw):
        captured["query"] = query
        captured.update(kw)
        return {"results": [{"url": "https://x.com/a/status/1", "text": "hi"}], "query": query}

    monkeypatch.setattr(mcp_server, "x_search", fake_x_search)
    result = _call(server, "x_search", {"query": "grok", "count": 99})
    assert result["results"][0]["text"] == "hi"
    assert captured["count"] == 20  # clamped


def test_research_tool_drops_raw_response(monkeypatch, server):
    def fake_research(question, **kw):
        assert kw["effort"] == "low"
        assert kw["tools"] == ["web_search", "x_search"]
        return {"output_text": "answer", "raw_response": {"huge": True}}

    monkeypatch.setattr(mcp_server, "multi_agent_research", fake_research)
    result = _call(server, "research", {"question": "why?"})
    assert result["output_text"] == "answer"
    assert "raw_response" not in result

    with pytest.raises(Exception):
        _call(server, "research", {"question": "why?", "agents": 8})


def test_sgx_status_without_credentials(monkeypatch, server):
    def no_creds():
        raise RuntimeError("No xAI credentials found")

    monkeypatch.setattr(mcp_server, "resolve_credentials", no_creds)
    result = _call(server, "sgx_status", {})
    assert result["ready"] is False
    assert "device-login" in result["fix"]


def test_sgx_status_masks_key(monkeypatch, server):
    monkeypatch.setattr(
        mcp_server,
        "resolve_credentials",
        lambda: {
            "provider": "xai-oauth",
            "source": "sgx",
            "base_url": "https://api.x.ai/v1",
            "api_key": "xai-1234567890abcdef",
        },
    )
    result = _call(server, "sgx_status", {})
    assert result["ready"] is True
    assert "1234567890" not in json.dumps(result)
    assert result["api_key_masked"].endswith("cdef")


def test_thread_lifecycle(monkeypatch, server):
    monkeypatch.setattr(
        mcp_server,
        "create_response",
        lambda **kw: {"id": "resp-1", "output_text": "hello back", "usage": {}},
    )
    created = _call(server, "thread_new", {"name": "demo"})
    assert created["name"] == "demo"

    sent = _call(server, "thread_send", {"name": "demo", "message": "hi"})
    assert sent["output_text"] == "hello back"

    listed = _call(server, "thread_list", {})
    assert listed["threads"][0]["message_count"] == 1

    with pytest.raises(Exception):
        _call(server, "thread_send", {"name": "missing", "message": "hi"})


def test_x_search_tool_slims_duplicate_raw_text(monkeypatch, server):
    monkeypatch.setattr(
        mcp_server,
        "x_search",
        lambda q, **kw: {
            "results": [{"url": "u", "text": "t"}],
            "raw_text": '{"results": [{"url": "u", "text": "t"}]}',
            "web": False,
            "query": q,
        },
    )
    result = _call(server, "x_search", {"query": "grok"})
    assert "raw_text" not in result


def test_thread_new_duplicate_and_show_turns(monkeypatch, server):
    monkeypatch.setattr(
        mcp_server,
        "create_response",
        lambda **kw: {"id": "resp-9", "output_text": "mcp ok", "usage": {}},
    )
    _call(server, "thread_new", {"name": "demo2"})
    with pytest.raises(Exception):  # #7: duplicate create must not wipe history
        _call(server, "thread_new", {"name": "demo2"})

    _call(server, "thread_send", {"name": "demo2", "message": "hello there"})
    shown = _call(server, "thread_show", {"name": "demo2"})
    assert shown["turns"] == [
        {"response_id": "resp-9", "user": "hello there", "grok": "mcp ok"}
    ]  # #1


def test_sgx_status_reports_unchecked_connectivity(monkeypatch, server):
    monkeypatch.setattr(
        mcp_server,
        "resolve_credentials",
        lambda: {
            "provider": "xai-oauth",
            "source": "sgx",
            "base_url": "https://api.x.ai/v1",
            "api_key": "xai-1234567890abcdef",
        },
    )
    result = _call(server, "sgx_status", {})
    assert result["connectivity_checked"] is False  # #3: resolution != acceptance
