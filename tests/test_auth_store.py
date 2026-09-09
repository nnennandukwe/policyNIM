"""Tests for the hosted beta SQLite auth store."""

from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Barrier, Event

import pytest

from policynim.errors import PolicyNIMError
from policynim.storage import AuthStore
from policynim.storage.auth_store import ApiKeyQuotaResult
from policynim.types import BetaAccount, BetaUsageSnapshot


def _hash_api_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_auth_store_initializes_schema_idempotently(tmp_path) -> None:
    db_path = tmp_path / "auth.sqlite3"

    first = AuthStore(path=db_path)
    second = AuthStore(path=db_path)

    assert first.path == db_path
    assert second.path == db_path
    assert second.list_accounts() == []


def test_auth_store_upserts_github_accounts_without_duplication(tmp_path) -> None:
    store = AuthStore(path=tmp_path / "auth.sqlite3")
    created_at = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    updated_at = datetime(2026, 4, 5, 12, 5, tzinfo=UTC)

    first = store.upsert_account_from_github(
        github_user_id=123,
        github_login="octocat",
        email="first@example.com",
        now=created_at,
    )
    second = store.upsert_account_from_github(
        github_user_id=123,
        github_login="octocat-renamed",
        email="second@example.com",
        now=updated_at,
    )

    assert first.account_id == second.account_id
    assert second.github_login == "octocat-renamed"
    assert second.email == "second@example.com"
    assert len(store.list_accounts()) == 1


def test_auth_store_rotates_keys_and_revokes_previous_secret(tmp_path) -> None:
    store = AuthStore(path=tmp_path / "auth.sqlite3")
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    account = store.upsert_account_from_github(
        github_user_id=123,
        github_login="octocat",
        email="octocat@example.com",
        now=now,
    )

    store.rotate_api_key(
        account_id=account.account_id,
        key_prefix="pnm_first",
        key_hash=_hash_api_key("pnm_first_secret"),
        now=now,
    )
    rotated = store.rotate_api_key(
        account_id=account.account_id,
        key_prefix="pnm_second",
        key_hash=_hash_api_key("pnm_second_secret"),
        now=now,
    )

    assert store.authenticate_api_key(key_hash=_hash_api_key("pnm_first_secret")) is None
    authenticated = store.authenticate_api_key(key_hash=_hash_api_key("pnm_second_secret"))
    assert authenticated is not None
    assert authenticated.account_id == rotated.account_id
    assert authenticated.api_key_prefix == "pnm_second"


def test_auth_store_consumes_quota_atomically_until_limit(tmp_path) -> None:
    store = AuthStore(path=tmp_path / "auth.sqlite3")
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    account = store.upsert_account_from_github(
        github_user_id=123,
        github_login="octocat",
        email="octocat@example.com",
        now=now,
    )

    first, first_allowed = store.consume_daily_quota(
        account_id=account.account_id,
        usage_date=date(2026, 4, 5),
        quota=2,
        now=now,
    )
    second, second_allowed = store.consume_daily_quota(
        account_id=account.account_id,
        usage_date=date(2026, 4, 5),
        quota=2,
        now=now,
    )
    third, third_allowed = store.consume_daily_quota(
        account_id=account.account_id,
        usage_date=date(2026, 4, 5),
        quota=2,
        now=now,
    )

    assert first_allowed is True
    assert first.request_count == 1
    assert second_allowed is True
    assert second.request_count == 2
    assert third_allowed is False
    assert third.request_count == 2
    assert third.remaining == 0


def test_auth_store_lists_audit_events_newest_first_with_account_metadata(tmp_path) -> None:
    """Return hosted beta audit events newest first with account metadata."""
    store = AuthStore(path=tmp_path / "auth.sqlite3")
    first_time = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    second_time = datetime(2026, 4, 5, 12, 5, tzinfo=UTC)
    account = store.upsert_account_from_github(
        github_user_id=123,
        github_login="octocat",
        email="octocat@example.com",
        now=first_time,
    )
    store.rotate_api_key(
        account_id=account.account_id,
        key_prefix="pnm_first",
        key_hash=_hash_api_key("pnm_first_secret"),
        now=second_time,
    )

    events = store.list_audit_events(limit=10)

    assert [event.event_type for event in events] == ["api_key_rotated", "account_signup"]
    assert events[0].github_login == "octocat"
    assert events[0].account_status == "active"
    assert events[0].details == {"key_prefix": "pnm_first"}
    assert events[0].created_at == second_time


