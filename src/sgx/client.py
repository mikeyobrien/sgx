# ABOUTME: Low-level client for xAI Responses API — specifically the privileged server-side x_search tool
# ABOUTME: Replicates the proven pattern from rho/extensions/x-search while staying small and testable

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from .auth import get_xai_auth

DEFAULT_MODEL = "grok-4.3"
MULTI_AGENT_MODEL = "grok-4.20-multi-agent"
USER_AGENT = "sgx/0.1.0 (Hermes-Compatible; +https://github.com/mikeyobrien/sgx)"


def _extract_output_text(data: dict) -> str:
    """Unified extraction of output_text from xAI Responses API shape.
    Handles both direct output_text and the output[] array form.
    """
    output_text = data.get("output_text") or ""
    if not output_text and isinstance(data.get("output"), list):
        for item in data["output"]:
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    output_text += part.get("text", "")
    return output_text.strip()


def _get_x_search_schema(count: int) -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["results"],
        "properties": {
            "results": {
                "type": "array",
                "maxItems": max(1, min(count, 20)),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["url", "text"],
                    "properties": {
                        "url": {"type": "string", "description": "Full X status URL"},
                        "text": {"type": "string", "description": "Post text (full when --full, otherwise may be truncated to ~500 chars)"},
                        "author": {
                            "type": "string",
                            "description": "Author handle or display name",
                        },
                        "created_at": {
                            "type": "string",
                            "description": "ISO timestamp if available",
                        },
                    },
                },
            }
        },
    }


def x_search(
    query: str,
    *,
    count: int = 5,
    model: Optional[str] = None,
    web: bool = False,
    full: bool = False,
    credentials: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Perform a search using xAI's server-side tools via the Responses API.

    By default uses only the privileged `x_search` tool (X/Twitter).
    When `web=True`, also enables general `web_search` for hybrid results.

    Returns a dict with keys:
        results: list of {url, text, author?, created_at?} (when only X)
        raw_text: the model's raw output (for debugging)
        model: the model that was used
        query: original query
        web: whether web search was enabled
        full: whether full post text was requested
    """
    api_key, base_url = get_xai_auth(credentials)
    chosen_model = model or DEFAULT_MODEL

    tools = [{"type": "x_search"}]
    if web:
        tools.append({"type": "web_search"})

    payload: Dict[str, Any] = {
        "model": chosen_model,
        "input": [
            {
                "role": "system",
                "content": (
                    "You are a search assistant. Use the built-in x_search tool (and web_search if available) "
                    "to find the most relevant and recent information. "
                    "Prefer high-quality primary sources and recent posts."
                ),
            },
            {
                "role": "user",
                "content": f"Research: {query}",
            },
        ],
        "tools": tools,
    }

    # Only force strict X-only JSON schema when doing pure X search
    if not web:
        payload["input"][0]["content"] = (
            "You are a search assistant. Use the built-in x_search tool to find real X posts. "
            "Return ONLY JSON that matches the provided JSON schema. Do not include markdown or extra keys."
        )
        text_rule = (
            "- Return the full original text of each post (no length limit).\n"
            if full
            else "- Keep post text to a reasonable length (up to ~500 characters). "
            "Truncate longer posts with '...' if needed.\n"
        )
        payload["input"][1]["content"] = (
            f"Find up to {max(1, min(count, 20))} recent and relevant X posts for the query: {json.dumps(query)}.\n\n"
            "Rules:\n"
            "- Only include direct X status URLs (https://x.com/<user>/status/<id>).\n"
            "- De-duplicate near-identical posts.\n"
            f"{text_rule}"
            "- If a field is unknown, omit it (do not fabricate)."
        )
        payload["text"] = {
            "format": {
                "type": "json_schema",
                "name": "x_search_results",
                "schema": _get_x_search_schema(count),
                "strict": True,
            }
        }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": USER_AGENT,
    }

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        resp = client.post(f"{base_url}/responses", headers=headers, json=payload)

        if resp.status_code == 400 and "only the grok-4 family" in resp.text.lower():
            raise RuntimeError(
                f"xAI requires a Grok 4 family model for x_search. "
                f"Current model: {chosen_model}. Set XAI_X_SEARCH_MODEL=grok-4.3 or similar."
            )

        if not resp.is_success:
            raise RuntimeError(f"xAI API error {resp.status_code}: {resp.text[:2000]}")

        data = resp.json()

    # Extract output text (Responses API shape)
    raw_text = ""
    if isinstance(data.get("output_text"), str):
        raw_text = data["output_text"]
    elif isinstance(data.get("output"), list):
        for item in data["output"]:
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    raw_text += part.get("text", "")

    raw_text = raw_text.strip()

    # Try to parse the JSON the model was forced to emit
    results: List[Dict[str, Any]] = []
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict) and isinstance(parsed.get("results"), list):
            results = parsed["results"]
    except Exception:
        # The model sometimes wraps in ```json fences even with strict mode
        import re

        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_text, re.I)
        if m:
            try:
                parsed = json.loads(m.group(1))
                results = parsed.get("results", []) if isinstance(parsed, dict) else []
            except Exception:
                pass

    citations = data.get("citations") or []
    if not isinstance(citations, list):
        citations = []

    return {
        "results": results,
        "citations": citations,
        "raw_text": raw_text,
        "model": chosen_model,
        "query": query,
        "web": web,
    }


# =============================================================================
# Multi-Agent Research (grok-4.20-multi-agent)
# =============================================================================

VALID_EFFORTS = {"low", "medium", "high", "xhigh"}

BUILTIN_TOOLS = {
    "web_search",
    "x_search",
    "code_execution",
    "collections_search",
}


def multi_agent_research(
    query: str,
    *,
    effort: str = "low",
    tools: Optional[List[str]] = None,
    credentials: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Run a deep research query using xAI's multi-agent model.

    This spins up multiple specialized agents (controlled by `effort`) that
    collaborate and use server-side tools to produce a high-quality synthesized answer.

    Args:
        query: The research question.
        effort: One of "low", "medium", "high", "xhigh".
                low/medium ≈ 4 agents, high/xhigh ≈ 16 agents.
        tools: List of built-in tools to enable, e.g. ["web_search", "x_search"].
               Pass empty list or None to disable tools.
        credentials: Optional override for auth (mainly for testing).

    Returns:
        Dict containing:
            - output_text: The leader agent's final response
            - model: The model used
            - usage: Rich usage object from xAI (tokens + tool calls)
            - raw_response: The full API response (for advanced use / debugging)
            - query, effort, tools_used
    """
    if effort not in VALID_EFFORTS:
        raise ValueError(f"effort must be one of {VALID_EFFORTS}, got {effort!r}")

    api_key, base_url = get_xai_auth(credentials)

    # Build tools list
    enabled_tools: List[Dict[str, str]] = []
    if tools is None:
        # Sensible default for research
        enabled_tools = [{"type": "web_search"}, {"type": "x_search"}]
    else:
        for t in tools:
            if t in BUILTIN_TOOLS:
                enabled_tools.append({"type": t})
            else:
                raise ValueError(f"Unknown tool '{t}'. Supported: {sorted(BUILTIN_TOOLS)}")

    payload: Dict[str, Any] = {
        "model": MULTI_AGENT_MODEL,
        "input": [
            {
                "role": "user",
                "content": query,
            }
        ],
        "reasoning": {"effort": effort},
    }

    if enabled_tools:
        payload["tools"] = enabled_tools

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": USER_AGENT,
    }

    with httpx.Client(timeout=300.0) as client:  # Multi-agent can take time
        resp = client.post(f"{base_url}/responses", headers=headers, json=payload)

        if not resp.is_success:
            # Try to surface useful error info
            try:
                err = resp.json()
            except Exception:
                err = resp.text[:2000]
            raise RuntimeError(f"xAI API error {resp.status_code}: {err}")

        data = resp.json()

    # Extract the leader's final output text (multi-agent returns this differently than regular models)
    output_text = _extract_output_text(data)

    return {
        "output_text": output_text,
        "model": MULTI_AGENT_MODEL,
        "effort": effort,
        "tools_used": [t["type"] for t in enabled_tools],
        "usage": data.get("usage", {}),
        "raw_response": data,
        "query": query,
    }


