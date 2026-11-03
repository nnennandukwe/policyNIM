"""Exercise key-generation form retries through real portal and SQLite boundaries."""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import AnyHttpUrl
from starlette.testclient import TestClient

from policynim.errors import InvalidApiKeySubmission
from policynim.interfaces import mcp as mcp_module
from policynim.services.beta_auth import BetaAuthService
from policynim.settings import Settings
from policynim.storage import AuthStore


@pytest.fixture
def portal(tmp_path, monkeypatch):
    """Sign into a disposable account while retaining real key issuance and storage."""
    settings = Settings(
        beta_signup_enabled=True,
        mcp_require_auth=True,
        beta_session_secret="submission-test-session-secret",
        beta_github_client_id="test-client",
        beta_github_client_secret="test-secret",
        mcp_public_base_url=AnyHttpUrl("https://testserver"),
        beta_auth_db_path=tmp_path / "auth.sqlite3",
    )
    store = AuthStore(path=settings.beta_auth_db_path)
    account = store.upsert_account_from_github(
        github_user_id=123, github_login="test-account", email=None, now=datetime.now(UTC)
    )
    service = BetaAuthService(store=store, settings=settings)
    monkeypatch.setattr(service, "complete_github_oauth", lambda **kwargs: account)
    monkeypatch.setattr(mcp_module, "create_beta_auth_service", lambda settings: service)
    app = mcp_module._build_streamable_http_app(settings)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/auth/github/start", follow_redirects=False)
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        client.get(f"/auth/github/callback?state={state}&code=test-code")
        yield client, service, store, account
    service.close()


def submission_from(response) -> dict[str, str]:
    """Read the browser's actual generation form, without manufacturing an action ID."""
    match = re.search(r'name="submission_id" value="([^"]+)"', response.text)
    assert match, "The generation form must carry a submission ID."
    return {"submission_id": match[1]}


def secret_from(response) -> str:
    """Extract the first successful response's disposable key for authorization checks."""
    match = re.search(r"pnm_[A-Za-z0-9_-]{32}", response.text)
    assert match, "The first successful response must reveal its key."
    return match[0]


def test_refresh_replays_submission_without_revoking_the_copied_key(portal) -> None:
    """A repeated browser POST must redirect without rotating or revealing another key."""
    client, service, store, account = portal
    form = submission_from(client.get("/beta"))
    generated = client.post("/beta/api-key/regenerate", data=form, follow_redirects=False)
    assert generated.status_code == 200
    secret = secret_from(generated)
    prefix = service.get_account(account.account_id).api_key_prefix
    for _ in range(3):
        replay = client.post("/beta/api-key/regenerate", data=form, follow_redirects=False)
        assert replay.status_code == 303
        assert replay.headers["location"] == "/beta"
        assert secret not in replay.text
        assert service.authenticate_api_key(token=secret).status == "authorized"
        dashboard = client.get(replay.headers["location"])
        assert secret not in dashboard.text
        assert service.get_account(account.account_id).api_key_prefix == prefix
    assert len(store.list_audit_events(event_type="api_key_rotated")) == 1


def test_fresh_submission_rotates_but_older_retries_preserve_the_latest_key(portal) -> None:
    """A deliberate second action must replace the first key; old retries stay harmless."""
    client, service, store, account = portal
    first_form = submission_from(client.get("/beta"))
    first = client.post("/beta/api-key/regenerate", data=first_form)
    first_secret = secret_from(first)
    second_form = submission_from(first)
    assert first_form != second_form
    second = client.post("/beta/api-key/regenerate", data=second_form)
    second_secret = secret_from(second)
    assert first_secret != second_secret
    for form in (first_form, second_form):
        retry = client.post("/beta/api-key/regenerate", data=form, follow_redirects=False)
        assert retry.status_code == 303
        assert service.authenticate_api_key(token=second_secret).status == "authorized"
    assert service.authenticate_api_key(token=first_secret).status == "unauthorized"
    assert len(store.list_audit_events(event_type="api_key_rotated")) == 2


def test_lost_success_response_retry_doesnt_rotate_again(portal, monkeypatch) -> None:
    """A render failure after commit must leave a retryable, consumed action."""
    client, service, store, account = portal
    form = submission_from(client.get("/beta"))
    renderer = mcp_module._render_beta_dashboard

    def fail_response(*args, **kwargs):
        """Lose the response after the real rotation has committed."""
        raise RuntimeError("simulated lost success response")

    monkeypatch.setattr(mcp_module, "_render_beta_dashboard", fail_response)
    with pytest.raises(RuntimeError, match="lost success response"):
        client.post("/beta/api-key/regenerate", data=form)
    prefix = service.get_account(account.account_id).api_key_prefix
    monkeypatch.setattr(mcp_module, "_render_beta_dashboard", renderer)
    retry = client.post("/beta/api-key/regenerate", data=form, follow_redirects=False)
    assert retry.status_code == 303
    assert service.get_account(account.account_id).api_key_prefix == prefix
    assert len(store.list_audit_events(event_type="api_key_rotated")) == 1


