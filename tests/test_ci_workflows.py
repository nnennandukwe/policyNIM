"""Workflow contract checks for offline CI and opt-in live smoke gates."""

from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path

import pytest
import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
HOSTED_SMOKE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "hosted-smoke.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _workflow_files(directory: Path) -> tuple[Path, ...]:
    """Include every supported workflow extension, including newly added files."""
    return tuple(sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")]))


WORKFLOW_FILES = _workflow_files(REPO_ROOT / ".github/workflows")
NODE24_ACTION_MAJOR_COMMENTS = {
    "actions/attest": {"v4"},
    "actions/checkout": {"v5", "v6", "v7"},
    "actions/setup-python": {"v6", "v7"},
    "actions/upload-artifact": {"v6", "v7"},
    "actions/download-artifact": {"v7", "v8"},
    "astral-sh/setup-uv": {"v8"},
}


def _read_text(path: Path) -> str:
    """Read a workflow file as UTF-8 text."""
    return path.read_text(encoding="utf-8")


def _workflow_uses(path: Path) -> list[tuple[str, str, str | None]]:
    """Read actual YAML keys and refs, retaining source comments for approved versions."""
    text = _read_text(path)
    lines = text.splitlines()
    document = yaml.compose(text)
    results: list[tuple[str, str, str | None]] = []

    def visit(node: Node) -> None:
        """Traverse scalar uses keys regardless of YAML quoting or whitespace."""
        if isinstance(node, MappingNode):
            for key, value in node.value:
                if isinstance(key, ScalarNode) and key.value == "uses":
                    assert isinstance(value, ScalarNode), "Action refs must be scalar strings."
                    action, separator, ref = value.value.rpartition("@")
                    assert separator, "Remote action refs must name a revision."
                    version = re.search(r"#\s*(v[0-9][^\s#]*)", lines[value.start_mark.line])
                    results.append((action, ref, version.group(1) if version else None))
                visit(value)
        elif isinstance(node, SequenceNode):
            for item in node.value:
                visit(item)

    assert document is not None
    visit(document)
    return results


def _assert_workflow_pins(path: Path) -> None:
    """Reject mutable action refs, including the Docker-backed publisher."""
    for action, ref, version in _workflow_uses(path):
        if action == "pypa/gh-action-pypi-publish":
            assert path.resolve() == RELEASE_WORKFLOW.resolve()
            assert ref == "cef221092ed1bacb1cc03d23a2d87d1d172e277b"
            assert version == "v1.14.0"
            continue
        assert re.fullmatch(r"[0-9a-f]{40}", ref), (path, action, ref)
        assert version is not None, (path, action)
        assert version.split(".")[0] in NODE24_ACTION_MAJOR_COMMENTS[action], (
            path,
            action,
            version,
        )
    assert "ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION" not in _read_text(path)


def test_workflows_pin_node24_compatible_actions() -> None:
    """Keep all workflows on reviewed Node 24 action commits."""
    assert WORKFLOW_FILES
    for path in WORKFLOW_FILES:
        _assert_workflow_pins(path)


@pytest.mark.parametrize(
    ("reference", "version"),
    [
        ("a309ff8b426b58ec0e2a45f0f869d46889d02405", "v6.2.0"),
        ("5fda3b95a4ea91299a34e894583c3862153e4b97", "v7.0.0"),
    ],
)
def test_setup_python_accepts_reviewed_node24_majors(
    tmp_path: Path, reference: str, version: str
) -> None:
    path = tmp_path / "workflow.yml"
    path.write_text(f"uses: actions/setup-python@{reference} # {version}\n")
    _assert_workflow_pins(path)


@pytest.mark.parametrize("version", ["v1", "v2", "v3", "v4", "v5", "v8"])
def test_setup_python_rejects_unreviewed_majors(tmp_path: Path, version: str) -> None:
    path = tmp_path / "workflow.yml"
    path.write_text(f"uses: actions/setup-python@{'a' * 40} # {version}.0.0\n")
    with pytest.raises(AssertionError):
        _assert_workflow_pins(path)


@pytest.mark.parametrize("version", ["v6.2.0", "v7.0.0"])
@pytest.mark.parametrize("reference", ["main", "v6", "v7", "abc123", "A" * 40])
def test_setup_python_still_requires_full_commit_pins(
    tmp_path: Path, reference: str, version: str
) -> None:
    path = tmp_path / "workflow.yml"
    path.write_text(f"uses: actions/setup-python@{reference} # {version}\n")
    with pytest.raises(AssertionError):
        _assert_workflow_pins(path)


def test_ci_pytest_gate_excludes_all_live_markers() -> None:
    """Ensure normal CI cannot discover live or Docker-only tests by accident."""
    text = _read_text(CI_WORKFLOW)

    jobs = yaml.safe_load(text)["jobs"]
    pytest_commands = [
        step["run"]
        for name, job in jobs.items()
        if name != "docker-offline"
        for step in job["steps"]
        if "pytest" in step.get("run", "")
    ]
    assert pytest_commands
    for command in pytest_commands:
        assert 'pytest -q -m "not live and not docker_live"' in command
        assert "--no-sync" in command


def test_ci_container_job_only_selects_credential_free_offline_probes() -> None:
    """Keep the explicit container job separate from paid provider test discovery."""
    job = yaml.safe_load(_read_text(CI_WORKFLOW))["jobs"]["docker-offline"]
    assert job["env"]["NVIDIA_API_KEY"] == ""
    assert job["env"]["POLICYNIM_RUN_DOCKER_TESTS"] == "1"
    commands = [step["run"] for step in job["steps"] if "pytest" in step.get("run", "")]
    assert len(commands) == 1
    assert "--no-sync" in commands[0] and "-m docker_live" in commands[0]
    assert re.findall(r"tests/\S+", commands[0]) == ["tests/test_docker_build_live.py"]


def test_ci_checks_supported_python_versions_from_locked_dependencies() -> None:
    """Verify the SDK migration on both supported interpreters without resolving new versions."""
    text = _read_text(CI_WORKFLOW)
    assert 'python-version: ["3.11", "3.12"]' in text
    assert "--python ${{ matrix.python-version }}" in text
    sync_commands = re.findall(r"run: (.*uv sync.*)", text)
    assert sync_commands
    assert all("--locked" in command for command in sync_commands)


def test_hosted_smoke_workflow_is_manual_and_secret_gated() -> None:
    """Ensure the hosted smoke workflow is opt-in and requires deployed beta secrets."""
    text = _read_text(HOSTED_SMOKE_WORKFLOW)

    assert "workflow_dispatch:" in text
    assert "pull_request:" not in text
    assert "push:" not in text
    assert "POLICYNIM_BETA_MCP_URL: ${{ secrets.POLICYNIM_BETA_MCP_URL }}" in text
    assert "POLICYNIM_BETA_MCP_TOKEN: ${{ secrets.POLICYNIM_BETA_MCP_TOKEN }}" in text
    assert "Validate hosted smoke secrets" in text
    assert "pytest -q -m live tests/test_hosted_mcp_live.py" in text
    assert "permissions:\n  contents: read" in text


def test_hosted_smoke_workflow_uses_pinned_actions() -> None:
    """Ensure the hosted smoke workflow keeps GitHub actions pinned to SHAs."""
    text = _read_text(HOSTED_SMOKE_WORKFLOW)
    action_refs = re.findall(r"uses: [^@\n]+@([0-9a-f]{40})", text)

    assert len(action_refs) == 3


def test_release_workflow_runs_offline_verification_before_release_artifacts() -> None:
    """Ensure release automation builds from the same offline quality gate as CI."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        "workflow_dispatch:",
        "tags:",
        '- "v*.*.*"',
        "uv lock --check",
        "uv run --no-sync --python ${{ matrix.python-version }} ruff check .",
        "uv run --no-sync --python ${{ matrix.python-version }} pyright",
        (
            "uv run --no-sync --python ${{ matrix.python-version }} "
            'pytest -q -m "not live and not docker_live"'
        ),
        "uv build --out-dir dist",
        "wheel-smoke:",
        "standalone-build:",
        "publish-github-release:",
    ):
        assert token in text
    assert "tests/test_hosted_mcp_live.py" not in text
    assert "POLICYNIM_BETA_MCP_TOKEN" not in text


def test_release_workflow_uses_pinned_actions_and_trusted_pypi_publish() -> None:
    """Ensure release actions are pinned and PyPI publish uses OIDC, not secrets."""
    text = _read_text(RELEASE_WORKFLOW)
    uses_lines = re.findall(r"uses: [^\n]+", text)

    assert uses_lines
    for line in uses_lines:
        assert re.search(r"@[0-9a-f]{40}(?:\s+#.*)?$", line), line
    assert "id-token: write" in text
    assert "pypa/gh-action-pypi-publish@" in text
    assert "password:" not in text
    assert "TWINE_PASSWORD" not in text


def test_release_workflow_publishes_pypi_after_release_assets() -> None:
    """Do not let immutable PyPI files publish before release assets pass."""
    text = _read_text(RELEASE_WORKFLOW)

    publish_pypi_job = re.search(
        r"publish-pypi:\n(?P<body>.*?)(?=\n  [a-zA-Z0-9_-]+:|\Z)",
        text,
        re.S,
    )
    assert publish_pypi_job is not None
    body = publish_pypi_job.group("body")

    assert "needs:\n      - publish-github-release" in body
    assert "pypa/gh-action-pypi-publish@" in body
    assert text.index("publish-github-release:") < text.index("publish-pypi:")


def test_release_workflow_uploads_expected_install_artifacts() -> None:
    """Lock the public artifact contract used by curl and PowerShell installers."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        "policynim-${RELEASE_TAG}-linux-amd64.tar.gz",
        "policynim-${RELEASE_TAG}-darwin-arm64.tar.gz",
        "policynim-${RELEASE_TAG}-darwin-amd64.tar.gz",
        "policynim-${RELEASE_TAG}-windows-amd64.zip",
        "scripts/install.sh",
        "scripts/install.ps1",
        "SHA256SUMS",
        "gh release create",
        "--draft",
    ):
        assert token in text


