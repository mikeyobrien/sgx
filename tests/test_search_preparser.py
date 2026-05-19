# ABOUTME: Basic tests for the -p/--prompt preparser in sgx
# ABOUTME: Covers dataclass, schema shape, and fallback behavior

import pytest

from sgx.search_preparser import SearchParameters, SEARCH_PARAMETERS_SCHEMA, parse_search_intent


def test_search_parameters_dataclass_defaults():
    p = SearchParameters(query="grok 4.3 reactions")
    assert p.query == "grok 4.3 reactions"
    assert p.count == 5
    assert p.after is None
    assert p.before is None
    assert p.web is False
    assert p.full is False


def test_search_parameters_schema_is_valid_json_schema():
    schema = SEARCH_PARAMETERS_SCHEMA
    assert schema["type"] == "object"
    assert "query" in schema["properties"]
    assert "required" in schema
    assert "query" in schema["required"]


def test_parse_search_intent_fallback_on_bad_output(monkeypatch):
    """If the LLM returns garbage, parse_search_intent should raise so CLI can fallback."""
    def fake_create_response(**kwargs):
        return {"output_text": "this is not json at all"}

    # Patch where it is actually imported inside the function
    monkeypatch.setattr("sgx.client.create_response", fake_create_response)

    with pytest.raises(RuntimeError, match="invalid JSON"):
        parse_search_intent("some random request")
