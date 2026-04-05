"""SQLite-backed storage for hosted beta accounts, API keys, and usage."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

from policynim.errors import PolicyNIMError
from policynim.types import BetaAccount, BetaAccountStatus, BetaUsageSnapshot

_ACTIVE_STATUS = "active"
_SUSPENDED_STATUS = "suspended"
_ACCOUNT_SELECT = """
SELECT
    a.id AS account_id,
    a.github_user_id,
    a.github_login,
    a.email,
    a.status,
    a.created_at,
    a.last_login_at,
    (
        SELECT key_prefix
        FROM api_keys
        WHERE account_id = a.id AND revoked_at IS NULL
        ORDER BY id DESC
        LIMIT 1
    ) AS api_key_prefix,
    (
        SELECT created_at
        FROM api_keys
        WHERE account_id = a.id AND revoked_at IS NULL
        ORDER BY id DESC
        LIMIT 1
    ) AS api_key_created_at
FROM accounts a
"""


class AuthStore:
    """Persist hosted beta auth state in one local SQLite database."""

    def __init__(self, *, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @property
    def path(self) -> Path:
        """Return the underlying auth database path."""
        return self._path

    def close(self) -> None:
        """Release owned resources.

        The store opens one SQLite connection per operation, so there is no shared
        handle to close. This hook exists to keep tests and services symmetrical.
        """

    def reset_for_tests(self) -> None:
        """Reset the backing SQLite file for deterministic test re-entry."""
        if self._path.exists():
            self._path.unlink()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def list_accounts(self) -> list[BetaAccount]:
        """Return all hosted beta accounts with active-key metadata."""
        with closing(self._connect()) as conn:
            rows = conn.execute(_ACCOUNT_SELECT + " ORDER BY a.created_at ASC").fetchall()
            return [_account_from_row(row) for row in rows]

    def get_account_by_id(self, account_id: int) -> BetaAccount | None:
        """Return one hosted beta account by internal id."""
        with closing(self._connect()) as conn:
            return self._fetch_account_by_column(conn, "a.id", account_id)

    def get_account_by_github_login(self, github_login: str) -> BetaAccount | None:
        """Return one hosted beta account by GitHub login."""
        with closing(self._connect()) as conn:
            return self._fetch_account_by_column(conn, "a.github_login", github_login)

    def upsert_account_from_github(
        self,
        *,
        github_user_id: int,
        github_login: str,
        email: str | None,
        now: datetime,
    ) -> BetaAccount:
        """Create or update one hosted beta account after GitHub login."""
        with closing(self._connect()) as conn:
            _begin_immediate(conn)
            try:
                row = conn.execute(
                    "SELECT id FROM accounts WHERE github_user_id = ?",
                    (github_user_id,),
                ).fetchone()
                if row is None:
                    cursor = conn.execute(
                        """
                        INSERT INTO accounts (
                            github_user_id,
                            github_login,
                            email,
                            status,
                            created_at,
                            last_login_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            github_user_id,
                            github_login,
                            email,
                            _ACTIVE_STATUS,
                            _iso_utc(now),
                            _iso_utc(now),
                        ),
                    )
                    lastrowid = cursor.lastrowid
                    if lastrowid is None:
                        raise PolicyNIMError("SQLite did not return an account id.")
                    account_id = int(lastrowid)
                    self._insert_audit_event(
                        conn,
                        account_id=account_id,
                        event_type="account_signup",
                        details={
                            "github_login": github_login,
                            "email": email,
                        },
                        now=now,
                    )
                else:
                    account_id = int(row["id"])
                    conn.execute(
                        """
                        UPDATE accounts
                        SET github_login = ?, email = ?, last_login_at = ?
                        WHERE id = ?
                        """,
                        (github_login, email, _iso_utc(now), account_id),
                    )
                    self._insert_audit_event(
                        conn,
                        account_id=account_id,
                        event_type="account_login",
                        details={
                            "github_login": github_login,
                            "email": email,
                        },
                        now=now,
                    )
                account = self._require_account(conn, account_id)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return account

    def rotate_api_key(
        self,
        *,
        account_id: int,
        key_prefix: str,
        key_hash: str,
        now: datetime,
    ) -> BetaAccount:
        """Revoke the current active key and persist a new one atomically."""
        with closing(self._connect()) as conn:
            _begin_immediate(conn)
            try:
                self._require_account(conn, account_id)
                conn.execute(
                    (
                        "UPDATE api_keys SET revoked_at = ? "
                        "WHERE account_id = ? AND revoked_at IS NULL"
                    ),
                    (_iso_utc(now), account_id),
                )
                conn.execute(
                    """
                    INSERT INTO api_keys (account_id, key_prefix, key_hash, created_at, revoked_at)
                    VALUES (?, ?, ?, ?, NULL)
                    """,
                    (account_id, key_prefix, key_hash, _iso_utc(now)),
                )
                self._insert_audit_event(
                    conn,
                    account_id=account_id,
                    event_type="api_key_rotated",
                    details={"key_prefix": key_prefix},
                    now=now,
                )
                account = self._require_account(conn, account_id)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return account

    def revoke_active_key(self, *, account_id: int, now: datetime) -> BetaAccount:
        """Revoke the current active key, if one exists."""
        with closing(self._connect()) as conn:
            _begin_immediate(conn)
            try:
                account = self._require_account(conn, account_id)
                active_key = conn.execute(
                    """
                    SELECT key_prefix
                    FROM api_keys
                    WHERE account_id = ? AND revoked_at IS NULL
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (account_id,),
                ).fetchone()
                conn.execute(
                    (
                        "UPDATE api_keys SET revoked_at = ? "
                        "WHERE account_id = ? AND revoked_at IS NULL"
                    ),
                    (_iso_utc(now), account_id),
                )
                if active_key is not None:
                    self._insert_audit_event(
                        conn,
                        account_id=account_id,
                        event_type="api_key_revoked",
                        details={"key_prefix": str(active_key["key_prefix"])},
                        now=now,
                    )
                account = self._require_account(conn, account_id)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return account

    def set_account_status(self, *, account_id: int, status: str, now: datetime) -> BetaAccount:
        """Persist the supplied account status."""
        with closing(self._connect()) as conn:
            _begin_immediate(conn)
            try:
                if status not in {_ACTIVE_STATUS, _SUSPENDED_STATUS}:
                    raise PolicyNIMError(f"Unsupported beta account status {status!r}.")
                conn.execute(
                    "UPDATE accounts SET status = ? WHERE id = ?",
                    (status, account_id),
                )
                event_type = "account_resumed" if status == _ACTIVE_STATUS else "account_suspended"
                self._insert_audit_event(
                    conn,
                    account_id=account_id,
                    event_type=event_type,
                    details={"status": status},
                    now=now,
                )
                account = self._require_account(conn, account_id)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return account

    def authenticate_api_key(self, *, key_hash: str) -> BetaAccount | None:
        """Return the owning account for one active API key hash."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                _ACCOUNT_SELECT
                + """
                JOIN api_keys k ON k.account_id = a.id
                WHERE k.key_hash = ? AND k.revoked_at IS NULL
                LIMIT 1
                """,
                (key_hash,),
            ).fetchone()
            if row is None:
                return None
            return _account_from_row(row)

    def consume_daily_quota(
        self,
        *,
        account_id: int,
        usage_date: date,
        quota: int,
        now: datetime,
    ) -> tuple[BetaUsageSnapshot, bool]:
        """Consume one request from the current UTC-day quota if capacity remains."""
        with closing(self._connect()) as conn:
            _begin_immediate(conn)
            try:
                self._require_account(conn, account_id)
                row = conn.execute(
                    "SELECT request_count FROM daily_usage WHERE account_id = ? AND usage_date = ?",
                    (account_id, usage_date.isoformat()),
                ).fetchone()
                current = int(row["request_count"]) if row is not None else 0
                if current >= quota:
                    snapshot = _usage_snapshot(
                        usage_date=usage_date,
                        request_count=current,
                        quota=quota,
                    )
                    self._insert_audit_event(
                        conn,
                        account_id=account_id,
                        event_type="quota_exceeded",
                        details={"usage_date": usage_date.isoformat(), "request_count": current},
                        now=now,
                    )
                    conn.execute("COMMIT")
                    return snapshot, False

                next_count = current + 1
                if row is None:
                    conn.execute(
                        """
                        INSERT INTO daily_usage (account_id, usage_date, request_count)
                        VALUES (?, ?, ?)
                        """,
                        (account_id, usage_date.isoformat(), next_count),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE daily_usage
                        SET request_count = ?
                        WHERE account_id = ? AND usage_date = ?
                        """,
                        (next_count, account_id, usage_date.isoformat()),
                    )
                snapshot = _usage_snapshot(
                    usage_date=usage_date,
                    request_count=next_count,
                    quota=quota,
                )
                conn.execute("COMMIT")
                return snapshot, True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_usage_snapshot(
        self,
        *,
        account_id: int,
        usage_date: date,
        quota: int,
    ) -> BetaUsageSnapshot:
        """Return the current UTC-day usage snapshot for one account."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT request_count FROM daily_usage WHERE account_id = ? AND usage_date = ?",
                (account_id, usage_date.isoformat()),
            ).fetchone()
            request_count = int(row["request_count"]) if row is not None else 0
            return _usage_snapshot(
                usage_date=usage_date,
                request_count=request_count,
                quota=quota,
            )

    def _initialize_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    github_user_id INTEGER NOT NULL UNIQUE,
                    github_login TEXT NOT NULL,
                    email TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    key_prefix TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS daily_usage (
                    account_id INTEGER NOT NULL,
                    usage_date TEXT NOT NULL,
                    request_count INTEGER NOT NULL,
                    PRIMARY KEY (account_id, usage_date),
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER,
                    event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_accounts_github_login ON accounts(github_login);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_active_account
                ON api_keys(account_id)
                WHERE revoked_at IS NULL;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _fetch_account_by_column(
        self,
        conn: sqlite3.Connection,
        column: str,
        value: Any,
    ) -> BetaAccount | None:
        row = conn.execute(
            _ACCOUNT_SELECT + f" WHERE {column} = ? LIMIT 1",
            (value,),
        ).fetchone()
        if row is None:
            return None
        return _account_from_row(row)

    def _require_account(self, conn: sqlite3.Connection, account_id: int) -> BetaAccount:
        account = self._fetch_account_by_column(conn, "a.id", account_id)
        if account is None:
            raise PolicyNIMError(f"Hosted beta account {account_id} does not exist.")
        return account

    def _insert_audit_event(
        self,
        conn: sqlite3.Connection,
        *,
        account_id: int | None,
        event_type: str,
        details: dict[str, object],
        now: datetime,
    ) -> None:
        conn.execute(
            """
            INSERT INTO audit_events (account_id, event_type, details_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                account_id,
                event_type,
                json.dumps(details, sort_keys=True),
                _iso_utc(now),
            ),
        )


def _begin_immediate(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value))


def _account_from_row(row: sqlite3.Row) -> BetaAccount:
    created_at = _parse_datetime(row["created_at"])
    last_login_at = _parse_datetime(row["last_login_at"])
    if created_at is None or last_login_at is None:
        raise PolicyNIMError("Account row is missing required timestamps.")

    status_value = str(row["status"])
    if status_value not in {_ACTIVE_STATUS, _SUSPENDED_STATUS}:
        raise PolicyNIMError(f"Unsupported beta account status: {status_value}")

    return BetaAccount(
        account_id=int(row["account_id"]),
        github_user_id=int(row["github_user_id"]),
        github_login=str(row["github_login"]),
        email=str(row["email"]) if row["email"] is not None else None,
        status=cast(BetaAccountStatus, status_value),
        created_at=created_at,
        last_login_at=last_login_at,
        api_key_prefix=str(row["api_key_prefix"]) if row["api_key_prefix"] is not None else None,
        api_key_created_at=_parse_datetime(row["api_key_created_at"]),
    )


def _usage_snapshot(
    *,
    usage_date: date,
    request_count: int,
    quota: int,
) -> BetaUsageSnapshot:
    remaining = quota - request_count
    return BetaUsageSnapshot(
        usage_date=usage_date,
        request_count=request_count,
        quota=quota,
        remaining=max(0, remaining),
    )