def test_release_workflow_builds_macos_intel_standalone_artifact() -> None:
    """Restore macOS Intel as a first-class standalone release target."""
    text = _read_text(RELEASE_WORKFLOW)

    for token in (
        "os: macos-15-intel",
        "platform: darwin-amd64",
        'darwin-amd64) ASSET_NAME="policynim-${RELEASE_TAG}-darwin-amd64.tar.gz" ;;',
    ):
        assert token in text


def test_release_workflow_rejects_mismatched_manual_versions() -> None:
    """Prevent manual release dispatches from publishing inconsistent artifacts."""
    text = _read_text(RELEASE_WORKFLOW)

    assert 'project_version = project["project"]["version"]' in text
    assert 'requested_version = requested.removeprefix("v")' in text
    assert "requested_version != project_version" in text
    assert "Release version mismatch:" in text


def test_fresh_wheel_install_uses_locked_hashed_dependencies() -> None:
    """Fresh package evidence mustn't resolve new transitive versions at test time."""
    workflow = yaml.safe_load(_read_text(CI_WORKFLOW))
    command = next(
        step["run"]
        for step in workflow["jobs"]["package-offline"]["steps"]
        if "verify_installed_package.py" in step.get("run", "")
    )
    assert '"export", "--locked"' in command
    assert '"--require-hashes", "--no-deps"' in command
    assert '"--lockfile"' in command and '"--lock-digest"' in command
    assert '"--requirements"' in command


