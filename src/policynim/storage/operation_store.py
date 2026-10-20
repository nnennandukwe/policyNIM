"""Atomic accepted-operation accounting for one hosted SQLite deployment."""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from policynim.storage.operation_lease import OperationOwner

OperationOutcome = Literal["completed", "failed", "cancelled", "interrupted"]


class OperationAdmission(BaseModel):
    """Describe an admission decision without exposing account or credential data."""

    model_config = ConfigDict(frozen=True)
    operation_id: str | None = None
    rejection: (
        Literal["principal_busy", "server_busy", "quota_exceeded", "invalid_token"] | None
    ) = None


class OperationUsage(BaseModel):
    """Separate accepted work and terminal outcomes from unknown provider usage."""

    model_config = ConfigDict(frozen=True)
    accepted: int = 0
    active: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    interrupted: int = 0
    provider_usage: None = None


class OperationStore:
    """Serialize authority checks, active limits and daily charges in one transaction."""

    def __init__(self, path: Path) -> None:
        """Create only operation tables, leaving legacy account tables unchanged."""
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._owner = OperationOwner(path)
        with closing(self._connect()) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS operation_usage (
                    operation_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK(outcome IN
                        ('accepted','completed','failed','cancelled','interrupted')),
                    process_id INTEGER NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    owner_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS operation_usage_principal_day
                    ON operation_usage(principal_id, usage_date);
                CREATE INDEX IF NOT EXISTS operation_usage_active
                    ON operation_usage(outcome);
            """)

    def _connect(self) -> sqlite3.Connection:
        """Open a bounded-wait connection with explicit transaction ownership."""
        return sqlite3.connect(self.path, timeout=0.1, isolation_level=None)

    def admit(
        self,
        *,
        principal_id: str,
        operation: str,
        daily_limit: int,
        principal_limit: int,
        global_limit: int,
        beta_account_id: int | None = None,
        credential_id: str | None = None,
        now: float | None = None,
    ) -> OperationAdmission:
        """Commit exactly one charge only when authority and all limits permit it."""
        timestamp = time.time() if now is None else now
        usage_date = datetime.fromtimestamp(timestamp, UTC).date().isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if beta_account_id is not None:
                authorized = connection.execute(
                    "SELECT 1 FROM accounts a JOIN api_keys k ON k.account_id = a.id "
                    "WHERE a.id = ? AND a.status = 'active' "
                    "AND k.key_hash = ? AND k.revoked_at IS NULL",
                    (beta_account_id, credential_id),
                ).fetchone()
                if authorized is None:
                    return OperationAdmission(rejection="invalid_token")
            active = connection.execute(
                "SELECT COUNT(*) FROM operation_usage WHERE outcome = 'accepted'"
            ).fetchone()[0]
            if active >= global_limit:
                return OperationAdmission(rejection="server_busy")
            principal_active = connection.execute(
                "SELECT COUNT(*) FROM operation_usage "
                "WHERE principal_id = ? AND outcome = 'accepted'",
                (principal_id,),
            ).fetchone()[0]
            if principal_active >= principal_limit:
                return OperationAdmission(rejection="principal_busy")
            accepted = connection.execute(
                "SELECT COUNT(*) FROM operation_usage WHERE principal_id = ? AND usage_date = ?",
                (principal_id, usage_date),
            ).fetchone()[0]
            if accepted >= daily_limit:
                return OperationAdmission(rejection="quota_exceeded")
            operation_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO operation_usage VALUES (?, ?, ?, ?, 'accepted', ?, ?, NULL, ?)",
                (
                    operation_id,
                    principal_id,
                    operation,
                    usage_date,
                    os.getpid(),
                    timestamp,
                    self._owner.identity,
                ),
            )
            return OperationAdmission(operation_id=operation_id)

    def finish(self, operation_id: str, outcome: OperationOutcome) -> None:
        """Retry bounded transient writer contention without refunding accepted work."""
        for attempt in range(10):
            try:
                with closing(self._connect()) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "UPDATE operation_usage SET outcome = ?, finished_at = ? "
                        "WHERE operation_id = ? AND outcome = 'accepted' AND owner_id = ?",
                        (outcome, time.time(), operation_id, self._owner.identity),
                    )
                return
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", None)
                if (
                    not isinstance(code, int)
                    or code & 0xFF not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                    or attempt == 9
                ):
                    raise
                time.sleep(0.05)

    def close(self) -> None:
        """Release process ownership only after every accepted worker has drained."""
        with closing(self._connect()) as connection:
            active = connection.execute(
                "SELECT COUNT(*) FROM operation_usage WHERE owner_id = ? AND outcome = 'accepted'",
                (self._owner.identity,),
            ).fetchone()[0]
        if active:
            return  # Preserve ownership until process exit or successful terminal recovery.
        self._owner.close()

    def usage(self, principal_id: str, *, now: float | None = None) -> OperationUsage:
        """Read the current UTC day's accepted work and separate outcome counts."""
        timestamp = time.time() if now is None else now
        usage_date = datetime.fromtimestamp(timestamp, UTC).date().isoformat()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT outcome, COUNT(*) FROM operation_usage "
                "WHERE principal_id = ? AND usage_date = ? GROUP BY outcome",
                (principal_id, usage_date),
            ).fetchall()
        counts = dict(rows)
        return OperationUsage(
            accepted=sum(counts.values()),
            active=counts.get("accepted", 0),
            completed=counts.get("completed", 0),
            failed=counts.get("failed", 0),
            cancelled=counts.get("cancelled", 0),
            interrupted=counts.get("interrupted", 0),
        )

    def recover_interrupted(self) -> int:
        """Release abandoned local-process slots without refunding their accepted work."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            owners = connection.execute(
                "SELECT DISTINCT owner_id FROM operation_usage WHERE outcome = 'accepted'"
            ).fetchall()
            recovered = 0
            for (owner_id,) in owners:
                if self._owner.abandoned(owner_id):
                    cursor = connection.execute(
                        "UPDATE operation_usage SET outcome = 'interrupted', finished_at = ? "
                        "WHERE owner_id = ? AND outcome = 'accepted'",
                        (time.time(), owner_id),
                    )
                    recovered += cursor.rowcount
            return recovered
