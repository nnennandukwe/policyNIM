"""Local HTTP setup acceptance through real source publication and extractive services."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient
from test_extractive_guidance import ExactCompiler, PreserveRanking, SourceGenerator
from test_ingest_service import MockEmbedder
from test_policy_profiles import write_pack

from policynim.interfaces.onboarding import create_onboarding_app
from policynim.services.onboarding import OnboardingRequest, OnboardingService
from policynim.settings import Settings

NO_ENV: dict[str, Any] = {"_env_file": None}
ORIGIN = "http://127.0.0.1:8765"
HEADERS = {"X-Setup-Token": "fixture-token", "Origin": ORIGIN}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Use model doubles at provider seams while exercising actual services and SQLite."""
    from policynim.services import compiler, ingest, preflight, router

    for key in list(os.environ):
        if key.startswith("POLICYNIM_") or key == "NVIDIA_API_KEY":
            monkeypatch.delenv(key)
    monkeypatch.setattr(ingest, "_create_default_embedder", lambda settings: MockEmbedder())
    monkeypatch.setattr(
        router,
        "_create_default_router_components",
        lambda settings: (MockEmbedder(), PreserveRanking()),
    )
    monkeypatch.setattr(
        compiler, "_create_default_policy_compiler", lambda settings: ExactCompiler()
    )
    monkeypatch.setattr(preflight, "_create_default_generator", lambda settings: SourceGenerator())
    source = write_pack(tmp_path / "policies", "test.setup", ["A", "B"])
    settings = Settings(
        **NO_ENV,
        nvidia_api_key="synthetic-provider-key",
        nvidia_embed_model="test-embed",
        nvidia_base_url="https://example.invalid/v1",
        generations_dir=tmp_path / "generations",
        index_db_path=tmp_path / "unused.sqlite3",
        runtime_rules_artifact_path=tmp_path / "unused-rules.json",
    )
    destination = tmp_path / "new-config.env"
    service = OnboardingService(settings=settings, destination=destination)
    app = create_onboarding_app(service, token="fixture-token", port=8765)
    payload = OnboardingRequest(
        source_mode="directory",
        source_path=str(source),
        consent=True,
        generations_dir=str(settings.generations_dir),
        task="Review a data migration for interruption and recovery.",
    ).model_dump(mode="json")
    # SecretStr's response serialization is deliberately masked; request fixtures send blanks.
    for name in ("nvidia_api_key", "vector_api_key", "vector_password"):
        payload[name] = ""
    with TestClient(app, base_url=ORIGIN, headers=HEADERS) as client:
        yield client, payload, destination, source, service


def reviewed(client: TestClient, payload: dict) -> dict:
    """Bind subsequent actions to the actual source digest returned by preview."""
    response = client.post("/api/preview", json=payload)
    assert response.status_code == 200, response.text
    return {**payload, "review_digest": response.json()["review_digest"]}


def verified(client: TestClient, payload: dict) -> dict:
    """Complete build and source verification through the ordinary HTTP workflow."""
    payload = reviewed(client, payload)
    for action in ("build", "verify"):
        response = client.post("/api/" + action, json=payload)
        assert response.status_code == 200, response.text
    return payload


def test_full_setup_saves_restartable_private_configuration(workspace) -> None:
    """A fresh user reaches a source-backed result and reloads the same generation."""
    from contextlib import closing

    from policynim.services.index_store import create_index_store

    client, payload, destination, _, _ = workspace
    page = client.get("/")
    assert page.status_code == 200
    assert "fixture-token" not in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert client.get("/assets/setup.js").status_code == 200
    payload = reviewed(client, payload)
    probe = client.post("/api/probe", json=payload)
    assert probe.json() == {
        "status": "connected",
        "backend": "sqlite",
        "generation_verified": False,
    }
    built = client.post("/api/build", json=payload)
    assert built.status_code == 200, built.text
    answer = client.post("/api/verify", json=payload)
    assert answer.status_code == 200, answer.text
    result = answer.json()["result"]
    assert result["citations"] and result["implementation_guidance"] == []
    assert result["generation_id"] == built.json()["generation_id"]
    saved = client.post("/api/save", json=payload)
    assert saved.status_code == 200, saved.text
    assert "synthetic-provider-key" not in saved.text
    assert destination.is_file()
    if os.name != "nt":
        assert destination.stat().st_mode & 0o077 == 0
    options: dict[str, Any] = {"_env_file": destination}
    restored = Settings(**options)
    with closing(create_index_store(restored)) as store:
        store.validate_identity()
        assert store.generation_id == result["generation_id"]