@pytest.mark.parametrize("form", [{}, {"submission_id": "bad"}, {"submission_id": "x" * 32}])
def test_missing_malformed_and_unknown_submissions_cant_rotate(portal, form) -> None:
    """Reject unissued form actions before mutating keys or audit records."""
    client, service, store, account = portal
    valid_form = submission_from(client.get("/beta"))
    secret = secret_from(client.post("/beta/api-key/regenerate", data=valid_form))
    invalid = client.post("/beta/api-key/regenerate", data=form)
    assert invalid.status_code == 400
    assert "no-store" in invalid.headers["cache-control"]
    assert service.authenticate_api_key(token=secret).status == "authorized"
    assert len(store.list_audit_events(event_type="api_key_rotated")) == 1


@pytest.mark.parametrize(
    "body",
    [
        "submission_id=" + "x" * 1024,
        "submission_id=" + "x" * 32 + "&submission_id=" + "x" * 32,
        "submission_id",
        "submission_id=%FF",
        "other=" + "x" * 32,
    ],
)
def test_invalid_form_encoding_and_duplicate_fields_cant_rotate(portal, body) -> None:
    """Reject oversized, duplicated and malformed form input without a mutation."""
    client, service, store, account = portal
    response = client.post(
        "/beta/api-key/regenerate",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 400
    assert store.list_audit_events(event_type="api_key_rotated") == []


def test_submission_is_bound_to_its_account_and_expiry(portal, monkeypatch) -> None:
    """Another account and an expired form must leave current credentials intact."""
    client, service, store, account = portal
    now = datetime(2026, 11, 3, tzinfo=UTC)
    monkeypatch.setattr(service, "_utc_now", lambda: now)
    form = submission_from(client.get("/beta"))
    other = store.upsert_account_from_github(
        github_user_id=456, github_login="another-test-account", email=None, now=now
    )
    with pytest.raises(InvalidApiKeySubmission):
        service.issue_api_key(account_id=other.account_id, submission_id=form["submission_id"])
    now += timedelta(minutes=30)
    expired = client.post("/beta/api-key/regenerate", data=form)
    assert expired.status_code == 400
    assert store.list_audit_events(event_type="api_key_rotated") == []
    fresh = submission_from(client.get("/beta"))
    assert client.post("/beta/api-key/regenerate", data=fresh).status_code == 200
    assert client.post("/beta/api-key/regenerate", data=form).status_code == 400
    assert len(store.list_audit_events(event_type="api_key_rotated")) == 1


def test_rollback_restores_submission_and_previous_key_before_retry(portal) -> None:
    """A storage failure must roll back action consumption, revocation, and insertion."""
    client, service, store, account = portal
    old_secret = secret_from(
        client.post("/beta/api-key/regenerate", data=submission_from(client.get("/beta")))
    )
    form = submission_from(client.get("/beta"))
    with closing(store._connect()) as connection:
        connection.execute("""
            CREATE TRIGGER fail_rotation BEFORE INSERT ON audit_events
            WHEN NEW.event_type = 'api_key_rotated'
            BEGIN SELECT RAISE(ABORT, 'simulated transaction failure'); END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="transaction failure"):
        client.post("/beta/api-key/regenerate", data=form)
    assert service.authenticate_api_key(token=old_secret).status == "authorized"
    assert len(store.list_audit_events(event_type="api_key_rotated")) == 1
    with closing(store._connect()) as connection:
        connection.execute("DROP TRIGGER fail_rotation")
    replacement = client.post("/beta/api-key/regenerate", data=form)
    assert replacement.status_code == 200
    assert service.authenticate_api_key(token=secret_from(replacement)).status == "authorized"
    assert service.authenticate_api_key(token=old_secret).status == "unauthorized"
    assert len(store.list_audit_events(event_type="api_key_rotated")) == 2


def test_submission_consumption_is_atomic_across_processes_and_restarts(portal) -> None:
    """Independent workers sharing only SQLite must admit a single rotation."""
    client, service, store, account = portal
    form = submission_from(client.get("/beta"))
    worker = """
import sys
from pathlib import Path
from policynim.errors import ApiKeySubmissionReplayed
from policynim.services.beta_auth import BetaAuthService
from policynim.settings import Settings
from policynim.storage import AuthStore
service = BetaAuthService(
    store=AuthStore(path=Path(sys.argv[1])),
    settings=Settings(_env_file=None, beta_session_secret="submission-test-session-secret")
)
print('ready', flush=True)
sys.stdin.readline()
try:
    service.issue_api_key(account_id=int(sys.argv[2]), submission_id=sys.argv[3])
except ApiKeySubmissionReplayed:
    print('replayed')
else:
    print('issued')
"""
    workers = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                worker,
                str(store.path),
                str(account.account_id),
                form["submission_id"],
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    try:
        for process in workers:
            assert process.stdout is not None
            assert process.stdout.readline().strip() == "ready"
        for process in workers:
            assert process.stdin is not None
            process.stdin.write("start\n")
            process.stdin.flush()
        outcomes = []
        for process in workers:
            output, errors = process.communicate(timeout=15)
            assert process.returncode == 0, errors
            outcomes.append(output.strip())
        assert sorted(outcomes) == ["issued", "replayed"]
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
                process.communicate()
    assert len(store.list_audit_events(event_type="api_key_rotated")) == 1
    retry = client.post("/beta/api-key/regenerate", data=form, follow_redirects=False)
    assert retry.status_code == 303


def test_credential_responses_dont_cache_or_put_secrets_in_cookies_or_urls(portal) -> None:
    """Keep full credentials in the first response body only, including GET refreshes."""
    client, service, store, account = portal
    dashboard = client.get("/beta")
    generated = client.post("/beta/api-key/regenerate", data=submission_from(dashboard))
    secret = secret_from(generated)
    for response in (dashboard, generated, client.get("/beta"), client.get("/beta")):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
        assert secret not in str(response.url)
        assert secret not in response.headers.get("set-cookie", "")
    assert service.authenticate_api_key(token=secret).status == "authorized"
    assert len(store.list_audit_events(event_type="api_key_rotated")) == 1


def test_dashboard_refreshes_dont_accumulate_pending_submissions(portal) -> None:
    """Repeated form reads must leave SQLite unchanged until a real rotation commits."""
    client, service, store, account = portal
    for _ in range(100):
        assert client.get("/beta").status_code == 200
    with closing(store._connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM api_key_submissions").fetchone()[0] == 0
    form = submission_from(client.get("/beta"))
    assert client.post("/beta/api-key/regenerate", data=form).status_code == 200
    for _ in range(100):
        assert client.get("/beta").status_code == 200
    with closing(store._connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM api_key_submissions").fetchone()[0] == 1


def test_preparing_the_next_form_cant_lose_a_committed_key(portal, monkeypatch) -> None:
    """A next-form preparation failure must occur before the current key is replaced."""
    client, service, store, account = portal
    first = client.post("/beta/api-key/regenerate", data=submission_from(client.get("/beta")))
    secret = secret_from(first)
    form = submission_from(first)
    create_submission = service.create_api_key_submission

    def fail_preparation(**kwargs):
        """Inject a failure at the exact preparation boundary found during review."""
        raise RuntimeError("simulated form preparation failure")

    monkeypatch.setattr(service, "create_api_key_submission", fail_preparation)
    with pytest.raises(RuntimeError, match="form preparation failure"):
        client.post("/beta/api-key/regenerate", data=form)
    assert service.authenticate_api_key(token=secret).status == "authorized"
    assert len(store.list_audit_events(event_type="api_key_rotated")) == 1
    monkeypatch.setattr(service, "create_api_key_submission", create_submission)
    assert client.post("/beta/api-key/regenerate", data=form).status_code == 200
    assert service.authenticate_api_key(token=secret).status == "unauthorized"


def test_equivalent_signature_encoding_cant_bypass_submission_replay(portal) -> None:
    """Alternative signature padding must reject or replay the same action safely."""
    client, service, store, account = portal
    form = submission_from(client.get("/beta"))
    secret = secret_from(client.post("/beta/api-key/regenerate", data=form))
    form["submission_id"] += "="
    replay = client.post("/beta/api-key/regenerate", data=form, follow_redirects=False)
    assert replay.status_code in (303, 400)
    assert service.authenticate_api_key(token=secret).status == "authorized"
    assert len(store.list_audit_events(event_type="api_key_rotated")) == 1


@pytest.mark.parametrize(
    "content_type",
    [
        "Application/X-WWW-Form-Urlencoded",
        "application/x-www-form-urlencoded; charset=UTF-8",
        "APPLICATION/X-WWW-FORM-URLENCODED ; charset=UTF-8",
    ],
)
def test_form_media_type_accepts_case_and_parameter_variants(portal, content_type) -> None:
    """Valid media-type capitalization and parameters mustn't reject an issued action."""
    from urllib.parse import urlencode

    client, service, store, account = portal
    form = submission_from(client.get("/beta"))
    response = client.post(
        "/beta/api-key/regenerate",
        content=urlencode(form),
        headers={"content-type": content_type},
    )
    assert response.status_code == 200
    assert service.authenticate_api_key(token=secret_from(response)).status == "authorized"
    assert len(store.list_audit_events(event_type="api_key_rotated")) == 1
