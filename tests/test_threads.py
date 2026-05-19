# ABOUTME: Tests for the sgx persistent agent storage layer
# ABOUTME: These tests define the contract for creating, loading, and updating named agents stored as JSON files

import json
import tempfile
from pathlib import Path

import pytest

# We will import from the module we're about to build
from sgx.threads import ThreadStorage, ThreadNotFoundError


def test_create_new_agent():
    """Creating a new agent should persist basic metadata and return the agent object."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ThreadStorage(base_path=Path(tmpdir))

        thread = storage.create(name="research-thread", model="grok-4.3")

        assert thread.name == "research-thread"
        assert thread.model == "grok-4.3"
        assert len(thread.response_ids) == 0
        assert thread.created_at is not None

        # Should be persisted to disk
        thread_file = Path(tmpdir) / "threads" / "research-thread.json"
        assert thread_file.exists()

        data = json.loads(thread_file.read_text())
        assert data["name"] == "research-thread"
        assert data["model"] == "grok-4.3"


def test_create_agent_with_initial_response_id():
    """We should be able to create an agent that already has its first response ID."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ThreadStorage(base_path=Path(tmpdir))

        thread = storage.create(
            name="first-thread", model="grok-4.3", initial_response_id="resp_abc123"
        )

        assert thread.response_ids == ["resp_abc123"]


def test_load_existing_agent():
    """Loading an agent by name should return the persisted state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ThreadStorage(base_path=Path(tmpdir))
        storage.create(name="test-thread", model="grok-4.3", initial_response_id="resp_001")

        loaded = storage.load("test-thread")

        assert loaded.name == "test-thread"
        assert loaded.response_ids == ["resp_001"]


def test_load_nonexistent_agent_raises():
    """Loading an agent that doesn't exist should raise a clear error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ThreadStorage(base_path=Path(tmpdir))

        with pytest.raises(ThreadNotFoundError, match="Thread 'ghost' not found"):
            storage.load("ghost")


def test_append_response_id():
    """Appending a new response ID should persist correctly and keep order."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ThreadStorage(base_path=Path(tmpdir))
        _ = storage.create(name="thread-1", model="grok-4.3")

        storage.append_response("thread-1", "resp_002")
        storage.append_response("thread-1", "resp_003")

        reloaded = storage.load("thread-1")
        assert reloaded.response_ids == ["resp_002", "resp_003"]


def test_list_agents():
    """list() should return all created agents sorted by creation time."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ThreadStorage(base_path=Path(tmpdir))

        storage.create("alpha", model="grok-4.3")
        storage.create("beta", model="grok-4.20-multi-agent")

        names = [t.name for t in storage.list()]
        assert names == ["alpha", "beta"]


def test_agent_name_sanitization():
    """Agent names with spaces or special characters should be safely converted to filenames."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ThreadStorage(base_path=Path(tmpdir))

        _ = storage.create(name="My Research Agent!", model="grok-4.3")

        # Should still be loadable by original name
        loaded = storage.load("My Research Agent!")
        assert loaded.name == "My Research Agent!"

        # File on disk should be sanitized
        files = list((Path(tmpdir) / "threads").glob("*.json"))
        assert len(files) == 1
        assert "My_Research_Agent" in files[0].name or "my_research_agent" in files[0].name.lower()