def test_package_evidence_round_trip_uses_pinned_download_after_upload() -> None:
    """Exercise artifact downloads on every offline package matrix combination."""
    jobs = yaml.safe_load(_read_text(CI_WORKFLOW))["jobs"]
    job = jobs["package-offline"]
    assert job["strategy"]["matrix"] == {
        "os": ["ubuntu-24.04", "windows-2022"],
        "python-version": ["3.11", "3.12"],
    }
    steps = job["steps"]
    upload = next(
        step for step in steps if step["name"] == "Retain installed-package verification evidence"
    )
    download = next(
        step for step in steps if step["name"] == "Download installed-package verification evidence"
    )
    verification = next(
        step for step in steps if step["name"] == "Verify installed-package evidence round trip"
    )
    assert steps.index(upload) < steps.index(download) < steps.index(verification)
    assert download["uses"] == (
        "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333"
    )
    assert upload["with"]["path"] == "package-evidence.json"
    assert download["with"] == {
        "name": upload["with"]["name"],
        "path": "downloaded-package-evidence",
    }
    assert verification["shell"] == "bash"
    assert "continue-on-error" not in job
    for step in (download, verification):
        assert "if" not in step
        assert "continue-on-error" not in step


@pytest.mark.parametrize(
    "downloaded",
    [b'{"verified": true}\n', b'{"verified": false}\n', b'{"verified": true}\r\n', None],
    ids=["identical", "changed-content", "changed-line-endings", "missing"],
)
def test_package_evidence_round_trip_checks_actual_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, downloaded: bytes | None
) -> None:
    """The workflow accepts identical receipts but rejects changed or missing files."""
    steps = yaml.safe_load(_read_text(CI_WORKFLOW))["jobs"]["package-offline"]["steps"]
    command = next(
        step["run"]
        for step in steps
        if step["name"] == "Verify installed-package evidence round trip"
    )
    script = command.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    code = compile(script, str(CI_WORKFLOW), "exec")
    monkeypatch.chdir(tmp_path)
    original = b'{"verified": true}\n'
    (tmp_path / "package-evidence.json").write_bytes(original)
    destination = tmp_path / "downloaded-package-evidence" / "package-evidence.json"
    destination.parent.mkdir()
    if downloaded is not None:
        destination.write_bytes(downloaded)

    if downloaded is None:
        with pytest.raises(FileNotFoundError):
            exec(code, {})
    elif downloaded != original:
        with pytest.raises(AssertionError, match="Downloaded package evidence differs"):
            exec(code, {})
        destination.write_bytes(original)
        exec(code, {})
    else:
        exec(code, {})


