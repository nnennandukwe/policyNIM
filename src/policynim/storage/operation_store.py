"""Atomic accepted-operation accounting for one hosted SQLite deployment."""

from __future__ import annotations

import logging
import math
import os
import re
import sqlite3
import stat
import time
import uuid
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from policynim.storage.operation_lease import OperationOwner, inspect_owner_lease

# Caller lifecycle outcome after worker drain; provider attempt results stay separate.
OperationOutcome = Literal["completed", "failed", "cancelled", "interrupted"]
AdmissionRejection = Literal[
    "principal_busy",
    "server_busy",
    "quota_exceeded",
    "invalid_token",
    "acceptance_conflict",
    "acceptance_not_found",
    "acceptance_finalized",
]


class CredentialState(BaseModel):
    """Return stored key/account facts without making an authorization decision."""

    model_config = ConfigDict(frozen=True)
    account_id: int
    account_status: str
    revoked_at: str | None


class OperationSnapshot(BaseModel):
    """Expose one write-locked accounting snapshot to the service's admission policy."""

    model_config = ConfigDict(frozen=True)
    active: int
    principal_active: int
    daily_accepted: int
    daily_work_units: int = 0
    reused: bool = False
    credential: CredentialState | None = None


class ReservedWork(BaseModel):
    """Read one principal-bound keyed acceptance without altering its lifecycle."""

    model_config = ConfigDict(frozen=True)
    operation_id: str
    operation: str
    request_digest: str | None
    work_units: int
    outcome: str


class WorkAllowanceSnapshot(BaseModel):
    """Expose a consistent advisory read without charging or recovering operation state."""

    model_config = ConfigDict(frozen=True)
    daily_work_units: int
    credential: CredentialState | None = None
    reservation: ReservedWork | None = None


class OperationAdmission(BaseModel):
    """Describe an admission decision without exposing account or credential data."""

    model_config = ConfigDict(frozen=True)
    operation_id: str | None = None
    rejection: AdmissionRejection | None = None
    work_units: int = 1
    outcome: str = "accepted"


class OperationUsage(BaseModel):
    """Separate accepted work and caller outcomes from unknown provider usage."""

    model_config = ConfigDict(frozen=True)
    accepted: int = 0
    work_units: int = 0
    inactive_accepted: int = 0
    active: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    interrupted: int = 0
    provider_usage: None = None


class HostedOperationReport(BaseModel):
    """Bounded aggregate operational evidence without per-principal labels."""

    model_config = ConfigDict(frozen=True)
    scope: Literal["local_single_instance"] = "local_single_instance"
    retained_rows: int
    active_operations: int
    reserved_operations: int = 0
    reserved_work_units: int = 0
    inactive_accepted_operations: int = 0
    unreconciled_operations: int = 0
    unknown_owner_operations: int = 0
    active_state_basis: Literal["kernel_owner_lease_observation"] = "kernel_owner_lease_observation"
    outcomes: dict[str, int]
    latency_sample_count: int
    latency_sample_limit: int = 1000
    latency_clock: Literal["persisted_utc_wall_timestamps"] = "persisted_utc_wall_timestamps"
    latency_window: Literal["most_recent_timed_terminal_operations"] = (
        "most_recent_timed_terminal_operations"
    )
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    latency_p99_ms: float | None = None
    provider_usage: None = None
    provider_retry_totals: None = None
    job_backlog: None = None