def test_auth_store_filters_audit_events_and_redacts_secret_details(tmp_path) -> None:
    """Filter hosted beta audit events and redact secret-bearing details."""
    store = AuthStore(path=tmp_path / "auth.sqlite3")
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    account = store.upsert_account_from_github(
        github_user_id=123,
        github_login="octocat",
        email="octocat@example.com",
        now=now,
    )
    other = store.upsert_account_from_github(
        github_user_id=456,
        github_login="hubot",
        email="hubot@example.com",
        now=now,
    )
    store.rotate_api_key(
        account_id=account.account_id,
        key_prefix="pnm_first",
        key_hash=_hash_api_key("pnm_first_secret"),
        now=now,
    )
    with store._connect() as conn:
        store._insert_audit_event(
            conn,
            account_id=other.account_id,
            event_type="operator_note",
            details={
                "api_key": "pnm_full_secret",
                "key_hash": _hash_api_key("pnm_full_secret"),
                "key_prefix": "pnm_safe_prefix",
                "nested": {"session_token": "token-secret"},
            },
            now=now,
        )

    events = store.list_audit_events(github_login="hubot", event_type="operator_note", limit=1)

    assert len(events) == 1
    assert events[0].github_login == "hubot"
    assert events[0].details == {
        "api_key": "[redacted]",
        "key_hash": "[redacted]",
        "key_prefix": "pnm_safe_prefix",
        "nested": {"session_token": "[redacted]"},
    }


def test_auth_store_rejects_malformed_audit_event_details(tmp_path) -> None:
    """Raise a controlled error when stored audit event details are malformed."""
    store = AuthStore(path=tmp_path / "auth.sqlite3")
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    account = store.upsert_account_from_github(
        github_user_id=123,
        github_login="octocat",
        email="octocat@example.com",
        now=now,
    )
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_events (account_id, event_type, details_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (account.account_id, "operator_note", "{not-json", now.isoformat()),
        )

    try:
        store.list_audit_events(event_type="operator_note")
    except PolicyNIMError as exc:
        assert "malformed details JSON" in str(exc)
    else:
        raise AssertionError("Expected malformed audit event details to fail.")


def test_auth_store_reset_for_tests_clears_existing_state(tmp_path) -> None:
    store = AuthStore(path=tmp_path / "auth.sqlite3")
    store.upsert_account_from_github(
        github_user_id=123,
        github_login="octocat",
        email="octocat@example.com",
        now=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
    )

    store.reset_for_tests()

    assert store.list_accounts() == []


def _account_with_key(store: AuthStore, now: datetime) -> BetaAccount:
    """Create one active account and a known key for admission tests."""
    account = store.upsert_account_from_github(
        github_user_id=123, github_login="octocat", email=None, now=now
    )
    return store.rotate_api_key(
        account_id=account.account_id,
        key_prefix="pnm_test",
        key_hash=_hash_api_key("pnm_test_secret"),
        now=now,
    )


@pytest.mark.parametrize("key_state", ["unknown", "revoked"])
def test_auth_store_rejects_invalid_keys_while_another_writer_holds_lock(
    tmp_path: Path, key_state: str
) -> None:
    """Reject invalid keys without waiting for writer admission or changing state."""
    store = AuthStore(path=tmp_path / "auth.sqlite3")
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    account = _account_with_key(store, now)
    key_hash = _hash_api_key("unknown_secret")
    if key_state == "revoked":
        store.revoke_active_key(account_id=account.account_id, now=now)
        key_hash = _hash_api_key("pnm_test_secret")
    initial_events = store.list_audit_events()

    with closing(store._connect()) as writer, ThreadPoolExecutor(max_workers=1) as executor:
        writer.execute("BEGIN IMMEDIATE")
        try:
            pending = executor.submit(
                store.consume_quota_for_api_key,
                key_hash=key_hash,
                usage_date=now.date(),
                quota=3,
                now=now,
            )
            result = pending.result(timeout=2)
            assert writer.in_transaction
        finally:
            writer.execute("ROLLBACK")

    assert result == ApiKeyQuotaResult(account=None, usage=None, quota_consumed=False)
    assert (
        store.get_usage_snapshot(
            account_id=account.account_id, usage_date=now.date(), quota=3
        ).request_count
        == 0
    )
    assert store.list_audit_events() == initial_events