def test_artifact_downloads_keep_digest_mismatches_fatal() -> None:
    """CI and release downloads must retain v8's fail-closed digest behavior."""
    for path in (CI_WORKFLOW, RELEASE_WORKFLOW):
        jobs = yaml.safe_load(_read_text(path))["jobs"]
        downloads = [
            (job, step)
            for job in jobs.values()
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/download-artifact@")
        ]
        assert downloads
        for job, step in downloads:
            assert step["with"].get("digest-mismatch", "error") == "error"
            assert "continue-on-error" not in step
            assert "continue-on-error" not in job


def _release_uv_commands(script: str) -> list[list[str]]:
    """Inspect each shell command; reject wrapped uv invocations rather than guess."""
    commands = []
    for line in script.splitlines():
        if not re.search(r"\buv\s+(?:sync|run)\b", line):
            continue
        lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        current: list[str] = []
        for token in [*lexer, ";"]:
            if token and all(character in ";&|()" for character in token):
                if current:
                    if any(
                        part == "uv" or re.search(r"\buv\s+(?:sync|run)\b", part)
                        for part in current
                    ):
                        assert current[:1] == ["uv"], "Use direct uv commands in release gates."
                        commands.append(current)
                    current = []
            else:
                current.append(token)
    return commands


def _assert_locked_release_dependencies(workflow: dict) -> None:
    """Require locked setup and check flags within each executing shell command."""
    for name, groups in {"verify": ("test", "dev"), "standalone-build": ("release",)}.items():
        syncs = [
            tokens
            for step in workflow["jobs"][name]["steps"]
            for tokens in _release_uv_commands(step.get("run", ""))
            if tokens[:2] == ["uv", "sync"]
        ]
        assert syncs, f"{name} must install from the reviewed lockfile."
        for group in groups:
            assert any(
                "--locked" in tokens
                and any(
                    tokens[index : index + 2] == ["--group", group] for index in range(len(tokens))
                )
                for tokens in syncs
            )
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            for tokens in _release_uv_commands(step.get("run", "")):
                if tokens[:2] == ["uv", "sync"]:
                    assert "--locked" in tokens
                if tokens[:2] == ["uv", "run"]:
                    assert "--no-sync" in tokens


def test_release_uses_locked_dependencies_without_implicit_resync() -> None:
    """Release verification and standalone builds must use the reviewed lockfile."""
    _assert_locked_release_dependencies(yaml.safe_load(_read_text(RELEASE_WORKFLOW)))


@pytest.mark.parametrize("reference", ["main", "v5", "abc123", "A" * 40])
@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_action_reference_parser_doesnt_hide_mutable_refs(
    tmp_path: Path, reference: str, quote: str
) -> None:
    """Unpinned refs must reach the pin assertion, including quoted YAML values."""
    path = tmp_path / "workflow.yml"
    path.write_text(f"uses: {quote}actions/checkout@{reference}{quote} # v5.0.1\n")
    uses = _workflow_uses(path)
    assert uses == [("actions/checkout", reference, "v5.0.1")]
    assert re.fullmatch(r"[0-9a-f]{40}", uses[0][1]) is None


@pytest.mark.parametrize("key", ['"uses":', "uses :", "'uses':"])
def test_yaml_key_spelling_cant_hide_an_unpinned_action(tmp_path: Path, key: str) -> None:
    """Valid quoted or spaced YAML keys must still reach the pin assertion."""
    path = tmp_path / "workflow.yml"
    path.write_text(f"{key} actions/checkout@main # v5.0.1\n")
    with pytest.raises(AssertionError):
        _assert_workflow_pins(path)


