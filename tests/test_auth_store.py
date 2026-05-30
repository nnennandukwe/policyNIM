"""Tests for the hosted beta SQLite auth store."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

from policynim.storage import AuthStore


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
