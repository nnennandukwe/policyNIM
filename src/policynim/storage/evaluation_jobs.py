"""Transactional persistence for bounded local evaluation workers."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from policynim.job_types import (
    TERMINAL_JOB_STATES,
    EvaluationJobError,
    EvaluationJobInfo,
    EvaluationJobReport,
    EvaluationJobSnapshot,
    EvaluationJobState,
    EvaluationJobUsage,
    StoredEvaluationJobReport,
)
from policynim.types import EvalCaseResult


def canonical_json(value: object) -> str:
    """Serialize already validated JSON-compatible values deterministically."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def payload_digest(value: object) -> str:
    """Hash the exact canonical JSON representation used for durable evidence."""
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def archive_digest(payload: bytes) -> str:
    """Hash the captured generation archive before accepting or restoring it."""
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class EvaluationJobStore:
    """Own short SQLite transactions; keep provider work outside database locks."""

    def __init__(
        self,
        path: Path,
        *,
        queue_limit: int = 8,
        owner_queue_limit: int = 2,
        clock: Callable[[], float] = time.time,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        """Create only this job schema and reject unsupported storage versions."""
        if not 1 <= queue_limit <= 100 or not 1 <= owner_queue_limit <= 100:
            raise ValueError("Job queue limits must be between 1 and 100 and owner <= global.")
        self.path, self.clock, self.fault = path, clock, fault
        self.queue_limit, self.owner_queue_limit = queue_limit, min(owner_queue_limit, queue_limit)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise EvaluationJobError("job_store_invalid: Job storage can't be a symlink.")
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)

        with closing(self._connect()) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise EvaluationJobError("job_store_version: Upgrade the worker before retrying.")
            connection.execute("PRAGMA journal_mode=WAL")
            migration = (
                "ALTER TABLE evaluation_jobs ADD COLUMN active_case_start_attempts "
                "INTEGER NOT NULL DEFAULT 0;"
                if version == 1
                else ""
            )
            connection.executescript(f"""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS evaluation_job_archives (
                    generation_id TEXT PRIMARY KEY, digest TEXT NOT NULL, archive BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evaluation_jobs (
                    job_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, request_digest TEXT NOT NULL,
                    state TEXT NOT NULL, accepted_at REAL NOT NULL,
                    started_at REAL, finished_at REAL, deadline_at REAL NOT NULL,
                    expires_at REAL NOT NULL, generation_id TEXT NOT NULL,
                    suite_digest TEXT NOT NULL, configuration_digest TEXT NOT NULL,
                    snapshot_digest TEXT NOT NULL, case_count INTEGER NOT NULL,
                    completed_case_count INTEGER NOT NULL DEFAULT 0,
                    active_case_id TEXT, active_case_start_attempts INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    failure_code TEXT, report_sha256 TEXT,
                    result_expired INTEGER NOT NULL DEFAULT 0,
                    snapshot_json TEXT, report_json TEXT, usage_json TEXT NOT NULL,
                    worker_token TEXT, next_attempt_at REAL NOT NULL DEFAULT 0,
                    UNIQUE(owner_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS evaluation_job_cases (
                    job_id TEXT NOT NULL, case_id TEXT NOT NULL,
                    result_json TEXT NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY(job_id, case_id),
                    FOREIGN KEY(job_id) REFERENCES evaluation_jobs(job_id)
                );
                {migration}
                PRAGMA user_version=2;
                COMMIT;
            """)

    def _connect(self) -> sqlite3.Connection:
        """Use fresh handles so restart and concurrent callers never share transactions."""
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Commit one mutation group or roll it back without replacing another writer's work."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                if self.fault is not None:
                    self.fault("before_commit")
                connection.commit()
                if self.fault is not None:
                    self.fault("after_commit")
            except BaseException:
                connection.rollback()
                raise

    def _info(self, row: sqlite3.Row) -> EvaluationJobInfo:
        """Read progress directly from its canonical columns."""
        values = {key: row[key] for key in EvaluationJobInfo.model_fields}
        values["result_expired"] = bool(row["result_expired"]) or self.clock() >= row["expires_at"]
        return EvaluationJobInfo.model_validate(values)

    def _owned(self, connection: sqlite3.Connection, job_id: str, owner_id: str) -> sqlite3.Row:
        """Make unknown and other-owner references indistinguishable to callers."""
        row = connection.execute(
            "SELECT * FROM evaluation_jobs WHERE job_id=? AND owner_id=?", (job_id, owner_id)
        ).fetchone()
        if row is None:
            raise EvaluationJobError("job_unavailable: This job isn't available to this principal.")
        return row

    def find_submission(
        self, owner_id: str, idempotency_key: str, request_digest: str
    ) -> EvaluationJobInfo | None:
        """Return an identical accepted submission before generation capture or admission."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE owner_id=? AND idempotency_key=?",
                (owner_id, idempotency_key),
            ).fetchone()
            if row is None:
                return None
            if row["request_digest"] != request_digest:
                raise EvaluationJobError(
                    "idempotency_conflict: This key already identifies different inputs. "
                    "Inspect the accepted job or use a new key."
                )
            return self._info(row)

    def ensure_capacity(self, owner_id: str) -> None:
        """Reject known saturation before capture; submit rechecks inside its transaction."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*), SUM(owner_id=?) FROM evaluation_jobs "
                "WHERE state IN ('queued','running')",
                (owner_id,),
            ).fetchone()
            if row[0] >= self.queue_limit or (row[1] or 0) >= self.owner_queue_limit:
                raise EvaluationJobError(
                    "queue_full: No job was accepted. Retry after active work finishes."
                )

    def submit(
        self,
        *,
        owner_id: str,
        idempotency_key: str,
        request_digest: str,
        snapshot: EvaluationJobSnapshot,
        archive: bytes,
    ) -> EvaluationJobInfo:
        """Commit an accepted snapshot and its archive atomically after bounded deduplication."""
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE owner_id=? AND idempotency_key=?",
                (owner_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != request_digest:
                    raise EvaluationJobError("idempotency_conflict: Use a new submission key.")
                return self._info(existing)
            active = connection.execute(
                "SELECT COUNT(*), SUM(owner_id=?) FROM evaluation_jobs "
                "WHERE state IN ('queued','running')",
                (owner_id,),
            ).fetchone()
            if active[0] >= self.queue_limit or (active[1] or 0) >= self.owner_queue_limit:
                raise EvaluationJobError(
                    "queue_full: No job was accepted. Retry after active work finishes."
                )
            digest = archive_digest(archive)
            if digest != snapshot.generation_archive_digest:
                raise EvaluationJobError("archive_invalid: Generation capture failed validation.")
            connection.execute(
                "INSERT OR IGNORE INTO evaluation_job_archives VALUES (?,?,?)",
                (snapshot.generation_id, digest, archive),
            )
            retained = connection.execute(
                "SELECT digest FROM evaluation_job_archives WHERE generation_id=?",
                (snapshot.generation_id,),
            ).fetchone()
            if retained[0] != digest:
                raise EvaluationJobError(
                    "archive_invalid: Preserve the job database for inspection."
                )
            now = self.clock()
            info = EvaluationJobInfo(
                job_id=uuid4().hex,
                owner_id=owner_id,
                accepted_at=now,
                deadline_at=now + snapshot.limits.deadline_seconds,
                expires_at=now
                + snapshot.limits.deadline_seconds
                + snapshot.limits.retention_seconds,
                generation_id=snapshot.generation_id,
                suite_digest=snapshot.suite_digest,
                configuration_digest=snapshot.configuration_digest,
                snapshot_digest=payload_digest(snapshot.model_dump(mode="json")),
                case_count=len(snapshot.suite.cases),
            )
            values = info.model_dump(mode="json")
            values.update(
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                snapshot_json=canonical_json(snapshot.model_dump(mode="json")),
                usage_json=canonical_json(EvaluationJobUsage().model_dump(mode="json")),
            )
            columns = ",".join(values)
            placeholders = ",".join("?" for _ in values)
            connection.execute(
                f"INSERT INTO evaluation_jobs ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
            return info

    def inspect(self, job_id: str, owner_id: str) -> EvaluationJobInfo:
        """Expose only the authenticated owner's progress."""
        with closing(self._connect()) as connection:
            return self._info(self._owned(connection, job_id, owner_id))

    def _finish(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        state: EvaluationJobState,
        *,
        failure_code: str | None = None,
        uncertain: bool = False,
    ) -> EvaluationJobInfo:
        """Quarantine invalid artifacts without blocking other jobs or inventing a report."""
        try:
            return self._finish_report(
                connection, row, state, failure_code=failure_code, uncertain=uncertain
            )
        except (EvaluationJobError, ValidationError, TypeError):
            connection.execute(
                "UPDATE evaluation_jobs SET state='failed',finished_at=?,"
                "failure_code='job_artifact_invalid',worker_token=NULL,"
                "report_json=NULL,report_sha256=NULL WHERE job_id=?",
                (self.clock(), row["job_id"]),
            )
            current = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE job_id=?", (row["job_id"],)
            ).fetchone()
            return self._info(current)

    def _finish_report(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        state: EvaluationJobState,
        *,
        failure_code: str | None = None,
        uncertain: bool = False,
    ) -> EvaluationJobInfo:
        """Commit terminal state and immutable report bytes together."""
        if row["state"] in TERMINAL_JOB_STATES:
            return self._info(row)
        snapshot = EvaluationJobSnapshot.model_validate_json(row["snapshot_json"])
        results = connection.execute(
            "SELECT * FROM evaluation_job_cases WHERE job_id=? ORDER BY rowid", (row["job_id"],)
        ).fetchall()
        completed = [EvalCaseResult.model_validate_json(item["result_json"]) for item in results]
        if payload_digest(snapshot.model_dump(mode="json")) != row["snapshot_digest"]:
            raise EvaluationJobError(
                "snapshot_invalid: Preserve immutable job inputs for inspection."
            )
        for record, result in zip(results, completed, strict=True):
            if payload_digest(result.model_dump(mode="json")) != record["digest"]:
                raise EvaluationJobError(
                    "case_artifact_invalid: Preserve the retained evidence for inspection."
                )

        completed_ids = {item.case_id for item in completed}
        report = EvaluationJobReport(
            job_id=row["job_id"],
            state=state,
            snapshot=snapshot,
            snapshot_digest=row["snapshot_digest"],
            completed_cases=completed,
            unfinished_case_ids=[
                case.case_id for case in snapshot.suite.cases if case.case_id not in completed_ids
            ],
            uncertain_case_id=(
                row["active_case_id"]
                if uncertain
                and EvaluationJobUsage.model_validate_json(row["usage_json"]).provider_attempts
                > row["active_case_start_attempts"]
                else None
            ),
            failure_code=failure_code,
            usage=EvaluationJobUsage.model_validate_json(row["usage_json"]),
            case_artifact_hashes={item["case_id"]: item["digest"] for item in results},
        )
        serialized = canonical_json(report.model_dump(mode="json"))
        digest = payload_digest(report.model_dump(mode="json"))
        now = self.clock()
        connection.execute(
            "UPDATE evaluation_jobs SET state=?,finished_at=?,expires_at=?,failure_code=?, "
            "report_json=?,report_sha256=?,active_case_id=NULL,worker_token=NULL WHERE job_id=?",
            (
                state,
                now,
                now + snapshot.limits.retention_seconds,
                failure_code,
                serialized,
                digest,
                row["job_id"],
            ),
        )
        current = connection.execute(
            "SELECT * FROM evaluation_jobs WHERE job_id=?", (row["job_id"],)
        ).fetchone()
        return self._info(current)

    def cancel(self, job_id: str, owner_id: str) -> EvaluationJobInfo:
        """Cancel queued work immediately and request cooperative cancellation for active work."""
        with self._transaction() as connection:
            row = self._owned(connection, job_id, owner_id)
            if row["state"] == "queued":
                return self._finish(connection, row, "cancelled", failure_code="cancelled")
            if row["state"] == "running":
                connection.execute(
                    "UPDATE evaluation_jobs SET cancel_requested=1 WHERE job_id=?", (job_id,)
                )
                row = self._owned(connection, job_id, owner_id)
            return self._info(row)

    def result(self, job_id: str, owner_id: str) -> StoredEvaluationJobReport:
        """Verify the immutable report digest before returning caller-specific evidence."""
        with closing(self._connect()) as connection:
            row = self._owned(connection, job_id, owner_id)
            if self.clock() >= row["expires_at"] or row["result_expired"]:
                raise EvaluationJobError("result_expired: The retained report has expired.")
            if row["state"] not in TERMINAL_JOB_STATES:
                raise EvaluationJobError(
                    "result_pending: Inspect the job until it reaches a terminal state."
                )
            if row["report_json"] is None:
                raise EvaluationJobError(
                    "report_unavailable: Job artifacts failed validation. "
                    "Preserve job storage for operator inspection."
                )
            report = EvaluationJobReport.model_validate_json(row["report_json"])
            if payload_digest(report.model_dump(mode="json")) != row["report_sha256"]:
                raise EvaluationJobError("report_invalid: Preserve job storage for inspection.")
            return StoredEvaluationJobReport(report=report, sha256=row["report_sha256"])

    def recover_interrupted(self) -> list[EvaluationJobInfo]:
        """Only a new kernel-lock owner may call this; uncertain provider work never requeues."""
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE state='running'"
            ).fetchall()
            return [
                self._finish(
                    connection,
                    row,
                    "interrupted",
                    failure_code="worker_interrupted",
                    uncertain=True,
                )
                for row in rows
            ]

    def claim(self, worker_token: str) -> EvaluationJobInfo | None:
        """Claim the oldest eligible queued job before provider or generation-restore work."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE state='queued' AND next_attempt_at<=? "
                "ORDER BY accepted_at, rowid LIMIT 1",
                (self.clock(),),
            ).fetchone()
            if row is None:
                return None
            if row["deadline_at"] <= self.clock():
                return self._finish(
                    connection, row, "deadline_exceeded", failure_code="deadline_exhausted"
                )
            connection.execute(
                "UPDATE evaluation_jobs SET state='running',started_at=?,worker_token=? "
                "WHERE job_id=? AND state='queued'",
                (self.clock(), worker_token, row["job_id"]),
            )
            return self._info(
                connection.execute(
                    "SELECT * FROM evaluation_jobs WHERE job_id=?", (row["job_id"],)
                ).fetchone()
            )

    def _running(
        self, connection: sqlite3.Connection, job_id: str, worker_token: str
    ) -> sqlite3.Row:
        """Recheck exact claim ownership at every worker mutation."""
        row = connection.execute(
            "SELECT * FROM evaluation_jobs WHERE job_id=? AND state='running' AND worker_token=?",
            (job_id, worker_token),
        ).fetchone()
        if row is None:
            raise EvaluationJobError(
                "worker_ownership_lost: Don't repeat provider work; inspect the job."
            )
        return row

    def captured_inputs(
        self, job_id: str, worker_token: str
    ) -> tuple[EvaluationJobSnapshot, bytes]:
        """Return the accepted snapshot and authenticated archive only to its current worker."""
        with closing(self._connect()) as connection:
            row = self._running(connection, job_id, worker_token)
            snapshot = EvaluationJobSnapshot.model_validate_json(row["snapshot_json"])
            if payload_digest(snapshot.model_dump(mode="json")) != row["snapshot_digest"]:
                raise EvaluationJobError("snapshot_invalid: Preserve job storage for inspection.")
            archive = connection.execute(
                "SELECT * FROM evaluation_job_archives WHERE generation_id=?",
                (snapshot.generation_id,),
            ).fetchone()
            if (
                archive is None
                or archive_digest(archive["archive"]) != snapshot.generation_archive_digest
            ):
                raise EvaluationJobError("archive_invalid: Preserve job storage for inspection.")
            return snapshot, archive["archive"]

    def begin_case(self, job_id: str, worker_token: str, case_id: str) -> None:
        """Persist the uncertain provider stage before the evaluator can start."""
        with self._transaction() as connection:
            row = self._running(connection, job_id, worker_token)
            if row["active_case_id"] is not None:
                raise EvaluationJobError("case_already_active: Inspect the job; don't repeat it.")
            snapshot = EvaluationJobSnapshot.model_validate_json(row["snapshot_json"])
            if case_id not in {item.case_id for item in snapshot.suite.cases}:
                raise EvaluationJobError("unknown_case: The case isn't in accepted inputs.")
            if (
                connection.execute(
                    "SELECT 1 FROM evaluation_job_cases WHERE job_id=? AND case_id=?",
                    (job_id, case_id),
                ).fetchone()
                is not None
            ):
                raise EvaluationJobError("case_already_completed: Never repeat a completed case.")

            connection.execute(
                "UPDATE evaluation_jobs SET active_case_id=?,active_case_start_attempts=? "
                "WHERE job_id=?",
                (
                    case_id,
                    EvaluationJobUsage.model_validate_json(row["usage_json"]).provider_attempts,
                    job_id,
                ),
            )

    def save_case(self, job_id: str, worker_token: str, result: EvalCaseResult) -> None:
        """Commit one completed case before another provider stage starts."""
        with self._transaction() as connection:
            row = self._running(connection, job_id, worker_token)
            if (
                row["active_case_id"] != result.case_id
                or row["generation_id"] != result.generation_id
            ):
                raise EvaluationJobError(
                    "case_input_mismatch: The result doesn't match accepted inputs."
                )
            value = result.model_dump(mode="json")
            connection.execute(
                "INSERT INTO evaluation_job_cases VALUES (?,?,?,?)",
                (job_id, result.case_id, canonical_json(value), payload_digest(value)),
            )
            connection.execute(
                "UPDATE evaluation_jobs SET active_case_id=NULL,"
                "completed_case_count=completed_case_count+1 WHERE job_id=?",
                (job_id,),
            )

    def save_usage(self, job_id: str, worker_token: str, usage: EvaluationJobUsage) -> None:
        """Persist each attempt reservation and any returned measured usage."""
        with self._transaction() as connection:
            self._running(connection, job_id, worker_token)
            connection.execute(
                "UPDATE evaluation_jobs SET usage_json=? WHERE job_id=?",
                (canonical_json(usage.model_dump(mode="json")), job_id),
            )

    def cancellation_requested(self, job_id: str) -> bool:
        """Read a cancellation signal across processes without changing job state."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM evaluation_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            return row is None or bool(row[0])

    def finish(
        self,
        job_id: str,
        worker_token: str,
        state: EvaluationJobState,
        *,
        failure_code: str | None = None,
        uncertain: bool = False,
    ) -> EvaluationJobInfo:
        """Keep a terminal report inseparable from its guarded final state."""
        if state not in TERMINAL_JOB_STATES:
            raise ValueError("Only terminal job states can finish execution.")
        with self._transaction() as connection:
            return self._finish(
                connection,
                self._running(connection, job_id, worker_token),
                state,
                failure_code=failure_code,
                uncertain=uncertain,
            )

    def purge_expired(self) -> int:
        """Delete only expired caller payloads and archives no retained job still references."""
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT job_id FROM evaluation_jobs WHERE expires_at<=? "
                "AND state NOT IN ('queued','running') AND result_expired=0",
                (self.clock(),),
            ).fetchall()
            for row in rows:
                connection.execute("DELETE FROM evaluation_job_cases WHERE job_id=?", (row[0],))
                connection.execute(
                    "UPDATE evaluation_jobs SET result_expired=1,snapshot_json=NULL,"
                    "report_json=NULL WHERE job_id=?",
                    (row[0],),
                )
            connection.execute(
                "DELETE FROM evaluation_job_archives WHERE generation_id NOT IN "
                "(SELECT generation_id FROM evaluation_jobs WHERE result_expired=0)"
            )
            return len(rows)

    def aggregate_counts(self) -> dict[str, int]:
        """Expose bounded operational counts without caller identities or request contents."""
        with closing(self._connect()) as connection:
            return {
                row["state"]: row["count"]
                for row in connection.execute(
                    "SELECT state, COUNT(*) AS count FROM evaluation_jobs GROUP BY state"
                )
            }


def inspect_job_backlog(path: Path) -> dict[str, int] | None:
    """Read aggregate operator evidence without creating a database or revealing caller data."""
    if not path.exists():
        return None
    if path.is_symlink():
        raise EvaluationJobError("job_store_invalid: Job storage can't be a symlink.")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != 2:
            raise EvaluationJobError("job_store_version: Upgrade the worker before retrying.")
        return {
            state: count
            for state, count in db.execute(
                "SELECT state, COUNT(*) FROM evaluation_jobs GROUP BY state"
            )
        }
