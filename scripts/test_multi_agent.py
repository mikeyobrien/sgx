#!/usr/bin/env python3
# ABOUTME: Quick test harness for xAI's grok-4.20-multi-agent model using sgx credential resolution
# ABOUTME: Lets us experiment with the multi-agent research capability using the same OAuth/API key path as the rest of sgx

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

# Add src to path so we can import sgx modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sgx.auth import resolve_credentials


def run_multi_agent_research(
    query: str,
    effort: str = "low",  # low/medium = ~4 agents, high/xhigh = ~16 agents
    include_tools: bool = True,
) -> dict:
    creds = resolve_credentials()
    api_key = creds["api_key"]
    base_url = creds["base_url"].rstrip("/")

    tools = []
    if include_tools:
        tools = [
            {"type": "web_search"},
            {"type": "x_search"},
            # code_execution is also available per docs
        ]

    payload = {
        "model": "grok-4.20-multi-agent",
        "input": [
            {
                "role": "user",
                "content": query,
            }
        ],
        "reasoning": {"effort": effort},
    }

    if tools:
        payload["tools"] = tools

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "sgx-multi-agent-test/0.1 (Hermes-Compatible)",
    }

    print(f"→ Using provider: {creds['provider']}")
    print(f"→ Model: grok-4.20-multi-agent (effort={effort})")
    print(f"→ Tools enabled: {bool(tools)}")
    print(f"→ Query: {query[:80]}{'...' if len(query) > 80 else ''}")
    print("→ Calling xAI Responses API (this may take a while with multiple agents)...\n")

    with httpx.Client(timeout=180.0) as client:
        resp = client.post(f"{base_url}/responses", headers=headers, json=payload)

    if not resp.is_success:
        print(f"ERROR {resp.status_code}:")
        print(resp.text[:2000])
        resp.raise_for_status()

    data = resp.json()
    return data


def pretty_print_response(data: dict):
    # Try to extract the final leader output
    output_text = data.get("output_text", "")

    if not output_text and "output" in data:
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    output_text += content.get("text", "")

    print("=" * 80)
    print("MULTI-AGENT LEADER RESPONSE")
    print("=" * 80)
    print(output_text or "(no output_text found in response)")
    print()

    # Show usage if present
    if "usage" in data:
        usage = data["usage"]
        print("Usage:")
        print(json.dumps(usage, indent=2))

    # Show any tool calls the leader made
    if "output" in data:
        for item in data["output"]:
            for content in item.get("content", []):
                if content.get("type") == "tool_call":
                    print("\nLeader tool call:")
                    print(json.dumps(content, indent=2))


if __name__ == "__main__":
    # Good test query for multi-agent (complex, benefits from multiple perspectives + tools)
    test_query = (
        "Compare the major approaches to building long-running personal AI agents in 2026. "
        "Focus on memory architectures, tool use patterns, multi-agent coordination, "
        "credential and auth management, and reliability for always-on personal use. "
        "Include concrete examples from real projects where possible."
    )

    # Start with low effort for faster/cheaper first test
    result = run_multi_agent_research(test_query, effort="low", include_tools=True)
    pretty_print_response(result)