def test_discovery_includes_new_workflow_files(tmp_path: Path) -> None:
    """A new workflow can't escape the common action checks."""
    for name in ("ci.yml", "new-delivery.yaml"):
        (tmp_path / name).write_text("uses: actions/checkout@main\n")
    assert {path.name for path in _workflow_files(tmp_path)} == {"ci.yml", "new-delivery.yaml"}


@pytest.mark.parametrize("ref", ["main", "v1.14.0"])
def test_publisher_exception_cant_be_used_outside_release(tmp_path: Path, ref: str) -> None:
    """The approved publisher exception doesn't apply to CI or other workflow files."""
    path = tmp_path / "ci.yml"
    path.write_text(f"uses: pypa/gh-action-pypi-publish@{ref}\n")
    with pytest.raises(AssertionError):
        _assert_workflow_pins(path)


@pytest.mark.parametrize("job", ["verify", "standalone-build"])
def test_replacing_locked_setup_with_unlocked_install_fails(job: str) -> None:
    """Build jobs can't remove locked setup while keeping a vacuous no-sync check."""
    workflow = yaml.safe_load(_read_text(RELEASE_WORKFLOW))
    for step in workflow["jobs"][job]["steps"]:
        if step.get("run", "").startswith("uv sync"):
            step["run"] = "python -m pip install ."
    with pytest.raises(AssertionError):
        _assert_locked_release_dependencies(workflow)


@pytest.mark.parametrize("job", ["verify", "standalone-build"])
def test_comment_flags_cant_satisfy_locked_setup(job: str) -> None:
    """A shell comment can't turn an unlocked command into a passing locked gate."""
    workflow = yaml.safe_load(_read_text(RELEASE_WORKFLOW))
    for step in workflow["jobs"][job]["steps"]:
        if step.get("run", "").startswith("uv sync"):
            step["run"] = step["run"].replace("--locked", "") + " # --locked"
    with pytest.raises(AssertionError):
        _assert_locked_release_dependencies(workflow)


@pytest.mark.parametrize("separator", ["&&", "||", ";", "|"])
@pytest.mark.parametrize("command", ["uv sync --group test", "uv run pytest -q"])
def test_chained_commands_cant_borrow_locked_flags(separator: str, command: str) -> None:
    """Every chained invocation must carry its own lock or no-sync guarantee."""
    workflow = yaml.safe_load(_read_text(RELEASE_WORKFLOW))
    for step in workflow["jobs"]["verify"]["steps"]:
        if step.get("run", "").startswith("uv sync"):
            step["run"] += f" {separator} {command}"
    with pytest.raises(AssertionError):
        _assert_locked_release_dependencies(workflow)


def test_shell_wrappers_cant_hide_unlocked_uv_commands() -> None:
    """The release gate doesn't assume a quoted shell script is harmless data."""
    with pytest.raises(AssertionError):
        _release_uv_commands('bash -c "uv sync --group test"')


def test_release_publication_joins_checked_artifact_evidence_before_upload() -> None:
    """Publication can't omit the source-bound manifest or archive-linked receipts."""
    jobs = yaml.safe_load(_read_text(RELEASE_WORKFLOW))["jobs"]
    publication = jobs["publish-github-release"]
    assert {"wheel-smoke", "standalone-build"} <= set(publication["needs"])
    commands = [step.get("run", "") for step in publication["steps"]]
    joined = next(
        index for index, command in enumerate(commands) if "scripts/release_manifest.py" in command
    )
    published = next(
        index for index, command in enumerate(commands) if "gh release create" in command
    )
    assert joined < published
    assert '--source-sha "$GITHUB_SHA"' in commands[joined]
    assert '--tag "$RELEASE_TAG"' in commands[joined]
    assert "uv run --no-sync" in commands[joined]
    assert any("uv sync --locked" in command for command in commands)
    assert any("astral-sh/setup-uv@" in step.get("uses", "") for step in publication["steps"])
    wheel = "\n".join(step.get("run", "") for step in jobs["wheel-smoke"]["steps"])
    assert '"export", "--locked"' in wheel
    assert '"--require-hashes", "--no-deps"' in wheel
    assert "verify_installed_package.py" in wheel
    names = [step["name"] for step in jobs["standalone-build"]["steps"]]
    assert names.index("Archive Unix standalone bundle") < names.index(
        "Verify standalone generation and transports"
    )
    assert names.index("Archive Windows standalone bundle") < names.index(
        "Verify standalone generation and transports"
    )
    assert names.index("Verify standalone generation and transports") < names.index(
        "Retain standalone verification evidence"
    )


