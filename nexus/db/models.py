from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from nexus.core.protocol import PendingAction


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT,
                    trace_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS message_ledger (
                    message_id TEXT PRIMARY KEY,
                    direction TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pending_actions (
                    action_id TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    proposed_args TEXT NOT NULL,
                    status TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    spec TEXT NOT NULL,
                    next_run_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT,
                    event TEXT NOT NULL,
                    payload TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )

    def insert_message(
        self,
        message_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        role: str,
        text: str | None,
        trace_id: str,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO messages
                (id, channel, chat_id, sender_id, role, text, trace_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, channel, chat_id, sender_id, role, text, trace_id, utc_now_iso()),
            )

    def get_recent_messages(self, chat_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM messages
                WHERE chat_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (chat_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def insert_ledger(self, message_id: str, direction: str, chat_id: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO message_ledger (message_id, direction, chat_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (message_id, direction, chat_id, utc_now_iso()),
            )

    def claim_ledger(self, message_id: str, direction: str, chat_id: str) -> bool:
        """Atomically claim a message_id in the ledger.

        Returns True when this call inserted the row and therefore "owns" processing.
        Returns False when another process already claimed the same message_id.
        """
        with self._lock, self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO message_ledger (message_id, direction, chat_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (message_id, direction, chat_id, utc_now_iso()),
            )
            return bool(cursor.rowcount)

    def ledger_contains(self, message_id: str, direction: str | None = None) -> bool:
        with self._lock, self._conn() as conn:
            if direction:
                row = conn.execute(
                    "SELECT 1 FROM message_ledger WHERE message_id = ? AND direction = ?",
                    (message_id, direction),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM message_ledger WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
        return row is not None

    def insert_pending_action(self, action: PendingAction) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pending_actions
                (action_id, tool_name, risk_level, expires_at, proposed_args, status, chat_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action.action_id,
                    action.tool_name,
                    action.risk_level,
                    action.expires_at.isoformat(),
                    json.dumps(action.proposed_args),
                    action.status,
                    action.chat_id,
                    utc_now_iso(),
                ),
            )

    def get_latest_pending_action(self, chat_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM pending_actions
                WHERE chat_id = ? AND status = 'pending'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (chat_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_pending_status(self, action_id: str, status: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE pending_actions SET status = ? WHERE action_id = ?", (status, action_id))

    def upsert_job(self, job_id: str, chat_id: str, spec: dict[str, Any], next_run_at: str | None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO jobs (job_id, chat_id, spec, next_run_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, chat_id, json.dumps(spec), next_run_at, utc_now_iso()),
            )

    def list_jobs(self, chat_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            if chat_id is None:
                rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
            else:
                rows = conn.execute("SELECT * FROM jobs WHERE chat_id = ? ORDER BY created_at DESC", (chat_id,)).fetchall()
        out = []
        for row in rows:
            row_dict = dict(row)
            row_dict["spec"] = json.loads(row_dict["spec"])
            out.append(row_dict)
        return out

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        row_dict = dict(row)
        row_dict["spec"] = json.loads(row_dict["spec"])
        return row_dict

    def delete_job(self, job_id: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))

    def update_job_spec_next_run(self, job_id: str, spec: dict[str, Any], next_run_at: str | None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET spec = ?, next_run_at = ? WHERE job_id = ?",
                (json.dumps(spec), next_run_at, job_id),
            )

    def insert_audit(self, trace_id: str, event: str, payload: dict[str, Any]) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO audit_log (trace_id, event, payload, created_at) VALUES (?, ?, ?, ?)",
                (trace_id, event, json.dumps(payload), utc_now_iso()),
            )