def create_response(
    *,
    input: list,
    model: str,
    previous_response_id: Optional[str] = None,
    tools: Optional[list] = None,
    reasoning: Optional[dict] = None,
    credentials: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Low-level helper to create (or continue) a response using xAI's Responses API.

    Supports `previous_response_id` for stateful/persistent conversations (used by threads).

    Returns a dict with:
        - output_text
        - id (the new response ID)
        - usage
        - raw_response
        - model
    """
    api_key, base_url = get_xai_auth(credentials)

    payload: Dict[str, Any] = {
        "model": model,
        "input": input,
    }

    if previous_response_id:
        payload["previous_response_id"] = previous_response_id

    if tools:
        payload["tools"] = [{"type": t} if isinstance(t, str) else t for t in tools]

    if reasoning:
        payload["reasoning"] = reasoning

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": USER_AGENT,
    }

    with httpx.Client(timeout=300.0) as client:
        resp = client.post(f"{base_url}/responses", headers=headers, json=payload)

        if not resp.is_success:
            try:
                err = resp.json()
            except Exception:
                err = resp.text[:2000]
            raise RuntimeError(f"xAI API error {resp.status_code}: {err}")

        data = resp.json()

    # Extract output text
    output_text = _extract_output_text(data)

    return {
        "id": data.get("id"),
        "output_text": output_text,
        "model": model,
        "usage": data.get("usage", {}),
        "raw_response": data,
    }


def retrieve_response(
    response_id: str,
    credentials: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Retrieve a previously stored response by its ID.
    Useful for reconstructing conversation history in threads.
    """
    api_key, base_url = get_xai_auth(credentials)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": USER_AGENT,
    }

    with httpx.Client(timeout=60.0) as client:
        resp = client.get(f"{base_url}/responses/{response_id}", headers=headers)

        if not resp.is_success:
            try:
                err = resp.json()
            except Exception:
                err = resp.text[:2000]
            raise RuntimeError(f"xAI API error {resp.status_code} retrieving {response_id}: {err}")

        return resp.json()