def test_standalone_upload_excludes_committed_generation_sources() -> None:
    """A committed artifacts directory mustn't enter the standalone download set."""
    job = yaml.safe_load(_read_text(RELEASE_WORKFLOW))["jobs"]["standalone-build"]
    upload = next(step for step in job["steps"] if step["name"] == "Upload standalone bundle")
    assert upload["with"]["path"] == "standalone-archives/*"
    binding = next(
        step["run"]
        for step in job["steps"]
        if step["name"] == "Verify standalone generation and transports"
    )
    assert '(root / "standalone-archives").iterdir()' in binding


def test_standalone_verification_requires_requested_release_version() -> None:
    """A stale binary can't pass a version-tagged standalone build gate."""
    workflow = yaml.safe_load(_read_text(RELEASE_WORKFLOW))
    command = next(
        step["run"]
        for step in workflow["jobs"]["standalone-build"]["steps"]
        if "scripts/verify_standalone.py" in step.get("run", "")
    )
    assert '"--expected-version"' in command
    assert 'os.environ["RELEASE_TAG"].removeprefix("v")' in command


def test_release_validates_both_supported_python_runtimes() -> None:
    """Publication waits for full source and fresh wheel checks on Python3.11 and3.12."""
    jobs = yaml.safe_load(_read_text(RELEASE_WORKFLOW))["jobs"]
    for name in ["verify", "wheel-smoke"]:
        job = jobs[name]
        assert job["strategy"]["matrix"]["python-version"] == ["3.11", "3.12"]
        setup = next(step for step in job["steps"] if step["name"] == "Set up Python")
        assert setup["with"]["python-version"] == "${{ matrix.python-version }}"
    wheel = jobs["wheel-smoke"]
    evidence = next(
        step for step in wheel["steps"] if step["name"] == "Retain wheel verification evidence"
    )
    assert "matrix.python-version" in evidence["with"]["name"]
    assert "matrix.python-version" in evidence["with"]["path"]


def test_release_attests_and_verifies_every_asset_before_publication() -> None:
    """A checksum manifest alone can't establish the trusted release producer."""
    job = yaml.safe_load(_read_text(RELEASE_WORKFLOW))["jobs"]["publish-github-release"]
    names = [step["name"] for step in job["steps"]]
    assert (
        names.index("Generate release asset attestations")
        < names.index("Verify release asset attestations")
        < names.index("Create draft GitHub release")
    )
    assert job["permissions"]["id-token"] == "write"
    assert job["permissions"]["attestations"] == "write"
    attest = next(
        step for step in job["steps"] if step["name"] == "Generate release asset attestations"
    )
    assert attest["with"]["subject-checksums"] == "release-assets/SHA256SUMS"
    assert names.index("Check release tag source") < names.index(
        "Generate release asset attestations"
    )


def _assert_release_source_python_selection(job: dict) -> None:
    """Every uv source-check command must override the repository interpreter pin."""
    for step in job["steps"]:
        command = step.get("run", "")
        if command.startswith(("uv sync", "uv run")):
            assert "--python ${{ matrix.python-version }}" in command


def test_release_source_checks_override_local_python_pin() -> None:
    """Setting PATH alone can't change uv's project interpreter selection."""
    job = yaml.safe_load(_read_text(RELEASE_WORKFLOW))["jobs"]["verify"]
    _assert_release_source_python_selection(job)


def test_release_python_guard_detects_removed_override() -> None:
    """A matrix label can't hide a command that still selects Python3.11."""
    job = yaml.safe_load(_read_text(RELEASE_WORKFLOW))["jobs"]["verify"]
    step = next(step for step in job["steps"] if step.get("run", "").startswith("uv sync"))
    step["run"] = step["run"].replace(" --python ${{ matrix.python-version }}", "")
    with pytest.raises(AssertionError):
        _assert_release_source_python_selection(job)