@pytest.mark.parametrize("mutation", ["rotate", "revoke", "suspend"])
def test_auth_store_rechecks_candidate_before_consuming_quota(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str
) -> None:
    """Reject a key or account invalidated after the preliminary read completes."""
    store = AuthStore(path=tmp_path / "auth.sqlite3")
    operator_store = AuthStore(path=store.path)
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    account = _account_with_key(store, now)
    candidate_read = Event()
    continue_admission = Event()
    original_lookup = store._fetch_account_by_key_hash

    def paused_candidate(conn: sqlite3.Connection, key_hash: str) -> BetaAccount | None:
        """Expose the gap between the preliminary read and authoritative transaction."""
        result = original_lookup(conn, key_hash)
        if not conn.in_transaction:
            assert result is not None and result.status == "active"
            candidate_read.set()
            assert continue_admission.wait(timeout=5)
        return result

    monkeypatch.setattr(store, "_fetch_account_by_key_hash", paused_candidate)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            store.consume_quota_for_api_key,
            key_hash=_hash_api_key("pnm_test_secret"),
            usage_date=now.date(),
            quota=3,
            now=now,
        )
        try:
            assert candidate_read.wait(timeout=5)
            if mutation == "rotate":
                operator_store.rotate_api_key(
                    account_id=account.account_id,
                    key_prefix="pnm_replacement",
                    key_hash=_hash_api_key("replacement_secret"),
                    now=now,
                )
            elif mutation == "revoke":
                operator_store.revoke_active_key(account_id=account.account_id, now=now)
            else:
                operator_store.set_account_status(
                    account_id=account.account_id, status="suspended", now=now
                )
        finally:
            continue_admission.set()
        result = pending.result(timeout=5)

    assert result.quota_consumed is False
    assert result.usage is None
    if mutation == "suspend":
        assert result.account is not None and result.account.status == "suspended"
    else:
        assert result.account is None
    assert (
        store.get_usage_snapshot(
            account_id=account.account_id, usage_date=now.date(), quota=3
        ).request_count
        == 0
    )
    assert store.list_audit_events(event_type="quota_exceeded") == []


