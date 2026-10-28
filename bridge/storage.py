"""SQLite persistence for command idempotency and safe execution metadata."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import ExecutionStatus, utc_iso


class Storage:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self._memory = str(database_path) == ":memory:"
        self._dsn = (
            f"file:bloombergbridge_{uuid.uuid4().hex}?mode=memory&cache=shared"
            if self._memory
            else str(self.database_path)
        )
        self._keeper = (
            sqlite3.connect(self._dsn, uri=True, timeout=15) if self._memory else None
        )
        if not self._memory:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._dsn, uri=self._memory, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS commands (
                    issue_number INTEGER PRIMARY KEY,
                    command_id TEXT,
                    sender TEXT NOT NULL,
                    command TEXT NOT NULL,
                    status TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    response_sent INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS ux_commands_command_id
                    ON commands(command_id) WHERE command_id IS NOT NULL;

                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    engine_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error_code TEXT,
                    outcome_code TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_executions_job_status
                    ON executions(job_id, status);

                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS remote_jobs (
                    request_id TEXT PRIMARY KEY,
                    issue_number INTEGER NOT NULL UNIQUE,
                    envelope_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    job_id TEXT,
                    outcome_code TEXT,
                    error_code TEXT,
                    received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            execution_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(executions)").fetchall()
            }
            if "outcome_code" not in execution_columns:
                connection.execute("ALTER TABLE executions ADD COLUMN outcome_code TEXT")
            connection.execute(
                """UPDATE remote_jobs SET status='FAILED', error_code='PROCESS_INTERRUPTED',
                   updated_at=? WHERE status IN ('QUEUED', 'RUNNING')""",
                (utc_iso(),),
            )

    def claim_remote_job(self, *, request_id: str, issue_number: int, envelope_digest: str) -> bool:
        now = utc_iso()
        try:
            with self._connection() as connection:
                connection.execute(
                    """INSERT INTO remote_jobs
                       (request_id, issue_number, envelope_digest, status, received_at, updated_at)
                       VALUES (?, ?, ?, 'QUEUED', ?, ?)""",
                    (request_id, issue_number, envelope_digest, now, now),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def update_remote_job(self, request_id: str, *, status: str, job_id: str | None = None,
                          outcome_code: str | None = None, error_code: str | None = None) -> None:
        with self._connection() as connection:
            connection.execute(
                """UPDATE remote_jobs SET status=?, job_id=COALESCE(?, job_id),
                   outcome_code=?, error_code=?, updated_at=? WHERE request_id=?""",
                (status, job_id, outcome_code, error_code, utc_iso(), request_id),
            )

    def get_remote_job(self, request_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM remote_jobs WHERE request_id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def get_remote_job_by_issue(self, issue_number: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM remote_jobs WHERE issue_number=?", (issue_number,)).fetchone()
        return dict(row) if row else None

    def get_command_by_issue(self, issue_number: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM commands WHERE issue_number = ?", (issue_number,)
            ).fetchone()
        return dict(row) if row else None

    def get_command_by_id(self, command_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM commands WHERE command_id = ?", (command_id,)
            ).fetchone()
        return dict(row) if row else None

    def save_command(
        self,
        *,
        issue_number: int,
        command_id: str | None,
        sender: str,
        command: str,
        status: str,
        response: dict[str, Any],
        error_code: str | None = None,
    ) -> bool:
        now = utc_iso()
        try:
            with self._connection() as connection:
                connection.execute(
                    """INSERT INTO commands
                       (issue_number, command_id, sender, command, status,
                        received_at, updated_at, response_json, error_code)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        issue_number,
                        command_id,
                        sender,
                        command,
                        status,
                        now,
                        now,
                        json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                        error_code,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def update_command_response(
        self,
        issue_number: int,
        *,
        status: str,
        response: dict[str, Any],
        error_code: str | None = None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """UPDATE commands SET status = ?, updated_at = ?, response_json = ?,
                   error_code = ? WHERE issue_number = ?""",
                (
                    status,
                    utc_iso(),
                    json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                    error_code,
                    issue_number,
                ),
            )

    def mark_response_sent(self, issue_number: int) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE commands SET response_sent = 1, updated_at = ? WHERE issue_number = ?",
                (utc_iso(), issue_number),
            )

    def list_pending_responses(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT issue_number, response_json FROM commands
                   WHERE response_sent = 0 AND status != 'PROCESSING'
                   ORDER BY issue_number"""
            ).fetchall()
        return [dict(row) for row in rows]

    def response_for_issue(self, issue_number: int) -> dict[str, Any] | None:
        row = self.get_command_by_issue(issue_number)
        if not row:
            return None
        return json.loads(row["response_json"])

    def create_execution(
        self,
        *,
        execution_id: str,
        job_id: str,
        engine_id: str,
    ) -> bool:
        now = utc_iso()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT 1 FROM executions WHERE status = ? LIMIT 1",
                (ExecutionStatus.RUNNING.value,),
            ).fetchone()
            if active:
                return False
            connection.execute(
                """INSERT INTO executions
                   (execution_id, job_id, engine_id, status, started_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (execution_id, job_id, engine_id, ExecutionStatus.RUNNING.value, now),
            )
        return True

    def finish_execution(
        self,
        execution_id: str,
        *,
        status: ExecutionStatus,
        error_code: str | None = None,
        outcome_code: str | None = None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """UPDATE executions SET status = ?, finished_at = ?, error_code = ?,
                   outcome_code = ?
                   WHERE execution_id = ?""",
                (status.value, utc_iso(), error_code, outcome_code, execution_id),
            )

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT execution_id, engine_id, job_id, status AS execution_status,
                          started_at, finished_at, error_code, outcome_code
                   FROM executions WHERE execution_id = ?""",
                (execution_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_running_execution(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT execution_id, engine_id, job_id, status AS execution_status,
                          started_at, finished_at, error_code, outcome_code
                   FROM executions WHERE job_id = ? AND status = ? LIMIT 1""",
                (job_id, ExecutionStatus.RUNNING.value),
            ).fetchone()
        return dict(row) if row else None

    def recover_interrupted(self) -> int:
        """Mark in-flight work interrupted; never resubmit it after restart."""
        with self._connection() as connection:
            cursor = connection.execute(
                """UPDATE executions SET status = ?, finished_at = ?, error_code = ?
                   WHERE status = ?""",
                (
                    ExecutionStatus.INTERRUPTED.value,
                    utc_iso(),
                    "PROCESS_RESTARTED",
                    ExecutionStatus.RUNNING.value,
                ),
            )
            execution_count = cursor.rowcount
            processing = connection.execute(
                "SELECT issue_number, command_id FROM commands WHERE status = 'PROCESSING'"
            ).fetchall()
            for row in processing:
                response = {
                    "contract_version": 1,
                    "command_id": row["command_id"],
                    "status": "FAILED",
                    "error_code": "PROCESS_INTERRUPTED",
                    "handled_at": utc_iso(),
                    "result": None,
                }
                connection.execute(
                    """UPDATE commands SET status = 'FAILED', updated_at = ?,
                       response_json = ?, error_code = 'PROCESS_INTERRUPTED'
                       WHERE issue_number = ?""",
                    (
                        utc_iso(),
                        json.dumps(response, separators=(",", ":")),
                        row["issue_number"],
                    ),
                )
        return execution_count

    def get_metadata(self, key: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_metadata(self, key: str, value: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO metadata(key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, value),
            )