@pytest.mark.parametrize("action", ["build", "verify", "save"])
def test_unreviewed_choices_cant_advance(workspace, action: str) -> None:
    """Every effectful operation requires a matching reviewed source/configuration digest."""
    client, payload, destination, _, _ = workspace
    response = client.post("/api/" + action, json=payload)
    assert response.status_code == 409
    assert "Preview again" in response.text
    assert not destination.exists()
    assert not (destination.parent / "generations" / "active.json").exists()


def test_source_or_credential_changes_invalidate_review(workspace) -> None:
    """Masked secrets and changed source files can't reuse a previous approval digest."""
    client, payload, destination, source, _ = workspace
    payload = reviewed(client, payload)
    changed = {**payload, "nvidia_api_key": "new-synthetic-key"}
    response = client.post("/api/build", json=changed)
    assert response.status_code == 409
    policy = source / "A.md"
    policy.write_text(policy.read_text() + "\nSource changed after preview.\n")
    response = client.post("/api/build", json=payload)
    assert response.status_code == 409
    assert not destination.exists()


def test_missing_consent_and_missing_verification_block_save(workspace) -> None:
    """Viewing a preview and completing a build have separate workflow gates."""
    client, payload, destination, _, _ = workspace
    payload = reviewed(client, payload)
    response = client.post("/api/build", json={**payload, "consent": False})
    assert response.status_code == 409 and "Approve" in response.text
    assert client.post("/api/build", json=payload).status_code == 200
    response = client.post("/api/save", json=payload)
    assert response.status_code == 409 and "Verify" in response.text
    assert not destination.exists()


def test_manual_configuration_is_preserved(workspace) -> None:
    """A file created between review and save is never overwritten by setup."""
    client, payload, destination, _, _ = workspace
    payload = verified(client, payload)
    destination.write_text("manual configuration\n")
    response = client.post("/api/save", json=payload)
    assert response.status_code == 409
    assert "wasn't overwritten" in response.text
    assert destination.read_text() == "manual configuration\n"


def test_topic_profile_survives_temporary_preview_and_restart(workspace) -> None:
    """Selected topic pins are persisted with absolute pack locations, without manual edits."""
    client, payload, destination, _, _ = workspace
    payload.update({"source_mode": "topics", "source_path": "", "topics": ["data"]})
    payload = verified(client, payload)
    response = client.post("/api/save", json=payload)
    assert response.status_code == 200, response.text
    options: dict[str, Any] = {"_env_file": destination}
    restored = Settings(**options)
    assert restored.profile is not None and restored.profile.is_file()
    from policynim.services.catalog import build_policy_catalog

    catalog = build_policy_catalog(settings=restored, source_only=True)
    assert {entry.policy.domain for entry in catalog.policies} == {"data"}


