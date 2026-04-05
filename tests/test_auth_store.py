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