def test_auth_store_concurrent_admissions_preserve_quota_and_audit(tmp_path: Path) -> None:
    """Admit exactly the quota under contention and audit every exhausted request."""
    store = AuthStore(path=tmp_path / "auth.sqlite3")
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    account = _account_with_key(store, now)
    start = Barrier(12, timeout=5)

    def admit() -> ApiKeyQuotaResult:
        """Attempt admission on a separate SQLite connection with concurrent peers."""
        start.wait()
        return store.consume_quota_for_api_key(
            key_hash=_hash_api_key("pnm_test_secret"), usage_date=now.date(), quota=3, now=now
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        pending = [executor.submit(admit) for _ in range(12)]
        results = [future.result(timeout=10) for future in pending]

    consumed = [result for result in results if result.quota_consumed]
    exhausted = [result for result in results if not result.quota_consumed]
    assert len(consumed) == 3
    assert len(exhausted) == 9
    assert sorted(item.usage.request_count for item in consumed if item.usage is not None) == [
        1,
        2,
        3,
    ]
    assert all(item.usage is not None and item.usage.remaining == 0 for item in exhausted)
    assert all(item.account == account for item in results)
    assert (
        store.get_usage_snapshot(
            account_id=account.account_id, usage_date=now.date(), quota=3
        ).request_count
        == 3
    )
    events = store.list_audit_events(event_type="quota_exceeded")
    assert len(events) == 9
    assert all(
        event.details == {"usage_date": now.date().isoformat(), "request_count": 3}
        for event in events
    )


def test_auth_store_admission_commits_before_waiting_revocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Serialize a later revocation without cancelling the already admitted request."""
    store = AuthStore(path=tmp_path / "auth.sqlite3")
    operator_store = AuthStore(path=store.path)
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    account = _account_with_key(store, now)
    account_read = Event()
    continue_admission = Event()
    revocation_started = Event()
    original_lookup = store._fetch_account_by_key_hash
    original_connect = operator_store._connect

    def paused_lookup(conn: sqlite3.Connection, key_hash: str) -> BetaAccount | None:
        """Hold the admission transaction after reading the active key."""
        result = original_lookup(conn, key_hash)
        if conn.in_transaction:
            account_read.set()
            assert continue_admission.wait(timeout=5)
        return result

    def trace_revocation(statement: str) -> None:
        """Signal the competing writer's attempt to acquire the transaction lock."""
        if statement == "BEGIN IMMEDIATE":
            revocation_started.set()

    def traced_connect() -> sqlite3.Connection:
        """Observe the real competing transaction without replacing SQLite locking."""
        conn = original_connect()
        conn.set_trace_callback(trace_revocation)
        return conn

    monkeypatch.setattr(store, "_fetch_account_by_key_hash", paused_lookup)
    monkeypatch.setattr(operator_store, "_connect", traced_connect)
    with ThreadPoolExecutor(max_workers=2) as executor:
        admission = executor.submit(
            store.consume_quota_for_api_key,
            key_hash=_hash_api_key("pnm_test_secret"),
            usage_date=now.date(),
            quota=3,
            now=now,
        )
        try:
            assert account_read.wait(timeout=5)
            revocation = executor.submit(
                operator_store.revoke_active_key, account_id=account.account_id, now=now
            )
            assert revocation_started.wait(timeout=5)
            assert not revocation.done()
        finally:
            continue_admission.set()
        result = admission.result(timeout=5)
        revocation.result(timeout=5)

    assert result.quota_consumed is True
    assert result.account == account
    assert result.usage is not None and result.usage.request_count == 1
    assert store.authenticate_api_key(key_hash=_hash_api_key("pnm_test_secret")) is None
    after_revocation = store.consume_quota_for_api_key(
        key_hash=_hash_api_key("pnm_test_secret"), usage_date=now.date(), quota=3, now=now
    )
    assert after_revocation == ApiKeyQuotaResult(account=None, usage=None, quota_consumed=False)
    assert (
        store.get_usage_snapshot(
            account_id=account.account_id, usage_date=now.date(), quota=3
        ).request_count
        == 1
    )


@pytest.mark.parametrize("failure_stage", ["snapshot", "commit"])
def test_auth_store_failed_admission_rolls_back_quota(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_stage: str
) -> None:
    """Return no consumption result or quota charge when validation or commit fails."""
    store = AuthStore(path=tmp_path / "auth.sqlite3")
    now = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    account = _account_with_key(store, now)
    original_connect = store._connect

    def reject_snapshot(**kwargs: object) -> BetaUsageSnapshot:
        """Inject usage-snapshot validation failure after the quota mutation."""
        raise ValueError("usage snapshot validation failed")

    def reject_commit(
        action: int,
        argument: str | None,
        second_argument: str | None,
        database: str | None,
        trigger: str | None,
    ) -> int:
        """Reject COMMIT through SQLite while allowing its rollback."""
        if action == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def failing_connect() -> sqlite3.Connection:
        """Install the commit-failure hook on the admission connection."""
        conn = original_connect()
        conn.set_authorizer(reject_commit)
        return conn

    with monkeypatch.context() as patch:
        if failure_stage == "snapshot":
            patch.setattr("policynim.storage.auth_store._usage_snapshot", reject_snapshot)
            expected_error = ValueError
        else:
            patch.setattr(store, "_connect", failing_connect)
            expected_error = sqlite3.DatabaseError
        with pytest.raises(expected_error):
            store.consume_quota_for_api_key(
                key_hash=_hash_api_key("pnm_test_secret"), usage_date=now.date(), quota=3, now=now
            )

    assert (
        store.get_usage_snapshot(
            account_id=account.account_id, usage_date=now.date(), quota=3
        ).request_count
        == 0
    )
    recovered = store.consume_quota_for_api_key(
        key_hash=_hash_api_key("pnm_test_secret"), usage_date=now.date(), quota=3, now=now
    )
    assert recovered.quota_consumed is True
    assert recovered.account == account
    assert recovered.usage is not None and recovered.usage.request_count == 1