class OperationStore:
    """Serialize authority checks, active limits and daily charges in one transaction."""

    def __init__(self, path: Path) -> None:
        """Create only operation tables, leaving legacy account tables unchanged."""
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._owner = OperationOwner(path)
        self._has_admitted = False
        try:
            self._initialize_schema()
        except BaseException:
            self._owner.close()
            raise

    def _initialize_schema(self) -> None:
        """Initialize operation tables without leaving unused owner files on storage failure."""
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
                    owner_id TEXT NOT NULL,
                    correlation_id TEXT
                );
                CREATE INDEX IF NOT EXISTS operation_usage_principal_day
                    ON operation_usage(principal_id, usage_date);
                CREATE INDEX IF NOT EXISTS operation_usage_active
                    ON operation_usage(outcome);
                CREATE INDEX IF NOT EXISTS operation_usage_finished
                    ON operation_usage(finished_at);
            """)
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(operation_usage)")
                }
                if "correlation_id" not in columns:
                    connection.execute("ALTER TABLE operation_usage ADD COLUMN correlation_id TEXT")
                for column, declaration in {
                    "work_units": "INTEGER NOT NULL DEFAULT 1 CHECK(work_units > 0)",
                    "active_slot": "INTEGER NOT NULL DEFAULT 1 CHECK(active_slot IN (0,1))",
                    "reservation_key": "TEXT",
                    "request_digest": "TEXT",
                }.items():
                    if column not in columns:
                        connection.execute(
                            f"ALTER TABLE operation_usage ADD COLUMN {column} {declaration}"
                        )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS operation_usage_reservation "
                    "ON operation_usage(principal_id, reservation_key) WHERE reservation_key "
                    "IS NOT NULL"
                )

    def _connect(self) -> sqlite3.Connection:
        """Open a bounded-wait connection with explicit transaction ownership."""
        return sqlite3.connect(self.path, timeout=0.1, isolation_level=None)

    def inspect_work_allowance(
        self,
        *,
        principal_id: str,
        reservation_key: str,
        credential_id: str | None = None,
        now: float | None = None,
    ) -> WorkAllowanceSnapshot:
        """Read a keyed reservation, credential and daily units in one nonmutating snapshot."""
        timestamp = time.time() if now is None else now
        usage_date = datetime.fromtimestamp(timestamp, UTC).date().isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            existing = connection.execute(
                "SELECT operation_id, operation, request_digest, work_units, outcome "
                "FROM operation_usage WHERE principal_id = ? AND reservation_key = ?",
                (principal_id, reservation_key),
            ).fetchone()
            credential = None
            if credential_id is not None:
                row = connection.execute(
                    "SELECT a.id, a.status, k.revoked_at FROM accounts a "
                    "JOIN api_keys k ON k.account_id = a.id WHERE k.key_hash = ?",
                    (credential_id,),
                ).fetchone()
                if row is not None:
                    credential = CredentialState(
                        account_id=row[0], account_status=row[1], revoked_at=row[2]
                    )
            return WorkAllowanceSnapshot(
                daily_work_units=connection.execute(
                    "SELECT COALESCE(SUM(work_units), 0) FROM operation_usage "
                    "WHERE principal_id = ? AND usage_date = ?",
                    (principal_id, usage_date),
                ).fetchone()[0],
                credential=credential,
                reservation=ReservedWork(
                    operation_id=existing[0],
                    operation=existing[1],
                    request_digest=existing[2],
                    work_units=existing[3],
                    outcome=existing[4],
                )
                if existing is not None
                else None,
            )

    def reserve(
        self,
        *,
        principal_id: str,
        operation: str,
        decide: Callable[[OperationSnapshot], AdmissionRejection | None],
        credential_id: str | None = None,
        now: float | None = None,
        retention_days: int = 30,
        correlation_id: str | None = None,
        work_units: int = 1,
        active_slot: bool = True,
        reservation_key: str | None = None,
        request_digest: str | None = None,
    ) -> OperationAdmission:
        """Read facts and insert a reservation under one transaction using a service decision."""
        timestamp = time.time() if now is None else now
        usage_date = datetime.fromtimestamp(timestamp, UTC).date().isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_interrupted(connection)
            self._prune(connection, timestamp=timestamp, retention_days=retention_days)
            existing = None
            if reservation_key is not None:
                existing = connection.execute(
                    "SELECT operation_id, operation, request_digest, work_units, outcome "
                    "FROM operation_usage WHERE principal_id = ? AND reservation_key = ?",
                    (principal_id, reservation_key),
                ).fetchone()
                if existing is not None and existing[1:4] != (
                    operation,
                    request_digest,
                    work_units,
                ):
                    return OperationAdmission(rejection="acceptance_conflict")
            credential = None
            if credential_id is not None:
                row = connection.execute(
                    "SELECT a.id, a.status, k.revoked_at FROM accounts a "
                    "JOIN api_keys k ON k.account_id = a.id WHERE k.key_hash = ?",
                    (credential_id,),
                ).fetchone()
                if row is not None:
                    credential = CredentialState(
                        account_id=row[0], account_status=row[1], revoked_at=row[2]
                    )
            snapshot = OperationSnapshot(
                active=connection.execute(
                    "SELECT COUNT(*) FROM operation_usage WHERE outcome = 'accepted' AND "
                    "active_slot = 1"
                ).fetchone()[0],
                principal_active=connection.execute(
                    "SELECT COUNT(*) FROM operation_usage "
                    "WHERE principal_id = ? AND outcome = 'accepted' AND active_slot = 1",
                    (principal_id,),
                ).fetchone()[0],
                daily_accepted=connection.execute(
                    "SELECT COUNT(*) FROM operation_usage "
                    "WHERE principal_id = ? AND usage_date = ?",
                    (principal_id, usage_date),
                ).fetchone()[0],
                daily_work_units=connection.execute(
                    "SELECT COALESCE(SUM(work_units), 0) FROM operation_usage "
                    "WHERE principal_id = ? AND usage_date = ?",
                    (principal_id, usage_date),
                ).fetchone()[0],
                reused=existing is not None,
                credential=credential,
            )
            rejection = decide(snapshot)
            if rejection is not None:
                return OperationAdmission(rejection=rejection)
            if existing is not None:
                return OperationAdmission(
                    operation_id=existing[0], work_units=existing[3], outcome=existing[4]
                )
            operation_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO operation_usage (operation_id, principal_id, operation, usage_date, "
                "outcome, process_id, started_at, finished_at, owner_id, correlation_id, "
                "work_units, active_slot, reservation_key, request_digest) "
                "VALUES (?, ?, ?, ?, 'accepted', ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    principal_id,
                    operation,
                    usage_date,
                    os.getpid(),
                    timestamp,
                    self._owner.identity,
                    correlation_id,
                    work_units,
                    int(active_slot),
                    reservation_key,
                    request_digest,
                ),
            )
            self._has_admitted = self._has_admitted or active_slot
            return OperationAdmission(operation_id=operation_id, work_units=work_units)

    def activate(
        self,
        *,
        operation_id: str,
        principal_id: str,
        operation: str,
        decide: Callable[[OperationSnapshot], AdmissionRejection | None],
        credential_id: str | None = None,
        correlation_id: str | None = None,
    ) -> OperationAdmission:
        """Acquire one execution slot for accepted work without another daily charge."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_interrupted(connection)
            row = connection.execute(
                "SELECT outcome, active_slot, work_units FROM operation_usage "
                "WHERE operation_id = ? AND principal_id = ? AND operation = ? AND "
                "reservation_key IS NOT NULL",
                (operation_id, principal_id, operation),
            ).fetchone()
            if row is None:
                return OperationAdmission(rejection="acceptance_not_found")
            if row[0] != "accepted":
                return OperationAdmission(
                    rejection="acceptance_finalized", outcome=row[0], work_units=row[2]
                )
            if row[1]:
                return OperationAdmission(rejection="principal_busy")
            credential = None
            if credential_id is not None:
                credential_row = connection.execute(
                    "SELECT a.id, a.status, k.revoked_at FROM accounts a "
                    "JOIN api_keys k ON k.account_id = a.id WHERE k.key_hash = ?",
                    (credential_id,),
                ).fetchone()
                if credential_row is not None:
                    credential = CredentialState(
                        account_id=credential_row[0],
                        account_status=credential_row[1],
                        revoked_at=credential_row[2],
                    )
            snapshot = OperationSnapshot(
                active=connection.execute(
                    "SELECT COUNT(*) FROM operation_usage WHERE outcome='accepted' AND "
                    "active_slot=1"
                ).fetchone()[0],
                principal_active=connection.execute(
                    "SELECT COUNT(*) FROM operation_usage WHERE outcome='accepted' AND "
                    "active_slot=1 AND principal_id=?",
                    (principal_id,),
                ).fetchone()[0],
                daily_accepted=0,
                credential=credential,
            )
            rejection = decide(snapshot)
            if rejection is not None:
                return OperationAdmission(rejection=rejection)
            connection.execute(
                "UPDATE operation_usage SET active_slot=1, owner_id=?, process_id=?, "
                "correlation_id=COALESCE(?,correlation_id) "
                "WHERE operation_id=?",
                (self._owner.identity, os.getpid(), correlation_id, operation_id),
            )
            self._has_admitted = True
            return OperationAdmission(operation_id=operation_id, work_units=row[2])

    def finish_accepted(
        self,
        *,
        operation_id: str,
        principal_id: str,
        operation: str,
        outcome: OperationOutcome,
    ) -> str | None:
        """Finalize an inactive acceptance by bound identity without changing terminal history."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_interrupted(connection)
            row = connection.execute(
                "SELECT outcome, active_slot FROM operation_usage WHERE operation_id=? "
                "AND principal_id=? AND operation=? AND reservation_key IS NOT NULL",
                (operation_id, principal_id, operation),
            ).fetchone()
            if row is None:
                return None
            if row[0] != "accepted":
                return row[0]
            if row[1]:
                return "accepted"
            connection.execute(
                "UPDATE operation_usage SET outcome=?, finished_at=? WHERE operation_id=?",
                (outcome, time.time(), operation_id),
            )
            return outcome

    def finish(self, operation_id: str, outcome: OperationOutcome) -> None:
        """Retry bounded transient writer contention without refunding accepted work."""
        for attempt in range(10):
            try:
                with closing(self._connect()) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "UPDATE operation_usage SET outcome = ?, finished_at = ?, active_slot = 0 "
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
        if not self._has_admitted:
            self._owner.close()
            return
        try:
            with closing(self._connect()) as connection:
                active = connection.execute(
                    "SELECT COUNT(*) FROM operation_usage "
                    "WHERE owner_id = ? AND outcome = 'accepted' AND active_slot = 1",
                    (self._owner.identity,),
                ).fetchone()[0]
        except (sqlite3.Error, OSError) as exc:
            logging.getLogger(__name__).warning(
                "Operation owner release deferred: %s. Retry close after state storage recovers.",
                type(exc).__name__,
            )
            return  # Unknown accounting retains ownership until retry or process exit.
        if active:
            return  # Preserve ownership until process exit or successful terminal recovery.
        self._owner.close()

    def prune_owner_leases(self) -> int:
        """Inspect at most 100 lease files, removing only unlocked owners with no accepted rows."""
        removed = 0
        try:
            with closing(self._connect()) as connection:
                for index, path in enumerate(self._owner.directory.iterdir()):
                    if index >= 100:
                        break
                    if re.fullmatch(r"\.[0-9a-f]{32}\.pending", path.name):
                        observed = path.stat(follow_symlinks=False)
                        if (
                            stat.S_ISREG(observed.st_mode)
                            and observed.st_mtime < time.time() - 3600
                        ):
                            path.unlink()
                            removed += 1
                        continue
                    if path.suffix != ".lock":
                        continue
                    active = connection.execute(
                        "SELECT 1 FROM operation_usage WHERE owner_id = ? "
                        "AND outcome = 'accepted' AND active_slot = 1 LIMIT 1",
                        (path.stem,),
                    ).fetchone()
                    if active is None and self._owner.abandoned(path.stem, remove=True):
                        removed += 1
        except (sqlite3.Error, OSError) as exc:
            logging.getLogger(__name__).warning(
                "Operation owner cleanup deferred: %s.", type(exc).__name__
            )
        return removed

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
            units, active, queued = connection.execute(
                "SELECT COALESCE(SUM(work_units),0), "
                "COALESCE(SUM(outcome='accepted' AND active_slot=1),0), "
                "COALESCE(SUM(outcome='accepted' AND active_slot=0),0) "
                "FROM operation_usage WHERE principal_id=? AND usage_date=?",
                (principal_id, usage_date),
            ).fetchone()
        counts = dict(rows)
        return OperationUsage(
            accepted=sum(counts.values()),
            work_units=units,
            active=active,
            inactive_accepted=queued,
            completed=counts.get("completed", 0),
            failed=counts.get("failed", 0),
            cancelled=counts.get("cancelled", 0),
            interrupted=counts.get("interrupted", 0),
        )

    def recover_interrupted(self) -> int:
        """Release abandoned local-process slots without refunding their accepted work."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._recover_interrupted(connection)

    def _recover_interrupted(self, connection: sqlite3.Connection) -> int:
        """Reconcile unlocked owner leases inside the caller's existing write transaction."""
        owners = connection.execute(
            "SELECT DISTINCT owner_id FROM operation_usage WHERE outcome = 'accepted' AND "
            "active_slot = 1"
        ).fetchall()
        recovered = 0
        for (owner_id,) in owners:
            if self._owner.abandoned(owner_id):
                cursor = connection.execute(
                    "UPDATE operation_usage SET outcome = 'interrupted', finished_at = ?, "
                    "active_slot = 0 "
                    "WHERE owner_id = ? AND outcome = 'accepted' AND active_slot = 1",
                    (time.time(), owner_id),
                )
                recovered += cursor.rowcount
        return recovered

    @staticmethod
    def _prune(connection: sqlite3.Connection, *, timestamp: float, retention_days: int) -> int:
        """Delete only old terminal rows, preserving active work and today's allowance."""
        if retention_days < 1:
            raise ValueError("Operation retention must be at least one day.")
        today = datetime.fromtimestamp(timestamp, UTC).date().isoformat()
        cursor = connection.execute(
            "DELETE FROM operation_usage WHERE operation_id IN ("
            "SELECT operation_id FROM operation_usage WHERE outcome != 'accepted' "
            "AND reservation_key IS NULL AND finished_at < ? AND usage_date < ? ORDER BY "
            "finished_at LIMIT 1000)",
            (timestamp - retention_days * 86400, today),
        )
        return cursor.rowcount

    def prune(self, *, retention_days: int, now: float | None = None) -> int:
        """Apply the same retention transaction independently for operator/test inspection."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._prune(
                connection,
                timestamp=time.time() if now is None else now,
                retention_days=retention_days,
            )

    @staticmethod
    def report(path: Path) -> HostedOperationReport:
        """Read aggregate operational state without creating or migrating any database."""
        uri = path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.1)) as connection:
            connection.execute("BEGIN")
            if (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'operation_usage'"
                ).fetchone()
                is None
            ):
                return HostedOperationReport(
                    retained_rows=0, active_operations=0, outcomes={}, latency_sample_count=0
                )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(operation_usage)")}
            active_filter = " AND active_slot = 1" if "active_slot" in columns else ""
            work_units, queued = connection.execute(
                "SELECT "
                + ("COALESCE(SUM(work_units),0)" if "work_units" in columns else "COUNT(*)")
                + ", "
                + (
                    "COALESCE(SUM(outcome='accepted' AND active_slot=0),0)"
                    if "active_slot" in columns
                    else "0"
                )
                + " FROM operation_usage"
            ).fetchone()
            outcomes = dict(
                connection.execute(
                    "SELECT outcome, COUNT(*) FROM operation_usage GROUP BY outcome"
                ).fetchall()
            )
            owner_counts = connection.execute(
                "SELECT owner_id, COUNT(*) FROM operation_usage "
                "WHERE outcome = 'accepted'" + active_filter + " GROUP BY owner_id"
            ).fetchall()
            samples = sorted(
                row[0]
                for row in connection.execute(
                    "SELECT (finished_at - started_at) * 1000 FROM operation_usage "
                    "WHERE outcome IN ('completed', 'failed', 'cancelled') "
                    "AND finished_at >= started_at ORDER BY finished_at DESC LIMIT 1000"
                ).fetchall()
            )

        def percentile(fraction: float) -> float | None:
            """Use nearest-rank percentiles over the explicitly bounded terminal sample."""
            return (
                round(samples[max(0, math.ceil(len(samples) * fraction) - 1)], 3)
                if samples
                else None
            )

        liveness = {"live": 0, "abandoned": 0, "unknown": 0}
        for owner_id, count in owner_counts:
            liveness[inspect_owner_lease(path, owner_id)] += count
        return HostedOperationReport(
            retained_rows=sum(outcomes.values()),
            active_operations=liveness["live"],
            reserved_operations=outcomes.get("accepted", 0),
            reserved_work_units=work_units,
            inactive_accepted_operations=queued,
            unreconciled_operations=liveness["abandoned"],
            unknown_owner_operations=liveness["unknown"],
            outcomes=outcomes,
            latency_sample_count=len(samples),
            latency_p50_ms=percentile(0.5),
            latency_p95_ms=percentile(0.95),
            latency_p99_ms=percentile(0.99),
        )