def test_host_origin_authorization_size_and_secret_rejections(workspace) -> None:
    """Local browser access still rejects cross-origin and malformed privileged requests."""
    client, payload, _, _, _ = workspace
    assert client.get("/api/status", headers={"X-Setup-Token": "wrong"}).status_code == 403
    assert client.get("/", headers={"Host": "attacker.test:8765"}).status_code == 403
    response = client.post(
        "/api/preview", json=payload, headers={"Origin": "https://attacker.test"}
    )
    assert response.status_code == 403
    response = client.post(
        "/api/preview", content="x" * 65537, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    response = client.post(
        "/api/preview", json={**payload, "vector_password": "secret\nINJECTED=1"}
    )
    assert response.status_code == 422 and "secret" not in response.text


def test_shared_hosted_choice_keeps_private_setup_outside_the_public_service(workspace) -> None:
    """Choosing shared hosting gives connection guidance and performs no private publication."""
    client, payload, destination, _, _ = workspace
    response = client.post("/api/preview", json={**payload, "deployment": "shared_hosted"})
    assert response.status_code == 409
    assert "quickstart --target hosted-mcp" in response.text
    assert not destination.exists()


def test_source_mutation_between_preview_check_and_capture_refuses_build(
    workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The publisher rechecks the reviewed catalog against its own captured source snapshot."""
    from policynim.services import onboarding

    client, payload, destination, source, _ = workspace
    payload = reviewed(client, payload)
    original = onboarding.create_ingest_service

    def changed(settings, **kwargs):
        """Change a source after the setup service checked its preview, before capture."""
        policy = source / "A.md"
        policy.write_text(policy.read_text() + "\nChanged at the publication boundary.\n")
        return original(settings, **kwargs)

    monkeypatch.setattr(onboarding, "create_ingest_service", changed)
    response = client.post("/api/build", json=payload)
    assert response.status_code == 409
    assert "sources changed after preview" in response.text
    assert not destination.exists()
    assert not (destination.parent / "generations" / "active.json").exists()


def test_postcommit_config_sync_failure_reports_saved_with_warning(
    workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory-sync failure after linking a new config can't claim that saving aborted."""
    import stat

    client, payload, destination, _, _ = workspace
    payload = verified(client, payload)
    original = os.fsync

    def fail_directory_sync(descriptor: int) -> None:
        """Retain file durability while injecting only the postcommit directory barrier."""
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("Synthetic directory sync failure")
        original(descriptor)

    monkeypatch.setattr(os, "fsync", fail_directory_sync)
    response = client.post("/api/save", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "saved" and response.json()["warnings"]
    assert destination.is_file()


def test_saved_credentials_preserve_literal_dollar_braces_and_quotes(workspace) -> None:
    """The actual saved configuration consumer mustn't expand a credential as an env template."""
    client, payload, destination, _, _ = workspace
    secret = "synthetic-${HOME}-'quoted'-\\value"
    payload["nvidia_api_key"] = secret
    payload = verified(client, payload)
    response = client.post("/api/save", json=payload)
    assert response.status_code == 200, response.text
    assert secret not in response.text
    options: dict[str, Any] = {"_env_file": destination}
    assert Settings(**options).nvidia_api_key == secret


def test_existing_env_files_still_expand_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Literal setup files don't change the existing configuration expansion contract."""
    monkeypatch.setenv("POLICYNIM_FIXTURE_SECRET", "expanded-fixture-value")
    path = tmp_path / "existing.env"
    path.write_text("NVIDIA_API_KEY='${POLICYNIM_FIXTURE_SECRET}'\n")
    options: dict[str, Any] = {"_env_file": path}
    assert Settings(**options).nvidia_api_key == "expanded-fixture-value"


def test_failed_topic_config_save_removes_only_its_new_profile(workspace) -> None:
    """A config collision doesn't leave a generated profile with no saved configuration."""
    client, payload, destination, _, _ = workspace
    payload.update({"source_mode": "topics", "source_path": "", "topics": ["data"]})
    payload = verified(client, payload)
    destination.write_text("manual config\n")
    response = client.post("/api/save", json=payload)
    assert response.status_code == 409
    assert destination.read_text() == "manual config\n"
    assert not destination.with_name(destination.name + ".profile.toml").exists()


def test_failed_save_preserves_a_concurrently_changed_profile(
    workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback can't remove a profile that changed after setup created it."""
    from policynim.services import onboarding

    client, payload, destination, _, _ = workspace
    payload.update({"source_mode": "topics", "source_path": "", "topics": ["data"]})
    payload = verified(client, payload)
    profile = destination.with_name(destination.name + ".profile.toml")
    original = onboarding._write_new_private

    def collision_after_manual_edit(path: Path, text: str):
        """Inject an external edit after profile commit and before failed config publication."""
        if path == destination:
            profile.write_text("manual profile changes\n")
            raise FileExistsError("Synthetic config collision")
        return original(path, text)

    monkeypatch.setattr(onboarding, "_write_new_private", collision_after_manual_edit)
    response = client.post("/api/save", json=payload)
    assert response.status_code == 409
    assert profile.read_text() == "manual profile changes\n"
    assert not destination.exists()
