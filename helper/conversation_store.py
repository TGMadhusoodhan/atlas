"""SQLite-backed desktop conversation persistence with legacy JSON import."""

from __future__ import annotations

import datetime
import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)


class ConversationStore:
    def __init__(self, db_path: Path, legacy_dir: Path | None = None) -> None:
        self.db_path = db_path
        self.legacy_dir = legacy_dir
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._import_legacy_sessions()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS conversations (
                    session_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    messages_json TEXT NOT NULL,
                    api_messages_json TEXT NOT NULL
                )"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS conversations_updated_at "
                "ON conversations(updated_at DESC)"
            )

    @staticmethod
    def _encode_messages(value: list) -> str:
        if not isinstance(value, list):
            raise TypeError("messages must be a list")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def save(self, session_id: str, messages: list, api_messages: list | None = None,
             *, created_at: str | None = None) -> None:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session id must be a non-empty string")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        api_messages = messages if api_messages is None else api_messages
        with self._connection() as db:
            db.execute(
                """INSERT INTO conversations(
                       session_id, created_at, updated_at, messages_json, api_messages_json
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       updated_at=excluded.updated_at,
                       messages_json=excluded.messages_json,
                       api_messages_json=excluded.api_messages_json""",
                (
                    session_id,
                    created_at or now,
                    now,
                    self._encode_messages(messages),
                    self._encode_messages(api_messages),
                ),
            )

    @staticmethod
    def _decode(row: sqlite3.Row, limit: int | None = None) -> dict:
        messages = json.loads(row["messages_json"])
        api_messages = json.loads(row["api_messages_json"])
        if limit is not None:
            messages = messages[-limit:]
            api_messages = api_messages[-limit:]
        return {
            "id": row["session_id"],
            "created_at": row["created_at"],
            "messages": messages,
            "api_messages": api_messages,
        }

    def load_last(self, limit: int = 100) -> dict | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return self._decode(row, max(0, int(limit))) if row else None

    def list_sessions(self, limit: int = 50) -> list[dict]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?",
                (max(0, int(limit)),),
            ).fetchall()
        result = []
        for row in rows:
            data = self._decode(row)
            preview = next(
                (m.get("content", "")[:80] for m in data["messages"]
                 if m.get("role") == "user" and isinstance(m.get("content"), str)),
                "",
            )
            result.append({
                "id": data["id"],
                "created_at": data["created_at"],
                "preview": preview,
            })
        return result

    def clear(self) -> int:
        with self._connection() as db:
            count = db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            db.execute("DELETE FROM conversations")
        return count

    def _import_legacy_sessions(self) -> None:
        if not self.legacy_dir or not self.legacy_dir.exists():
            return
        with self._connection() as db:
            existing = db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        if existing:
            return
        imported = 0
        for path in sorted(self.legacy_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                messages = data.get("messages", [])
                self.save(
                    str(data.get("id") or path.stem),
                    messages,
                    data.get("api_messages", messages),
                    created_at=data.get("created_at"),
                )
                imported += 1
            except Exception as error:
                log.warning("Skipping malformed legacy session %s: %s", path, error)
        if imported:
            log.info("Imported %d legacy conversation session(s)", imported)
