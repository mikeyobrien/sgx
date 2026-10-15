# ABOUTME: Persistent agent storage and management for sgx
# ABOUTME: Handles creation, loading, and updating of named stateful agents using local JSON files

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


@dataclass
class Thread:
    name: str
    model: str
    response_ids: List[str] = field(default_factory=list)
    created_at: str = ""
    last_used: str = ""
    # Local transcript: [{response_id, user, grok}]. The xAI retrieve endpoint
    # does not return the original user input, so turns are recorded at send
    # time; legacy threads (written before turns existed) simply have [].
    turns: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "model": self.model,
            "response_ids": self.response_ids,
            "created_at": self.created_at,
            "last_used": self.last_used,
            "turns": self.turns,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Thread":
        return cls(
            name=data["name"],
            model=data["model"],
            response_ids=data.get("response_ids", []),
            created_at=data.get("created_at", ""),
            last_used=data.get("last_used", ""),
            turns=data.get("turns", []),
        )


class ThreadNotFoundError(Exception):
    pass


class ThreadExistsError(Exception):
    pass


class ThreadStorage:
    """Manages persistent research threads stored as JSON files under a base directory."""

    def __init__(self, base_path: Optional[Path] = None):
        if base_path is None:
            base_path = Path.home() / ".sgx"
        self.base_path = base_path
        self.threads_dir = self.base_path / "threads"
        self.threads_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_name(self, name: str) -> str:
        """Convert thread name to a safe filename."""
        name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
        name = re.sub(r"_+", "_", name).strip("_")
        return name.lower() or "unnamed"

    def _get_thread_path(self, name: str) -> Path:
        safe_name = self._sanitize_name(name)
        return self.threads_dir / f"{safe_name}.json"

    def exists(self, name: str) -> bool:
        return self._get_thread_path(name).exists()

    def create(
        self,
        name: str,
        model: str,
        initial_response_id: Optional[str] = None,
        exist_ok: bool = False,
    ) -> Thread:
        """Create a new persistent research thread.

        Raises ThreadExistsError if a thread with this (sanitized) name
        already exists, unless exist_ok=True — recreating a thread discards
        its response history, so it must never happen silently.
        """
        if not exist_ok and self.exists(name):
            raise ThreadExistsError(f"Thread '{name}' already exists")
        now = datetime.now(timezone.utc).isoformat()

        response_ids = [initial_response_id] if initial_response_id else []

        thread = Thread(
            name=name,
            model=model,
            response_ids=response_ids,
            created_at=now,
            last_used=now,
        )

        self._save_thread(thread)
        return thread

    def load(self, name: str) -> Thread:
        """Load an existing thread by its original name."""
        path = self._get_thread_path(name)

        if not path.exists():
            raise ThreadNotFoundError(f"Thread '{name}' not found")

        data = json.loads(path.read_text())
        return Thread.from_dict(data)

    def append_response(
        self,
        name: str,
        response_id: str,
        user: Optional[str] = None,
        grok: Optional[str] = None,
    ) -> Thread:
        """Append a new response ID (and, when known, the turn text) to a thread."""
        thread = self.load(name)
        thread.response_ids.append(response_id)
        if user is not None or grok is not None:
            thread.turns.append(
                {"response_id": response_id, "user": user or "", "grok": grok or ""}
            )
        thread.last_used = datetime.now(timezone.utc).isoformat()

        self._save_thread(thread)
        return thread

    def list(self) -> List[Thread]:
        """Return all threads, sorted by creation time (oldest first)."""
        threads: List[Thread] = []

        for file in sorted(self.threads_dir.glob("*.json")):
            try:
                data = json.loads(file.read_text())
                threads.append(Thread.from_dict(data))
            except Exception:
                continue  # Skip corrupted files

        # Sort by created_at
        threads.sort(key=lambda t: t.created_at or "")
        return threads

    def _save_thread(self, thread: Thread) -> None:
        path = self._get_thread_path(thread.name)
        path.write_text(json.dumps(thread.to_dict(), indent=2))


def thread_turns(thread: Thread, limit: int, retrieve=None) -> List[dict]:
    """Build [{response_id, user, grok}] for the last `limit` turns of a thread.

    Locally stored turns (recorded at send time) are authoritative. For legacy
    response IDs with no stored turn, the response is fetched via `retrieve`
    (default: sgx.client.retrieve_response) and the assistant text is
    extracted from the real Responses API shape; user text is unavailable
    from that endpoint and stays "". Retrieval failures omit that turn's
    text (empty strings) rather than aborting the whole listing.
    """
    if limit < 1:
        return []
    stored = {t.get("response_id"): t for t in thread.turns}
    turns: List[dict] = []
    for rid in thread.response_ids[-limit:]:
        local = stored.get(rid)
        if local is not None:
            turns.append(
                {
                    "response_id": rid,
                    "user": str(local.get("user", "")),
                    "grok": str(local.get("grok", "")),
                }
            )
            continue
        grok = ""
        try:
            if retrieve is None:
                from .client import retrieve_response as retrieve
            from .client import extract_output_text

            grok = extract_output_text(retrieve(rid))
        except Exception:
            grok = ""
        turns.append({"response_id": rid, "user": "", "grok": grok})
    return turns
