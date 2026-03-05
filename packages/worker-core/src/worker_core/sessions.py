"""Session persistence — SQLite storage for conversations."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from worker_ai.models import Message, Role, ToolCall, ToolResult


_SCHEMA = """\
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    parent_id   INTEGER REFERENCES messages(id),
    role        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    tool_calls  TEXT,          -- JSON
    tool_result TEXT,          -- JSON
    reasoning   TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_parent  ON messages(parent_id);
"""


@dataclass
class SessionInfo:
    id: str
    title: str
    model: str
    created_at: str
    updated_at: str


class SessionStore:
    """Async SQLite store for agent sessions and messages."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "SessionStore not opened"
        return self._db

    # ── Sessions ──────────────────────────────────────────────────

    async def create_session(self, session_id: str, model: str, title: str = "") -> None:
        now = _now()
        await self.db.execute(
            "INSERT INTO sessions (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, title, model, now, now),
        )
        await self.db.commit()

    async def list_sessions(self, limit: int = 50) -> list[SessionInfo]:
        cursor = await self.db.execute(
            "SELECT id, title, model, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [SessionInfo(**dict(r)) for r in rows]

    async def delete_session(self, session_id: str) -> None:
        await self.db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        await self.db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await self.db.commit()

    # ── Messages ──────────────────────────────────────────────────

    async def add_message(
        self,
        session_id: str,
        message: Message,
        parent_id: int | None = None,
    ) -> int:
        tool_calls_json = (
            json.dumps([{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in message.tool_calls])
            if message.tool_calls
            else None
        )
        tool_result_json = (
            json.dumps(
                {
                    "tool_call_id": message.tool_result.tool_call_id,
                    "content": message.tool_result.content,
                    "is_error": message.tool_result.is_error,
                }
            )
            if message.tool_result
            else None
        )

        cursor = await self.db.execute(
            """INSERT INTO messages (session_id, parent_id, role, content, tool_calls, tool_result, reasoning, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                parent_id,
                message.role.value,
                message.content,
                tool_calls_json,
                tool_result_json,
                message.reasoning,
                _now(),
            ),
        )
        await self.db.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (_now(), session_id),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_messages(self, session_id: str) -> list[Message]:
        """Load linear message history for a session (follows parent chain from latest)."""
        cursor = await self.db.execute(
            "SELECT role, content, tool_calls, tool_result, reasoning FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        )
        rows = await cursor.fetchall()
        messages: list[Message] = []
        for row in rows:
            row_dict = dict(row)
            tcs = None
            if row_dict["tool_calls"]:
                raw = json.loads(row_dict["tool_calls"])
                tcs = [ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"]) for tc in raw]

            tr = None
            if row_dict["tool_result"]:
                raw = json.loads(row_dict["tool_result"])
                tr = ToolResult(
                    tool_call_id=raw["tool_call_id"],
                    content=raw["content"],
                    is_error=raw.get("is_error", False),
                )

            messages.append(
                Message(
                    role=Role(row_dict["role"]),
                    content=row_dict["content"],
                    tool_calls=tcs,
                    tool_result=tr,
                    reasoning=row_dict["reasoning"],
                )
            )
        return messages

    async def compact_messages(self, session_id: str, summary: str) -> None:
        """Replace all messages with a single summary message (for context compaction)."""
        await self.db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        await self.db.execute(
            """INSERT INTO messages (session_id, parent_id, role, content, created_at)
               VALUES (?, NULL, ?, ?, ?)""",
            (session_id, Role.SYSTEM.value, f"[Compacted history]\n{summary}", _now()),
        )
        await self.db.commit()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
