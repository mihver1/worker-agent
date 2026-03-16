"""Session persistence — SQLite storage for conversations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from worker_ai.models import ImageAttachment, Message, Role, ToolCall, ToolResult

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    project_dir TEXT NOT NULL DEFAULT '',
    thinking_level TEXT NOT NULL DEFAULT '',
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
    attachments TEXT,          -- JSON
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
    project_dir: str = ""
    thinking_level: str = ""


class SessionStore:
    """Async SQLite store for agent sessions and messages."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.executescript(_SCHEMA)
        # Migration: add project_dir column if missing
        try:
            await self._db.execute(
                "ALTER TABLE sessions ADD COLUMN project_dir TEXT NOT NULL DEFAULT ''"
            )
            await self._db.commit()
        except Exception:  # column already exists
            pass
        try:
            await self._db.execute(
                "ALTER TABLE sessions ADD COLUMN thinking_level TEXT NOT NULL DEFAULT ''"
            )
            await self._db.commit()
        except Exception:  # column already exists
            pass
        try:
            await self._db.execute("ALTER TABLE messages ADD COLUMN attachments TEXT")
            await self._db.commit()
        except Exception:  # column already exists
            pass

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "SessionStore not opened"
        return self._db

    # ── Sessions ──────────────────────────────────────────────────

    async def create_session(
        self,
        session_id: str,
        model: str,
        title: str = "",
        project_dir: str = "",
        thinking_level: str = "",
    ) -> None:
        now = _now()
        await self.db.execute(
            (
                "INSERT INTO sessions "
                "(id, title, model, project_dir, thinking_level, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)"
            ),
            (session_id, title, model, project_dir, thinking_level, now, now),
        )
        await self.db.commit()

    async def update_session_model(self, session_id: str, model: str) -> None:
        await self.db.execute(
            "UPDATE sessions SET model = ?, updated_at = ? WHERE id = ?",
            (model, _now(), session_id),
        )
        await self.db.commit()

    async def update_session_project(self, session_id: str, project_dir: str) -> None:
        await self.db.execute(
            "UPDATE sessions SET project_dir = ?, updated_at = ? WHERE id = ?",
            (project_dir, _now(), session_id),
        )
        await self.db.commit()

    async def update_session_thinking(self, session_id: str, thinking_level: str) -> None:
        await self.db.execute(
            "UPDATE sessions SET thinking_level = ?, updated_at = ? WHERE id = ?",
            (thinking_level, _now(), session_id),
        )
        await self.db.commit()

    async def list_sessions(self, limit: int = 50) -> list[SessionInfo]:
        cursor = await self.db.execute(
            (
                "SELECT id, title, model, created_at, updated_at, project_dir, "
                "thinking_level "
                "FROM sessions ORDER BY updated_at DESC, rowid DESC LIMIT ?"
            ),
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
            json.dumps(
                [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in message.tool_calls
                ]
            )
            if message.tool_calls
            else None
        )
        tool_result_json = (
            json.dumps(
                {
                    "tool_call_id": message.tool_result.tool_call_id,
                    "content": message.tool_result.content,
                    "is_error": message.tool_result.is_error,
                    "display": message.tool_result.display,
                }
            )
            if message.tool_result
            else None
        )
        attachments_json = (
            json.dumps(
                [
                    {
                        "path": attachment.path,
                        "mime_type": attachment.mime_type,
                        "name": attachment.name,
                    }
                    for attachment in message.attachments
                ]
            )
            if message.attachments
            else None
        )

        cursor = await self.db.execute(
            (
                "INSERT INTO messages (session_id, parent_id, role, content, "
                "tool_calls, tool_result, reasoning, attachments, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            (
                session_id,
                parent_id,
                message.role.value,
                message.content,
                tool_calls_json,
                tool_result_json,
                message.reasoning,
                attachments_json,
                _now(),
            ),
        )
        await self.db.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (_now(), session_id),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def count_messages(self, session_id: str) -> int:
        cursor = await self.db.execute(
            "SELECT COUNT(*) AS count FROM messages WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    async def get_messages(self, session_id: str) -> list[Message]:
        """Load linear message history for a session (follows parent chain from latest)."""
        cursor = await self.db.execute(
            (
                "SELECT role, content, tool_calls, tool_result, reasoning, attachments "
                "FROM messages WHERE session_id = ? ORDER BY id ASC"
            ),
            (session_id,),
        )
        rows = await cursor.fetchall()
        messages: list[Message] = []
        for row in rows:
            row_dict = dict(row)
            tcs = None
            if row_dict["tool_calls"]:
                raw = json.loads(row_dict["tool_calls"])
                tcs = [
                    ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"]) for tc in raw
                ]

            tr = None
            if row_dict["tool_result"]:
                raw = json.loads(row_dict["tool_result"])
                tr = ToolResult(
                    tool_call_id=raw["tool_call_id"],
                    content=raw["content"],
                    is_error=raw.get("is_error", False),
                    display=raw.get("display"),
                )

            attachments = None
            if row_dict.get("attachments"):
                raw = json.loads(row_dict["attachments"])
                attachments = [
                    ImageAttachment(
                        path=item["path"],
                        mime_type=item.get("mime_type", "image/png"),
                        name=item.get("name", ""),
                    )
                    for item in raw
                ]

            messages.append(
                Message(
                    role=Role(row_dict["role"]),
                    content=row_dict["content"],
                    tool_calls=tcs,
                    tool_result=tr,
                    reasoning=row_dict["reasoning"],
                    attachments=attachments,
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

    async def get_session(self, session_id: str) -> SessionInfo | None:
        cursor = await self.db.execute(
            (
                "SELECT id, title, model, created_at, updated_at, project_dir, "
                "thinking_level "
                "FROM sessions WHERE id = ?"
            ),
            (session_id,),
        )
        row = await cursor.fetchone()
        return SessionInfo(**dict(row)) if row else None

    async def get_last_session(self) -> SessionInfo | None:
        """Return the most recently updated session, or None."""
        sessions = await self.list_sessions(limit=1)
        return sessions[0] if sessions else None

    async def rename_session(self, session_id: str, title: str) -> None:
        await self.db.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), session_id),
        )
        await self.db.commit()

    async def fork_session(
        self,
        source_id: str,
        new_id: str,
        title: str = "",
        up_to_message_idx: int | None = None,
    ) -> None:
        """Copy messages from source session into a new session."""
        source = await self.get_session(source_id)
        if not source:
            raise ValueError(f"Session '{source_id}' not found")

        await self.create_session(
            new_id,
            source.model,
            title=title or f"Fork of {source.title}",
            project_dir=source.project_dir,
            thinking_level=source.thinking_level,
        )

        messages = await self.get_messages(source_id)
        if up_to_message_idx is not None:
            messages = messages[: up_to_message_idx + 1]

        for msg in messages:
            await self.add_message(new_id, msg)

    async def get_message_nodes(self, session_id: str) -> list[dict]:
        """Get raw message rows with metadata for tree view."""
        cursor = await self.db.execute(
            "SELECT id, parent_id, role, content, created_at FROM messages "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
