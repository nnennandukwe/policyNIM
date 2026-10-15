"""Public workflow proofs for pinned, explicit local policy composition."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from policynim.errors import InvalidPolicyDocumentError
from policynim.interfaces.cli import app
from policynim.services.catalog import build_policy_catalog
from policynim.services.ingest import IngestService
from policynim.services.inventory import inspect_source_inventory
from policynim.services.profiles import (
    DeclaredConflict,
    Exclusion,
    PolicyProfile,
    Replacement,
    diff_profile,
    initialize_profile,
    read_profile,
    resolve_profile,
    serialize_profile,
    update_profile,
)
from policynim.settings import Settings, get_settings
from policynim.storage import create_index_store
from policynim.types import RuntimeRulesArtifact

NO_ENV: dict[str, Any] = {"_env_file": None}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Prevent credentials, personal profiles, and cached settings from entering tests."""
    for key in list(os.environ):
        if key.startswith("POLICYNIM_") or key == "NVIDIA_API_KEY":
            monkeypatch.delenv(key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def write_pack(root: Path, pack_id: str, ids: list[str]) -> Path:
    """Create a manifest-owned fixture pack whose policies each carry a runtime rule."""
    root.mkdir(parents=True, exist_ok=True)
    manifest = [f'schema_version = 1\npack_id = "{pack_id}"\nversion = "1.0.0"\n']
    for policy_id in ids:
        (root / f"{policy_id}.md").write_text(
            f"---\npolicy_id: {policy_id}\ntitle: Policy {policy_id}\ndomain: backend\n"
            "runtime_rules:\n  - action: shell_command\n    effect: block\n"
            f'    reason: Review {policy_id}.\n    command_regexes:\n      - "^{policy_id}$"\n'
            f"---\n# Policy {policy_id}\n\n## Rules\nApply {policy_id} before deployment.\n",
            encoding="utf-8",
        )
        manifest.append(f'[[policies]]\npolicy_id = "{policy_id}"\npath = "{policy_id}.md"\n')
    (root / "pack.toml").write_text("\n".join(manifest), encoding="utf-8")
    return root


@pytest.fixture
def profile_path(tmp_path: Path) -> Path:
    """Pin a baseline and an explicitly selected local pack beside an unselected private pack."""
    baseline = write_pack(tmp_path / "baseline", "public.baseline", ["A", "B", "C"])
    custom = write_pack(tmp_path / "custom", "team.custom", ["D", "E"])
    private = tmp_path / "private"
    private.mkdir()
    (private / "pack.toml").write_text("invalid TOML PRIVATE-CANARY")
    path = tmp_path / "profile.toml"
    initialize_profile(path, [baseline, custom], profile_id="team")
    return path


def rewrite_profile(path: Path, profile: PolicyProfile) -> None:
    """Model an operator's deliberate editable TOML change."""
    path.write_text(serialize_profile(profile), encoding="utf-8")


def target_digest(path: Path, policy_id: str) -> str:
    """Read the inspectable authored digest used to review one override target."""
    return resolve_profile(path).catalog.show(policy_id).target_digest


def compose(path: Path) -> None:
    """Add one replacement and one exclusion while retaining two baseline policies."""
    a_digest, b_digest = target_digest(path, "A"), target_digest(path, "B")
    profile, _ = read_profile(path)
    rewrite_profile(
        path,
        profile.model_copy(
            update={
                "exclusions": [
                    Exclusion(target_id="B", expected_digest=b_digest, reason="No browser tier.")
                ],
                "replacements": [
                    Replacement(
                        target_id="A",
                        expected_digest=a_digest,
                        replacement_id="D",
                        reason="Use the reviewed local practice.",
                    )
                ],
            }
        ),
    )


def test_baseline_plus_custom_ignores_unselected_private_pack(profile_path: Path) -> None:
    """An invalid private neighbor never enters discovery or source-only output."""
    resolved = resolve_profile(profile_path)
    assert {document.metadata.policy_id for document in resolved.documents} == {
        "A",
        "B",
        "C",
        "D",
        "E",
    }
    assert "PRIVATE-CANARY" not in resolved.catalog.model_dump_json()
    settings = Settings(**NO_ENV, profile=profile_path)
    assert build_policy_catalog(settings=settings) == resolved.catalog
    inventory = inspect_source_inventory(settings)
    assert inventory.policy_ids == ["A", "B", "C", "D", "E"]
    assert inventory.catalog_digest == resolved.catalog.digest
    assert inventory.profile_id == "team"
    assert inventory.root is None
    assert inventory.profile_path == profile_path
    assert inventory.roots == [profile_path.parent / "baseline", profile_path.parent / "custom"]


def test_composition_flows_through_real_index_retrieval_and_runtime_rules(
    profile_path: Path,
) -> None:
    """Preview and actual SQLite publication agree; displaced rules can't reach the runtime."""
    from test_index_identity import SpyEmbedder

    compose(profile_path)
    resolved = resolve_profile(profile_path)
    assert resolved.catalog.authored_count == 5
    assert resolved.catalog.effective_count == 3
    assert resolved.catalog.show("A").state == "replaced"
    assert resolved.catalog.show("A").replacement_id == "D"
    assert resolved.catalog.show("B").state == "excluded"
    assert resolved.catalog.show("B").reason == "No browser tier."
    assert resolved.catalog.show("B").policy.provenance is not None
    settings = Settings(
        **NO_ENV, profile=profile_path, index_db_path=profile_path.parent / "index.sqlite3"
    )
    store = create_index_store(settings)
    rules = profile_path.parent / "rules.json"
    result = IngestService(
        embedder=SpyEmbedder(),
        index_store=store,
        corpus_root=profile_path.parent / "unused",
        embedding_model=settings.nvidia_embed_model,
        runtime_rules_artifact_path=rules,
        profile_path=profile_path,
    ).run()
    assert result.catalog_digest == resolved.catalog.digest
    assert result.corpus_path == result.profile_path == profile_path.as_posix()
    assert result.source_roots == [str(root) for root in resolved.roots]
    assert result.document_count == 3
    assert {chunk.policy.policy_id for chunk in store.list_chunks()} == {"C", "D", "E"}
    assert {chunk.policy.policy_id for chunk in store.search([1.0, 0.0], top_k=100)} == {
        "C",
        "D",
        "E",
    }
    artifact = RuntimeRulesArtifact.model_validate_json(rules.read_text())
    assert {rule.policy_id for rule in artifact.rules} == {"C", "D", "E"}
    assert inspect_source_inventory(settings).decisions == {"A": "replaced", "B": "excluded"}

    from collections.abc import Sequence

    from test_router_service import MockReranker

    from policynim.services.compiler import PolicyCompilerService
    from policynim.services.runtime_decision import RuntimeDecisionService
    from policynim.types import (
        CompileRequest,
        GeneratedCompiledPolicyDraft,
        PolicySelectionPacket,
        ScoredChunk,
        ShellCommandActionRequest,
    )

    class InspectingCompiler:
        """Observe real routed context at the model boundary without calling a provider."""

        def compile_policy_packet(
            self,
            request: CompileRequest,
            selection_packet: PolicySelectionPacket,
            context: Sequence[ScoredChunk],
        ) -> GeneratedCompiledPolicyDraft:
            """Reject any displaced policy reaching the compiler's provider input."""
            assert {chunk.policy.policy_id for chunk in context} == {"C", "D", "E"}
            return GeneratedCompiledPolicyDraft(insufficient_context=True)

    compiled = PolicyCompilerService(
        compiler=InspectingCompiler(),
        embedder=SpyEmbedder(),
        index_store=store,
        reranker=MockReranker(),
    ).compile(CompileRequest(task="Review deployment", top_k=10))
    assert {chunk.policy.policy_id for chunk in compiled.retained_context} == {"C", "D", "E"}
    runtime = RuntimeDecisionService(index_store=store, runtime_rules_artifact_path=rules)
    for inactive in ("A", "B"):
        decision = runtime.decide(
            ShellCommandActionRequest(
                kind="shell_command",
                task="Review deployment",
                cwd=profile_path.parent,
                command=[inactive],
            )
        )
        assert decision.decision == "allow"
        assert decision.matched_rules == []


@pytest.mark.parametrize(
    "case, diagnostic",
    [
        ("unknown", "Unknown target"),
        ("stale", "Stale target digest"),
        ("same_id", "cycles"),
        ("cycle", "cycles"),
        ("chain", "chains"),
        ("multiple", "multiple replacements"),
        ("contradiction", "Contradictory"),
        ("missing_replacement", "Unknown replacement"),
        ("excluded_replacement", "excluded replacements"),
    ],
)
def test_invalid_decisions_fail_before_embeddings_and_writes(
    profile_path: Path, case: str, diagnostic: str
) -> None:
    """Each single fault reaches the real ingestion gate and preserves absent destinations."""
    from test_index_identity import SpyEmbedder

    digest_a = target_digest(profile_path, "A")
    digest_b = target_digest(profile_path, "B")
    profile, _ = read_profile(profile_path)
    a = Replacement(target_id="A", expected_digest=digest_a, replacement_id="D", reason="Reviewed.")
    exclusions: list[Exclusion] = []
    replacements = [a]
    if case == "unknown":
        replacements = [a.model_copy(update={"target_id": "UNKNOWN"})]
    elif case == "stale":
        replacements = [a.model_copy(update={"expected_digest": "sha256:" + "0" * 64})]
    elif case == "same_id":
        replacements = [a.model_copy(update={"replacement_id": "A"})]
    elif case == "cycle":
        replacements = [
            a.model_copy(update={"replacement_id": "B"}),
            Replacement(
                target_id="B", expected_digest=digest_b, replacement_id="A", reason="Reviewed."
            ),
        ]
    elif case == "chain":
        replacements.append(
            Replacement(
                target_id="D",
                expected_digest=target_digest(profile_path, "D"),
                replacement_id="E",
                reason="Reviewed.",
            )
        )
    elif case == "multiple":
        replacements.append(a.model_copy(update={"replacement_id": "E"}))
    elif case == "contradiction":
        exclusions = [Exclusion(target_id="A", expected_digest=digest_a, reason="Reviewed.")]
    elif case == "missing_replacement":
        replacements = [a.model_copy(update={"replacement_id": "UNKNOWN"})]
    elif case == "excluded_replacement":
        exclusions = [
            Exclusion(
                target_id="D", expected_digest=target_digest(profile_path, "D"), reason="Reviewed."
            )
        ]
    rewrite_profile(
        profile_path,
        profile.model_copy(
            update={
                "exclusions": exclusions,
                "replacements": replacements,
            }
        ),
    )
    original = profile_path.read_bytes()
    settings = Settings(**NO_ENV, index_db_path=profile_path.parent / "out.sqlite3")
    rules = profile_path.parent / "out.json"
    embedder = SpyEmbedder()
    with pytest.raises(InvalidPolicyDocumentError, match=diagnostic):
        IngestService(
            embedder=embedder,
            index_store=create_index_store(settings),
            corpus_root=profile_path.parent / "unused",
            embedding_model="test",
            runtime_rules_artifact_path=rules,
            profile_path=profile_path,
        ).run()
    assert embedder.calls == 0
    assert not settings.index_db_path.exists()
    assert not rules.exists()
    assert profile_path.read_bytes() == original


def test_duplicate_effective_ids_never_use_directory_order(profile_path: Path) -> None:
    """Repeated IDs across distinct packs fail even when no override names them."""
    extra = write_pack(profile_path.parent / "duplicate", "team.duplicate", ["A"])
    with pytest.raises(InvalidPolicyDocumentError, match="Duplicate effective"):
        initialize_profile(
            profile_path.parent / "duplicate.toml",
            [profile_path.parent / "baseline", extra],
            profile_id="duplicate",
        )
    assert not (profile_path.parent / "duplicate.toml").exists()


def test_order_doesnt_decide_policy_selection(profile_path: Path) -> None:
    """Reordering explicit packs doesn't change effective content or decision states."""
    compose(profile_path)
    original = resolve_profile(profile_path)
    profile, _ = read_profile(profile_path)
    rewrite_profile(
        profile_path, profile.model_copy(update={"packs": list(reversed(profile.packs))})
    )
    reordered = resolve_profile(profile_path)
    assert reordered.documents == original.documents
    assert reordered.catalog.policies == original.catalog.policies


def test_update_diff_requires_explicit_review_and_preserves_invalidated_targets(
    profile_path: Path,
) -> None:
    """A changed target blocks ingestion and repinning until its override is reviewed."""
    compose(profile_path)
    policy = profile_path.parent / "baseline" / "A.md"
    policy.write_text(policy.read_text().replace("before deployment", "before every deployment"))
    original = profile_path.read_bytes()
    with pytest.raises(InvalidPolicyDocumentError, match="doesn't match its pin"):
        resolve_profile(profile_path)
    report, _, _ = diff_profile(profile_path)
    assert report.changed == ["A"]
    assert report.invalidated_decisions == ["A"]
    with pytest.raises(InvalidPolicyDocumentError, match="Stale target digest"):
        update_profile(profile_path, expected_digest=report.review_digest)
    assert profile_path.read_bytes() == original
    profile, _ = read_profile(profile_path)
    diff_output = CliRunner().invoke(
        app, ["profile", "diff", str(profile_path), "--format", "json"]
    )
    assert diff_output.exit_code == 0, diff_output.output
    current = json.loads(diff_output.stdout)["current_policies"]
    revised_a = next(entry for entry in current if entry["policy"]["policy_id"] == "A")
    assert "before every deployment" in revised_a["body"]
    reviewed = revised_a["target_digest"]
    rewrite_profile(
        profile_path,
        profile.model_copy(
            update={
                "replacements": [
                    profile.replacements[0].model_copy(update={"expected_digest": reviewed})
                ],
            }
        ),
    )
    report, _, _ = diff_profile(profile_path)
    assert report.invalidated_decisions == []
    update_profile(profile_path, expected_digest=report.review_digest)
    assert resolve_profile(profile_path).catalog.show("A").state == "replaced"


def test_cli_add_replace_exclude_diff_and_explicit_update(profile_path: Path, monkeypatch) -> None:
    """The public CLI works without credentials, provider calls, or Git writes."""
    compose(profile_path)
    runner = CliRunner()
    for command in ("validate", "preview"):
        result = runner.invoke(app, ["profile", command, str(profile_path), "--format", "json"])
        assert result.exit_code == 0, result.output
        assert "PRIVATE-CANARY" not in result.output
    policy = profile_path.parent / "custom" / "E.md"
    policy.write_text(policy.read_text().replace("before deployment", "after review"))
    original = profile_path.read_bytes()
    result = runner.invoke(app, ["profile", "diff", str(profile_path), "--format", "json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["changed"] == ["E"]
    assert profile_path.read_bytes() == original
    missing = runner.invoke(app, ["profile", "update", str(profile_path)])
    assert missing.exit_code == 2
    stale = runner.invoke(
        app,
        ["profile", "update", str(profile_path), "--expect-digest", "stale", "--format", "json"],
    )
    assert stale.exit_code == 1
    assert json.loads(stale.stdout)["status"] == "error"
    assert profile_path.read_bytes() == original
    updated = runner.invoke(
        app,
        [
            "profile",
            "update",
            str(profile_path),
            "--expect-digest",
            report["review_digest"],
            "--format",
            "json",
        ],
    )
    assert updated.exit_code == 0, updated.output
    monkeypatch.setenv("POLICYNIM_PROFILE", str(profile_path))
    result = runner.invoke(app, ["policies", "list", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["digest"] == resolve_profile(profile_path).catalog.digest
    assert not (profile_path.parent / ".git").exists()


def test_diff_reports_added_removed_and_revised_policy_meaning(profile_path: Path) -> None:
    """A new local release reports membership and provenance changes before adopting it."""
    baseline = profile_path.parent / "baseline"
    write_pack(baseline, "public.baseline", ["A", "C", "F"])
    report, _, _ = diff_profile(profile_path)
    assert report.added == ["F"]
    assert report.removed == ["B"]
    assert report.changed == []
    assert report.changed_packs == ["public.baseline"]
    manifest = baseline / "pack.toml"
    manifest.write_text(manifest.read_text().replace('version = "1.0.0"', 'version = "1.0.1"'))
    report, _, _ = diff_profile(profile_path)
    assert report.changed == ["A", "C"]


@pytest.mark.parametrize("fault", ["source_change", "profile_edit", "interrupt", "write_failure"])
def test_update_failure_keeps_profile_and_cleans_staging(
    profile_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Inject failure before replacement through the real update writer."""
    from policynim.services import profiles

    report, _, _ = diff_profile(profile_path)
    original = profile_path.read_bytes()
    real_diff = profiles.diff_profile
    calls = 0

    def inject(path: Path):
        """Change one guarded input or terminate at the second validation boundary."""
        nonlocal calls
        calls += 1
        if calls == 2:
            if fault == "source_change":
                policy = path.parent / "baseline" / "A.md"
                policy.write_text(policy.read_text() + "\nNew requirement.\n")
            elif fault == "profile_edit":
                path.write_bytes(original + b"\n# Retain this operator note.\n")
            elif fault == "interrupt":
                raise KeyboardInterrupt
        return real_diff(path)

    def fail_replace(*args):
        """Simulate a destination write failure after successful staging."""
        raise OSError("injected destination failure")

    monkeypatch.setattr(profiles, "diff_profile", inject)
    if fault == "write_failure":
        monkeypatch.setattr(profiles.os, "replace", fail_replace)
    with pytest.raises((InvalidPolicyDocumentError, KeyboardInterrupt, OSError)):
        update_profile(profile_path, expected_digest=report.review_digest)
    expected = (
        original + b"\n# Retain this operator note.\n" if fault == "profile_edit" else original
    )
    assert profile_path.read_bytes() == expected
    assert list(profile_path.parent.glob(".profile.toml.*.tmp")) == []


def test_declared_conflicts_and_exact_runtime_matcher_conflicts(profile_path: Path) -> None:
    """Structural validation rejects explicit conflicts without claiming semantic analysis."""
    profile, _ = read_profile(profile_path)
    rewrite_profile(
        profile_path,
        profile.model_copy(
            update={
                "conflicts": [DeclaredConflict(left="A", right="B", reason="Practices conflict.")],
            }
        ),
    )
    with pytest.raises(InvalidPolicyDocumentError, match="Declared conflict"):
        resolve_profile(profile_path)
    rewrite_profile(profile_path, profile)
    policy = profile_path.parent / "custom" / "D.md"
    policy.write_text(
        policy.read_text().replace('"^D$"', '"^A$"').replace("effect: block", "effect: confirm")
    )
    report, _, _ = diff_profile(profile_path)
    assert "Conflicting runtime effects" in (report.validation_error or "")
    with pytest.raises(InvalidPolicyDocumentError, match="Conflicting runtime effects"):
        update_profile(profile_path, expected_digest=report.review_digest)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", "1"),
        ("schema_version", 2),
        ("profile_id", ""),
        ("profile_id", "invalid/name"),
        ("packs", []),
        ("packs", "not a list"),
        ("exclusions", "not a list"),
    ],
)
def test_profile_schema_rejects_invalid_values(
    profile_path: Path, field: str, value: object
) -> None:
    """Exercise strict configuration types and constrained values against a valid control."""
    profile, _ = read_profile(profile_path)
    values = profile.model_dump()
    assert PolicyProfile.model_validate(values) == profile
    values[field] = value
    with pytest.raises(ValidationError):
        PolicyProfile.model_validate(values)


def test_settings_profile_selection_and_legacy_directory_compatibility(
    profile_path: Path, monkeypatch
) -> None:
    """Environment and direct construction agree; ambiguous or empty selections fail."""
    monkeypatch.setenv("POLICYNIM_PROFILE", str(profile_path))
    assert get_settings().profile == profile_path
    assert Settings(**NO_ENV, profile=profile_path).profile == profile_path
    for invalid in ("", " ", [], {}):
        with pytest.raises(ValidationError):
            Settings.model_validate({"profile": invalid})
    with pytest.raises(ValidationError, match="Set only one"):
        Settings(**NO_ENV, profile=profile_path, corpus_dir=profile_path.parent / "baseline")
    monkeypatch.delenv("POLICYNIM_PROFILE")
    legacy = build_policy_catalog(
        settings=Settings(**NO_ENV, corpus_dir=profile_path.parent / "baseline")
    )
    assert legacy.effective_count == 3
    assert legacy.profile_id is None


def test_update_keeps_manual_comments_and_decision_format(profile_path: Path) -> None:
    """Repinning preserves user-authored notes rather than serializing the whole document."""
    compose(profile_path)
    original = profile_path.read_text()
    original = "# Operator release notes.\n" + original.replace(
        'reason = "No browser tier."', 'reason = "No browser tier." # Retain this decision note.'
    ).replace('version = "1.0.0"', 'version = "1.0.0" # Reviewed release.')
    profile_path.write_text(original + "\n# End of notes.\n")
    custom = profile_path.parent / "custom"
    write_pack(custom, "team.custom", ["D", "F"])
    report, _, _ = diff_profile(profile_path)
    update_profile(profile_path, expected_digest=report.review_digest)
    updated = profile_path.read_text()
    for note in (
        "# Operator release notes.",
        "# Retain this decision note.",
        "# Reviewed release.",
        "# End of notes.",
    ):
        assert note in updated
    assert resolve_profile(profile_path).catalog.show("F").state == "active"
    assert "# Removed policy pin:" in updated


@pytest.mark.parametrize("source", ["escape", "missing", "remote", "repeat"])
def test_profile_paths_fail_closed(profile_path: Path, source: str) -> None:
    """Selected manifest members remain bounded; aliases and remote paths never fetch inputs."""
    profile, _ = read_profile(profile_path)
    if source == "escape":
        member = profile_path.parent / "baseline" / "A.md"
        target = profile_path.parent / "outside.md"
        target.write_bytes(member.read_bytes())
        member.unlink()
        member.symlink_to(target)
    elif source == "missing":
        (profile_path.parent / "baseline" / "A.md").unlink()
    elif source == "remote":
        text = profile_path.read_text().replace(
            'path = "baseline"', 'path = "https://example.com/pack"'
        )
        profile_path.write_text(text)
    else:
        second = profile.packs[1].model_copy(update={"path": profile.packs[0].path})
        rewrite_profile(
            profile_path, profile.model_copy(update={"packs": [profile.packs[0], second]})
        )
    with pytest.raises(InvalidPolicyDocumentError):
        resolve_profile(profile_path)


def test_cli_init_is_offline_and_never_overwrites(profile_path: Path) -> None:
    """Public initialization pins selected packs and reports an existing destination safely."""
    runner = CliRunner()
    destination = profile_path.parent / "new.toml"
    result = runner.invoke(
        app,
        [
            "profile",
            "init",
            str(destination),
            "--pack",
            str(profile_path.parent / "baseline"),
            "--id",
            "example",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["profile"]["profile_id"] == "example"
    original = destination.read_bytes()
    duplicate = runner.invoke(app, ["profile", "init", str(destination), "--format", "json"])
    assert duplicate.exit_code == 1
    assert "already exists" in json.loads(duplicate.stdout)["message"]
    assert destination.read_bytes() == original


def test_ingest_cant_write_into_any_selected_pack(profile_path: Path) -> None:
    """Output guards cover every pack root rather than the unused legacy corpus argument."""
    from test_index_identity import SpyEmbedder

    from policynim.errors import IndexCompatibilityError

    embedder = SpyEmbedder()
    settings = Settings(**NO_ENV, index_db_path=profile_path.parent / "custom" / "index.sqlite3")
    with pytest.raises(IndexCompatibilityError, match="outside policy sources"):
        IngestService(
            embedder=embedder,
            index_store=create_index_store(settings),
            corpus_root=profile_path.parent / "unused",
            embedding_model="test",
            runtime_rules_artifact_path=profile_path.parent / "rules.json",
            profile_path=profile_path,
        ).run()
    assert embedder.calls == 0
    assert not settings.index_db_path.exists()


def test_provenance_only_update_invalidates_pinned_override(profile_path: Path) -> None:
    """A new baseline release can't keep an override valid just because Markdown bytes match."""
    compose(profile_path)
    policy = profile_path.parent / "baseline" / "A.md"
    original_policy = policy.read_bytes()
    manifest = policy.parent / "pack.toml"
    manifest.write_text(manifest.read_text().replace('version = "1.0.0"', 'version = "1.0.1"'))
    report, _, _ = diff_profile(profile_path)
    assert report.invalidated_decisions == ["A", "B"]
    assert policy.read_bytes() == original_policy
    original = profile_path.read_bytes()
    with pytest.raises(InvalidPolicyDocumentError, match="Stale target digest"):
        update_profile(profile_path, expected_digest=report.review_digest)
    assert profile_path.read_bytes() == original


@pytest.mark.parametrize(
    "section,field,value",
    [
        ("packs", "path", "https://example.com/remote"),
        ("packs", "pack_id", "INVALID/ID"),
        ("packs", "version", "latest"),
        ("packs", "digest", "invalid"),
        ("packs", "policies", {}),
        ("exclusions", "target_id", " "),
        ("exclusions", "expected_digest", "invalid"),
        ("exclusions", "reason", " "),
        ("replacements", "replacement_id", " "),
        ("conflicts", "left", " "),
        ("conflicts", "right", 42),
        ("conflicts", "reason", " "),
    ],
)
def test_nested_config_validation(
    profile_path: Path, section: str, field: str, value: object
) -> None:
    """Invalid nested fields fail at the typed boundary with a valid profile control."""
    compose(profile_path)
    profile, _ = read_profile(profile_path)
    values = profile.model_dump()
    values["conflicts"] = [{"left": "A", "right": "B", "reason": "Reviewed conflict."}]
    assert PolicyProfile.model_validate(values)
    values[section][0][field] = value
    with pytest.raises(ValidationError):
        PolicyProfile.model_validate(values)


def test_cli_help_describes_public_profile_commands() -> None:
    """Every profile command remains discoverable without credentials or configuration."""
    runner = CliRunner()
    for command in ("init", "preview", "validate", "diff", "update"):
        result = runner.invoke(app, ["profile", command, "--help"])
        assert result.exit_code == 0
        assert "--format" in result.stdout
    update = runner.invoke(app, ["profile", "update", "--help"])
    assert "--expect-digest" in update.stdout
    assert "doesn't activate or rebuild" in update.stdout


def test_initialization_resolves_symlinked_parent_paths(tmp_path: Path) -> None:
    """macOS temporary-directory aliases mustn't produce broken relative pack paths."""
    real = tmp_path / "real" / "nested"
    real.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    root = write_pack(tmp_path / "baseline", "public.baseline", ["A"])
    path = alias / "profile.toml"
    initialize_profile(path, [root], profile_id="alias")
    assert resolve_profile(path).catalog.effective_count == 1
    assert resolve_profile(real / "profile.toml").catalog == resolve_profile(path).catalog


def test_member_swap_after_validation_cant_read_outside_pack(
    profile_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raced symlink after member inspection is rejected by the descriptor-backed reader."""
    from policynim.ingest import loader

    root = profile_path.parent / "baseline"
    outside = profile_path.parent / "outside.md"
    outside.write_text("PRIVATE-OUTSIDE-CANARY")
    original = loader.resolve_pack_members
    swapped = False

    def swap_member(selected_root, manifest):
        """Replace the member after the path validator returns its authorized names."""
        nonlocal swapped
        paths = original(selected_root, manifest)
        if not swapped:
            swapped = True
            paths[0].unlink()
            paths[0].symlink_to(outside)
        return paths

    monkeypatch.setattr(loader, "resolve_pack_members", swap_member)
    with pytest.raises(InvalidPolicyDocumentError, match="selected pack member"):
        loader.load_policy_documents(root)


def test_profile_updates_serialize_before_validation(
    profile_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A competing supported writer can't enter validation while another owns the lock."""
    import threading

    from policynim.services import profiles
    from policynim.storage.file_lock import exclusive_file_lock

    report, _, _ = diff_profile(profile_path)
    attempted, entered = threading.Event(), threading.Event()
    failures: list[BaseException] = []
    original = profiles.diff_profile

    def observe(path: Path):
        """Record entry into the protected validation boundary."""
        entered.set()
        return original(path)

    def run_update() -> None:
        """Attempt the competing writer while retaining any worker exception."""
        attempted.set()
        try:
            update_profile(profile_path, expected_digest=report.review_digest)
        except BaseException as exc:
            failures.append(exc)

    monkeypatch.setattr(profiles, "diff_profile", observe)
    with exclusive_file_lock(profile_path.with_name(f".{profile_path.name}.lock")):
        thread = threading.Thread(target=run_update)
        thread.start()
        assert attempted.wait(2)
        assert not entered.wait(0.1)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not failures
    assert entered.is_set()


@pytest.mark.parametrize(
    "policy_id", ["security=baseline", "security#baseline", "security[baseline]"]
)
def test_updates_preserve_quoted_policy_ids(tmp_path: Path, policy_id: str) -> None:
    """Generated quoted pin keys stay valid when IDs contain TOML punctuation."""
    root = write_pack(tmp_path / "pack", "team.special", [policy_id])
    path = tmp_path / "profile.toml"
    initialize_profile(path, [root], profile_id="special")
    path.write_text(path.read_text() + "\n# Retain notes with = and # punctuation.\n")
    policy = root / f"{policy_id}.md"
    policy.write_text(policy.read_text() + "\nReviewed requirement.\n")
    report, _, _ = diff_profile(path)
    assert report.changed == [policy_id]
    update_profile(path, expected_digest=report.review_digest)
    assert resolve_profile(path).catalog.show(policy_id).state == "active"
    assert "# Retain notes with = and # punctuation." in path.read_text()


def test_profile_init_accepts_a_pack_without_a_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows cross-drive selections keep an explicit absolute pin instead of failing."""
    from policynim.services import profiles

    root = write_pack(tmp_path / "pack", "team.cross-drive", ["A"])
    path = tmp_path / "config" / "profile.toml"

    def cross_drive(*args):
        """Simulate the standard library's cross-drive relative-path rejection."""
        raise ValueError("Different drive roots.")

    monkeypatch.setattr(profiles.os.path, "relpath", cross_drive)
    initialize_profile(path, [root], profile_id="cross-drive")
    profile, _ = read_profile(path)
    assert profile.packs[0].path == root.resolve().as_posix()
    assert resolve_profile(path).catalog.effective_count == 1


@pytest.mark.parametrize("value", ["//attacker/share", r"\\attacker\share", r"\\?\C:\packs"])
def test_network_or_device_pack_paths_fail_before_discovery(value: str) -> None:
    """Potential network inputs are rejected syntactically on every platform."""
    from policynim.services.profiles import _normalize_pack_path

    for windows in (False, True):
        with pytest.raises(InvalidPolicyDocumentError, match="network shares"):
            _normalize_pack_path(value, windows=windows)


def test_native_windows_pack_paths_normalize_without_network_access() -> None:
    """Pure path normalization handles native separators without opening a filesystem."""
    from policynim.services.profiles import _normalize_pack_path

    assert _normalize_pack_path(r"C:\packs\team", windows=True) == "C:/packs/team"
    assert _normalize_pack_path(r"packs\team", windows=True) == "packs/team"
    with pytest.raises(InvalidPolicyDocumentError, match="local directories"):
        _normalize_pack_path("C:relative-pack", windows=True)


def test_manifest_corpus_root_alias_keeps_legacy_source_paths(tmp_path: Path) -> None:
    """A deliberately selected local directory alias works with descriptor-backed member reads."""
    from policynim.ingest.loader import load_policy_documents

    root = write_pack(tmp_path / "actual", "team.alias", ["A"])
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    documents = load_policy_documents(alias)
    assert len(documents) == 1
    assert documents[0].source_path == "alias/A.md"
    assert build_policy_catalog(alias).show("A").path == "A.md"


def test_initialization_rejects_network_roots_before_pack_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public initializer rejects an explicit share before resolving or reading a pack."""
    from policynim.services import profiles

    def forbidden(*args):
        """Any filesystem pack read would violate this rejection test's boundary."""
        raise AssertionError("Pack I/O mustn't run for a network path.")

    monkeypatch.setattr(profiles, "_load_pack", forbidden)
    with pytest.raises(InvalidPolicyDocumentError, match="network shares"):
        initialize_profile(
            tmp_path / "profile.toml", [Path("//attacker/share")], profile_id="blocked"
        )
    assert not (tmp_path / "profile.toml").exists()


@pytest.mark.parametrize(
    "host,effect,blocked",
    [
        ("example.com", "confirm", True),
        ("example.org", "confirm", False),
        ("example.com", "block", False),
    ],
)
def test_host_conflicts_use_runtime_case_normalization(
    profile_path: Path, host: str, effect: str, blocked: bool
) -> None:
    """Host casing can't hide opposite effects from structural validation."""
    for policy_id, selected_host, selected_effect, pack in (
        ("A", "EXAMPLE.COM", "block", "baseline"),
        ("D", host, effect, "custom"),
    ):
        policy = profile_path.parent / pack / f"{policy_id}.md"
        policy.write_text(
            policy.read_text()
            .replace("action: shell_command", "action: http_request")
            .replace("command_regexes:", "url_host_patterns:")
            .replace(f'"^{policy_id}$"', f'"{selected_host}"')
            .replace("effect: block", f"effect: {selected_effect}")
        )
    report, _, _ = diff_profile(profile_path)
    assert bool(report.validation_error) is blocked
    if blocked:
        assert "Conflicting runtime effects" in (report.validation_error or "")
    else:
        update_profile(profile_path, expected_digest=report.review_digest)
        assert resolve_profile(profile_path).catalog.effective_count == 5


def test_reviewed_pack_relocation_persists_selected_path(profile_path: Path) -> None:
    """A Git-managed path edit remains selected through diff, repinning, and resolution."""
    from shutil import copytree

    relocated = profile_path.parent / "relocated"
    copytree(profile_path.parent / "custom", relocated)
    member = relocated / "E.md"
    member.write_text(member.read_text() + "\nReviewed local release.\n")
    profile, _ = read_profile(profile_path)
    profile.packs[1].path = "relocated"
    rewrite_profile(profile_path, profile)
    report, _, _ = diff_profile(profile_path)
    assert report.changed == ["E"]
    update_profile(profile_path, expected_digest=report.review_digest)
    updated, _ = read_profile(profile_path)
    assert updated.packs[1].path == "relocated"
    assert resolve_profile(profile_path).roots[1] == relocated


def test_update_preserves_multiline_operator_decisions(profile_path: Path) -> None:
    """TOML-looking text inside a multiline reason mustn't be interpreted as profile structure."""
    compose(profile_path)
    reason = 'No browser tier.\n[[packs]]\nversion = "review notes"\nRetain this decision.'
    original = profile_path.read_text().replace(
        'reason = "No browser tier."', "reason = '''" + reason + "'''"
    )
    profile_path.write_text(original)
    report, _, _ = diff_profile(profile_path)
    update_profile(profile_path, expected_digest=report.review_digest)
    updated, _ = read_profile(profile_path)
    assert updated.exclusions[0].reason == reason
    assert "reason = '''" in profile_path.read_text()


def test_profile_factory_doesnt_require_bundled_sources(
    profile_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A selected custom profile can ingest even when bundled resources aren't available."""
    from test_index_identity import SpyEmbedder

    from policynim.services import ingest

    compose(profile_path)
    embedder = SpyEmbedder()

    def missing_bundle(*args):
        """Reject any attempt to resolve the unselected bundled corpus."""
        raise InvalidPolicyDocumentError("Bundled corpus unavailable.")

    def fixture_embedder(settings: Settings):
        """Inject the deterministic embedding boundary without credentials or network access."""
        return embedder

    monkeypatch.setattr(ingest, "resolve_corpus_root", missing_bundle)
    monkeypatch.setattr(ingest, "_create_default_embedder", fixture_embedder)
    settings = Settings(
        **NO_ENV,
        profile=profile_path,
        index_db_path=profile_path.parent / "profile.sqlite3",
        runtime_rules_artifact_path=profile_path.parent / "profile-rules.json",
    )
    with ingest.create_ingest_service(settings) as service:
        result = service.run()
    assert result.document_count == 3
    assert result.catalog_digest == resolve_profile(profile_path).catalog.digest
    assert embedder.calls == 1
    assert embedder.closed


def test_profile_serializer_round_trips_astral_policy_ids(tmp_path: Path) -> None:
    """Unicode policy identifiers stay valid TOML rather than becoming surrogate escapes."""
    policy_id = "policy-" + chr(0x1F989)
    root = write_pack(tmp_path / "pack", "team.unicode", [policy_id])
    path = tmp_path / "profile.toml"
    initialize_profile(path, [root], profile_id="unicode")
    report, _, _ = diff_profile(path)
    update_profile(path, expected_digest=report.review_digest)
    assert resolve_profile(path).catalog.show(policy_id).state == "active"


def test_profile_serializer_keeps_toml_1_0_escape_compatibility(profile_path: Path) -> None:
    """Escaped strings serialize for the stdlib TOML readers on both supported interpreters."""
    compose(profile_path)
    profile, _ = read_profile(profile_path)
    reason = "Reviewed " + chr(27) + " decision."
    profile.exclusions[0].reason = reason
    rewrite_profile(profile_path, profile)
    assert read_profile(profile_path)[0].exclusions[0].reason == reason
    report, _, _ = diff_profile(profile_path)
    update_profile(profile_path, expected_digest=report.review_digest)
    assert read_profile(profile_path)[0].exclusions[0].reason == reason