def test_wheel_export_uses_the_running_matrix_interpreter() -> None:
    """Fresh wheel dependencies must use the interpreter that created their environment."""
    job = yaml.safe_load(_read_text(RELEASE_WORKFLOW))["jobs"]["wheel-smoke"]
    command = next(
        step["run"]
        for step in job["steps"]
        if step["name"] == "Verify fresh wheel from locked dependencies"
    )
    script = command.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    exports = [
        node
        for node in ast.walk(ast.parse(script))
        if isinstance(node, ast.List)
        and len(node.elts) >= 2
        and isinstance(node.elts[0], ast.Constant)
        and node.elts[0].value == "uv"
        and isinstance(node.elts[1], ast.Constant)
        and node.elts[1].value == "export"
    ]
    assert len(exports) == 1
    arguments = exports[0].elts
    selection = next(
        (
            index
            for index, node in enumerate(arguments)
            if isinstance(node, ast.Constant) and node.value == "--python"
        ),
        None,
    )
    assert selection is not None, "uv export must override the repository's Python pin"
    assert ast.dump(arguments[selection + 1]) == ast.dump(
        ast.parse("sys.executable", mode="eval").body
    )


def _assert_release_verification_dependencies(workflow: dict) -> None:
    """Keep helper dependencies in each isolated release verification environment."""
    jobs = workflow["jobs"]
    publication_syncs = [
        tokens
        for step in jobs["publish-github-release"]["steps"]
        for tokens in _release_uv_commands(step.get("run", ""))
        if tokens[:2] == ["uv", "sync"]
    ]
    assert len(publication_syncs) == 1
    assert any(
        publication_syncs[0][index : index + 2] == ["--group", "verification"]
        for index in range(len(publication_syncs[0]))
    ), "Manifest verification needs the locked verification group."
    wheel_command = next(
        step["run"]
        for step in jobs["wheel-smoke"]["steps"]
        if step["name"] == "Verify fresh wheel from locked dependencies"
    )
    script = wheel_command.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    exports = [
        node
        for node in ast.walk(ast.parse(script))
        if isinstance(node, ast.List)
        and len(node.elts) >= 2
        and isinstance(node.elts[0], ast.Constant)
        and node.elts[0].value == "uv"
        and isinstance(node.elts[1], ast.Constant)
        and node.elts[1].value == "export"
    ]
    assert len(exports) == 1
    arguments = [node.value if isinstance(node, ast.Constant) else None for node in exports[0].elts]
    assert any(
        arguments[index : index + 2] == ["--group", "verification"]
        for index in range(len(arguments))
    ), "Fresh wheel verification needs the locked verification group."


def test_release_verifiers_install_locked_helper_dependencies() -> None:
    """Runtime-only installs don't provide the packaging helpers used by release checks."""
    _assert_release_verification_dependencies(yaml.safe_load(_read_text(RELEASE_WORKFLOW)))


@pytest.mark.parametrize("job_name", ["wheel-smoke", "publish-github-release"])
def test_release_verifier_guard_rejects_missing_helper_group(job_name: str) -> None:
    """Removing helper dependencies from either release gate must block promotion."""
    workflow = yaml.safe_load(_read_text(RELEASE_WORKFLOW))
    for step in workflow["jobs"][job_name]["steps"]:
        if "run" in step:
            step["run"] = (
                step["run"]
                .replace(" --group verification", "")
                .replace(', "--group", "verification"', "")
            )
    with pytest.raises(AssertionError):
        _assert_release_verification_dependencies(workflow)


def test_standalone_archive_payload_runs_before_receipt_binding() -> None:
    """The actual compressed payload must run before evidence can authorize upload."""
    steps = yaml.safe_load(_read_text(RELEASE_WORKFLOW))["jobs"]["standalone-build"]["steps"]
    verification = next(
        i for i, step in enumerate(steps) if "scripts/verify_standalone.py" in step.get("run", "")
    )
    assert (
        next(i for i, step in enumerate(steps) if step["name"] == "Archive Unix standalone bundle")
        < verification
    )
    assert (
        next(
            i for i, step in enumerate(steps) if step["name"] == "Archive Windows standalone bundle"
        )
        < verification
    )
    assert '"--archive"' in steps[verification]["run"]
    assert not any(step["name"] == "Bind standalone receipt to archive" for step in steps)


