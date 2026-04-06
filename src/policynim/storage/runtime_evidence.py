"""SQLite-backed runtime execution evidence storage."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from policynim.contracts import RuntimeEvidenceStoreProtocol
from policynim.types import RuntimeExecutionEvidenceRecord


class RuntimeEvidenceStore(RuntimeEvidenceStoreProtocol):
    """Persist immutable runtime execution evidence events in SQLite."""

    def __init__(self, *, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @property
    def path(self) -> Path:
        """Return the backing SQLite file path."""
        return self._path

    def close(self) -> None:
        """Release owned resources.

        The store opens one SQLite connection per operation, so there is no
        shared handle to close. This hook keeps tests and services symmetrical.
        """

    def reset_for_tests(self) -> None:
        """Reset the backing SQLite file and WAL sidecars for deterministic tests."""
        for candidate in (
            self._path,
            self._path.with_name(f"{self._path.name}-wal"),
            self._path.with_name(f"{self._path.name}-shm"),
        ):
            if candidate.exists():
                candidate.unlink()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def append_event(self, record: RuntimeExecutionEvidenceRecord) -> None:
        """Persist one immutable evidence event."""
        with closing(self._connect()) as conn:
            _begin_immediate(conn)
            try:
                conn.execute(
                    """
                    INSERT INTO runtime_execution_events (
                        event_id,
                        execution_id,
                        session_id,
                        created_at,
                        event_kind,
                        request_json,
                        decision,
                        summary,
                        matched_rules_json,
                        citations_json,
                        confirmation_outcome,
                        execution_outcome,
                        result_metadata_json,
                        failure_class,
                        residual_uncertainty
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.event_id,
                        record.execution_id,
                        record.session_id,
                        _iso_utc(record.created_at),
                        record.event_kind,
                        json.dumps(record.request.model_dump(mode="json"), sort_keys=True),
                        record.decision,
                        record.summary,
                        json.dumps(
                            [rule.model_dump(mode="json") for rule in record.matched_rules],
                            sort_keys=True,
                        ),
                        json.dumps(
                            [citation.model_dump(mode="json") for citation in record.citations],
                            sort_keys=True,
                        ),
                        record.confirmation_outcome,
                        record.execution_outcome,
                        (
                            json.dumps(
                                record.result_metadata.model_dump(mode="json"),
                                sort_keys=True,
                            )
                            if record.result_metadata is not None
                            else None
                        ),
                        record.failure_class,
                        record.residual_uncertainty,
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def list_session_events(self, session_id: str) -> list[RuntimeExecutionEvidenceRecord]:
        """Return persisted events for one session in append order."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    event_id,
                    execution_id,
                    session_id,
                    created_at,
                    event_kind,
                    request_json,
                    decision,
                    summary,
                    matched_rules_json,
                    citations_json,
                    confirmation_outcome,
                    execution_outcome,
                    result_metadata_json,
                    failure_class,
                    residual_uncertainty
                FROM runtime_execution_events
                WHERE session_id = ?
                ORDER BY rowid ASC
                """,
                (session_id,),
            ).fetchall()
        return [_evidence_record_from_row(row) for row in rows]

    def _initialize_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_execution_events (
                    event_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    event_kind TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    matched_rules_json TEXT NOT NULL,
                    citations_json TEXT NOT NULL,
                    confirmation_outcome TEXT NOT NULL,
                    execution_outcome TEXT,
                    result_metadata_json TEXT,
                    failure_class TEXT,
                    residual_uncertainty TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_runtime_execution_events_session
                ON runtime_execution_events(session_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_runtime_execution_events_execution
                ON runtime_execution_events(execution_id, created_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def _begin_immediate(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _evidence_record_from_row(row: sqlite3.Row) -> RuntimeExecutionEvidenceRecord:
    payload = {
        "event_id": str(row["event_id"]),
        "execution_id": str(row["execution_id"]),
        "session_id": str(row["session_id"]),
        "created_at": datetime.fromisoformat(str(row["created_at"])),
        "event_kind": str(row["event_kind"]),
        "request": json.loads(str(row["request_json"])),
        "decision": str(row["decision"]),
        "summary": str(row["summary"]),
        "matched_rules": json.loads(str(row["matched_rules_json"])),
        "citations": json.loads(str(row["citations_json"])),
        "confirmation_outcome": str(row["confirmation_outcome"]),
        "execution_outcome": row["execution_outcome"],
        "result_metadata": (
            json.loads(str(row["result_metadata_json"]))
            if row["result_metadata_json"] is not None
            else None
        ),
        "failure_class": str(row["failure_class"]) if row["failure_class"] is not None else None,
        "residual_uncertainty": (
            str(row["residual_uncertainty"]) if row["residual_uncertainty"] is not None else None
        ),
    }
    return RuntimeExecutionEvidenceRecord.model_validate(payload)
