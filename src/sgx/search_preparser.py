# ABOUTME: Preparser that turns natural language into structured SearchParameters for sgx search
# ABOUTME: Uses a lightweight LLM call with strict JSON schema to extract query, dates, count, web, full etc.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SearchParameters:
    """Structured parameters that the -p/--prompt mode extracts and feeds to x_search()."""

    query: str
    count: int = 5
    after: Optional[str] = None          # YYYY-MM-DD
    before: Optional[str] = None         # YYYY-MM-DD
    web: bool = False
    full: bool = False

    # Future expansion (kept for schema stability)
    allowed_x_handles: list[str] = field(default_factory=list)
    excluded_x_handles: list[str] = field(default_factory=list)
    enable_image_understanding: bool = False
    enable_video_understanding: bool = False


# JSON Schema used for strict structured output from the preparser LLM
SEARCH_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query"],
    "properties": {
        "query": {
            "type": "string",
            "description": "The core search topic or keywords the user wants to search for on X."
        },
        "count": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "Desired number of X posts to return (1-20)."
        },
        "after": {
            "type": ["string", "null"],
            "description": "Start date in YYYY-MM-DD format. Only include posts after (or on) this date."
        },
        "before": {
            "type": ["string", "null"],
            "description": "End date in YYYY-MM-DD format. Only include posts before (or on) this date."
        },
        "web": {
            "type": "boolean",
            "description": "Whether the user also wants general web search results in addition to X."
        },
        "full": {
            "type": "boolean",
            "description": "Whether the user wants the full original text of posts (instead of truncated)."
        },
        "allowed_x_handles": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional list of X handles to restrict results to."
        },
        "excluded_x_handles": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional list of X handles to exclude from results."
        },
        "enable_image_understanding": {
            "type": "boolean",
            "description": "Whether to enable image analysis on returned posts."
        },
        "enable_video_understanding": {
            "type": "boolean",
            "description": "Whether to enable video analysis on returned posts."
        },
    },
}


PREPARSER_SYSTEM_PROMPT = """You are an expert search intent parser for X (Twitter) and web search.

Your job is to read the user's natural language request and output a single, clean JSON object that matches the provided JSON schema exactly.

Rules:
- Always produce valid JSON. Do not add any extra text, markdown, or explanations.
- "query" should be a concise, effective search string (keywords, phrases, or topics).
- Normalize any date references ("last week", "yesterday", "March 2025", "past month") into strict YYYY-MM-DD for after/before when possible. If you cannot determine an exact date, leave after/before as null.
- Only set "web": true if the user explicitly asks for web results or general web information in addition to X posts.
- Only set "full": true if the user specifically wants the complete original post text.
- Be conservative with handle lists — only include them if the user clearly names specific accounts.
- If the request is ambiguous, make the best reasonable guess for query and leave optional fields as null/false.
- Output MUST be a single JSON object matching the schema. Nothing else.
"""


def parse_search_intent(nl_prompt: str) -> SearchParameters:
    """
    Use a structured LLM call to turn a natural language search request into SearchParameters.
    """
    from .client import create_response

    user_message = f"User request: {nl_prompt}\n\nReturn the JSON object now."

    result = create_response(
        input=[
            {"role": "system", "content": PREPARSER_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        model="grok-4.3",
        text={
            "format": {
                "type": "json_schema",
                "name": "search_parameters",
                "schema": SEARCH_PARAMETERS_SCHEMA,
                "strict": True,
            }
        },
    )

    output_text = result.get("output_text", "").strip()

    import json

    try:
        data = json.loads(output_text)
    except Exception as e:
        raise RuntimeError(f"Preparser returned invalid JSON: {output_text[:500]}") from e

    # Basic validation and construction
    if not isinstance(data, dict) or "query" not in data:
        raise RuntimeError(f"Preparser output missing required 'query' field: {data}")

    return SearchParameters(
        query=data["query"],
        count=data.get("count", 5),
        after=data.get("after"),
        before=data.get("before"),
        web=data.get("web", False),
        full=data.get("full", False),
        allowed_x_handles=data.get("allowed_x_handles", []),
        excluded_x_handles=data.get("excluded_x_handles", []),
        enable_image_understanding=data.get("enable_image_understanding", False),
        enable_video_understanding=data.get("enable_video_understanding", False),
    )