def test_native_build_uses_managed_runtime_and_capability_guard() -> None:
    """Every native invocation must use the pinned managed build interpreter."""
    workflow = yaml.safe_load(_read_text(RELEASE_WORKFLOW))
    job = workflow["jobs"]["standalone-build"]
    assert job["env"]["NATIVE_PYTHON_VERSION"] == "3.11.13"
    commands = [step.get("run", "") for step in job["steps"]]
    install = next(command for command in commands if "uv python install" in command)
    assert "--managed-python" in install
    assert "${{ env.NATIVE_PYTHON_VERSION }}" in install
    sync = next(command for command in commands if command.startswith("uv sync"))
    assert "--locked" in sync and "--managed-python" in sync
    assert "--python ${{ env.NATIVE_PYTHON_VERSION }}" in sync
    guard = next(
        index for index, command in enumerate(commands) if "check_standalone_runtime.py" in command
    )
    build = next(
        index for index, command in enumerate(commands) if "pyinstaller --clean" in command
    )
    assert guard < build
    for index in (guard, build):
        assert "--no-sync" in commands[index] and "--managed-python" in commands[index]
        assert "--python ${{ env.NATIVE_PYTHON_VERSION }}" in commands[index]
    assert "--expected-python ${{ env.NATIVE_PYTHON_VERSION }}" in commands[guard]


def test_failed_native_verification_retains_existing_archive_only() -> None:
    """A failed verification must retain its archive without changing publisher inputs."""
    job = yaml.safe_load(_read_text(RELEASE_WORKFLOW))["jobs"]["standalone-build"]
    upload = next(step for step in job["steps"] if step["name"] == "Upload standalone bundle")
    assert "always()" in upload["if"]
    assert "hashFiles('standalone-archives/*')" in upload["if"]
    assert upload["with"]["name"] == "standalone-${{ matrix.platform }}"
    assert upload["with"]["path"] == "standalone-archives/*"
    assert not job.get("continue-on-error", False)
    assert not any(step.get("continue-on-error", False) for step in job["steps"])


def _publication_allowed(
    condition: str, *, build_only: bool, publish_pypi: bool, tagged: bool
) -> bool:
    """Evaluate only the boolean operators used by the selected publisher job gates."""
    expression = (
        condition.removeprefix("${{")
        .removesuffix("}}")
        .replace("inputs.build_only", str(build_only))
        .replace("inputs.publish_pypi", str(publish_pypi))
    )
    expression = expression.replace("startsWith(github.ref, 'refs/tags/v')", str(tagged)).replace(
        "github.event_name == 'workflow_dispatch'", str(not tagged)
    )
    expression = expression.replace("!", " not ").replace("&&", " and ").replace("||", " or ")

    def value(node: ast.AST) -> bool:
        """Reject unexpected expression forms instead of executing workflow text."""
        if isinstance(node, ast.Constant) and type(node.value) is bool:
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not value(node.operand)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            operands = [value(operand) for operand in node.values]
            return all(operands) if isinstance(node.op, ast.And) else any(operands)
        raise AssertionError("Unsupported publisher condition.")

    return value(ast.parse(expression.strip(), mode="eval").body)


def test_build_only_is_an_explicit_boolean_opt_in() -> None:
    """Manual validation must default to the existing release behavior."""
    workflow = yaml.safe_load(_read_text(RELEASE_WORKFLOW))
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert inputs["build_only"]["type"] == "boolean"
    assert inputs["build_only"]["default"] is False


@pytest.mark.parametrize("publish_pypi", [False, True])
@pytest.mark.parametrize("tagged", [False, True])
def test_build_only_cant_reach_either_publisher(publish_pypi: bool, tagged: bool) -> None:
    """Even contradictory publish flags mustn't sign, draft, tag or upload distributions."""
    jobs = yaml.safe_load(_read_text(RELEASE_WORKFLOW))["jobs"]
    for name in ("publish-github-release", "publish-pypi"):
        assert not _publication_allowed(
            jobs[name]["if"], build_only=True, publish_pypi=publish_pypi, tagged=tagged
        )
    for name in ("verify", "wheel-smoke", "standalone-build"):
        assert "build_only" not in jobs[name].get("if", "")


@pytest.mark.parametrize("publish_pypi", [False, True])
@pytest.mark.parametrize("tagged", [False, True])
def test_normal_release_publisher_behavior_is_preserved(publish_pypi: bool, tagged: bool) -> None:
    """Tag releases and ordinary manual drafts retain their previous publication rules."""
    jobs = yaml.safe_load(_read_text(RELEASE_WORKFLOW))["jobs"]
    assert _publication_allowed(
        jobs["publish-github-release"]["if"],
        build_only=False,
        publish_pypi=publish_pypi,
        tagged=tagged,
    )
    assert _publication_allowed(
        jobs["publish-pypi"]["if"], build_only=False, publish_pypi=publish_pypi, tagged=tagged
    ) == (tagged or publish_pypi)
