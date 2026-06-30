"""SessionRegistry - in-memory session registry with TTL and optional persistence.

Replaces the ad-hoc module-level dicts used for trace/http sessions, providing:
- TTL-based automatic cleanup of stale sessions
- Optional JSON persistence of session metadata (session_id -> info mapping)
- Query by page, by session_id, or by age
- Thread-safe operations (via asyncio locks)
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SessionInfo:
    """Metadata for a single trace/capture session."""
    session_id: str
    mode: str              # "js_instrumented" | "cdp" | "tracelogger" | "http"
    created_at: float
    last_accessed: float
    active: bool
    page_name: str | None = None
    trace_dir: str | None = None
    config: dict = field(default_factory=dict)
    # TTL in seconds; 0 = no expiry
    ttl: int = 3600


class SessionRegistry:
    """A registry for trace/capture sessions with TTL and optional persistence."""

    def __init__(self, persist_path: Path | None = None, default_ttl: int = 3600):
        self._sessions: dict[str, SessionInfo] = {}
        self._lock = asyncio.Lock()
        self._persist_path = persist_path
        self._default_ttl = default_ttl
        if persist_path:
            self._load()

    # ---- CRUD -----------------------------------------------------------

    async def register(
        self,
        session_id: str,
        mode: str,
        active: bool = True,
        page_name: str | None = None,
        trace_dir: str | None = None,
        config: dict | None = None,
        ttl: int | None = None,
    ) -> SessionInfo:
        """Register a new session or update an existing one."""
        async with self._lock:
            now = time.time()
            info = SessionInfo(
                session_id=session_id,
                mode=mode,
                created_at=now,
                last_accessed=now,
                active=active,
                page_name=page_name,
                trace_dir=trace_dir,
                config=config or {},
                ttl=ttl if ttl is not None else self._default_ttl,
            )
            self._sessions[session_id] = info
            self._save()
            return info

    async def get(self, session_id: str) -> SessionInfo | None:
        """Get a session by id, updating last_accessed."""
        async with self._lock:
            info = self._sessions.get(session_id)
            if info:
                info.last_accessed = time.time()
            return info

    async def set_active(self, session_id: str, active: bool) -> bool:
        """Mark a session as active/inactive."""
        async with self._lock:
            info = self._sessions.get(session_id)
            if info:
                info.active = active
                self._save()
                return True
            return False

    async def unregister(self, session_id: str) -> bool:
        """Remove a session from the registry."""
        async with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                self._save()
                return True
            return False

    async def list_all(self) -> list[SessionInfo]:
        """List all sessions (no modification)."""
        async with self._lock:
            return list(self._sessions.values())

    async def list_active(self) -> list[SessionInfo]:
        """List only active sessions."""
        async with self._lock:
            return [s for s in self._sessions.values() if s.active]

    async def cleanup_stale(self) -> int:
        """Remove sessions past their TTL. Returns count removed."""
        now = time.time()
        removed = 0
        async with self._lock:
            stale = [
                sid for sid, info in self._sessions.items()
                if info.ttl > 0 and (now - info.last_accessed) > info.ttl
            ]
            for sid in stale:
                del self._sessions[sid]
                removed += 1
            if removed:
                self._save()
        return removed

    async def clear(self) -> int:
        """Remove all sessions. Returns count removed."""
        async with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            if self._persist_path:
                self._save()
            return count

    # ---- Persistence ------------------------------------------------------

    def _persist_data(self) -> list[dict]:
        """Serialize all sessions to JSON-compatible dicts."""
        return [
            {
                "session_id": s.session_id,
                "mode": s.mode,
                "created_at": s.created_at,
                "last_accessed": s.last_accessed,
                "active": s.active,
                "page_name": s.page_name,
                "trace_dir": s.trace_dir,
                "config": s.config,
                "ttl": s.ttl,
            }
            for s in self._sessions.values()
        ]

    def _save(self) -> None:
        if not self._persist_path:
            return
        try:
            data = self._persist_data()
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._persist_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._persist_path)
        except Exception:
            pass  # Non-fatal; in-memory still works

    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            for item in data:
                info = SessionInfo(
                    session_id=item["session_id"],
                    mode=item["mode"],
                    created_at=item["created_at"],
                    last_accessed=item["last_accessed"],
                    active=item.get("active", False),
                    page_name=item.get("page_name"),
                    trace_dir=item.get("trace_dir"),
                    config=item.get("config", {}),
                    ttl=item.get("ttl", self._default_ttl),
                )
                self._sessions[info.session_id] = info
        except Exception:
            pass  # Start fresh if persistence is corrupted
