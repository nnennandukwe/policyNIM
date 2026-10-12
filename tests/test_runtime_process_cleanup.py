"""Exercise shell cleanup through the CLI, real processes, and SQLite evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_index_identity import chunk, settings_for
from typer.testing import CliRunner

from policynim.interfaces import cli
from policynim.services.runtime_execution import create_runtime_execution_service
from policynim.settings import Settings
from policynim.storage import RuntimeEvidenceStore, create_index_store
from policynim.types import RuntimeRulesArtifact, ShellCommandActionRequest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process-group cleanup")


@pytest.fixture
def runtime_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Prepare a real compatible index and empty runtime rules without a provider."""
    settings = settings_for(
        tmp_path / "index.sqlite3",
        runtime_rules_artifact_path=tmp_path / "rules.json",
        runtime_evidence_db_path=tmp_path / "evidence.sqlite3",
        runtime_shell_timeout_seconds=1.0,
    )
    create_index_store(settings).replace([chunk()])
    settings.runtime_rules_artifact_path.write_text(RuntimeRulesArtifact().model_dump_json())
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def _request(tmp_path: Path, script: str) -> ShellCommandActionRequest:
    """Build a shell action in the shared evidence session."""
    return ShellCommandActionRequest(
        kind="shell_command",
        task="Verify subprocess cleanup.",
        session_id="process-cleanup",
        cwd=tmp_path,
        command=[sys.executable, "-c", script],
    )


def _child_script(*, redirect: bool, parent_exits: bool = False) -> str:
    """Start a child that can write only after the test releases its gate."""
    child = (
        "import time; from pathlib import Path; "
        "Path('child-ready').touch(); deadline = time.monotonic() + 5\n"
        "while not Path('release').exists() and time.monotonic() < deadline: time.sleep(0.01)\n"
        "if Path('release').exists(): Path('late-write').write_text('survived')\n"
    )
    output = ", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL" if redirect else ""
    return (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]{output}); "
        + ("" if parent_exits else "time.sleep(10)")
    )


def _release_and_observe(tmp_path: Path) -> bool:
    """Let a surviving child write, with a bounded wait for the regression signal."""
    (tmp_path / "release").touch()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if (tmp_path / "late-write").exists():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.parametrize("redirect,parent_exits", [(False, False), (True, False), (False, True)])
def test_runtime_cli_timeout_stops_children_and_allows_retry(
    runtime_settings: Settings, tmp_path: Path, redirect: bool, parent_exits: bool
) -> None:
    """Timeout evidence must precede a safe retry with no surviving child effects."""
    runner = CliRunner()
    control = _request(tmp_path, "from pathlib import Path; Path('control').touch()")
    allowed = runner.invoke(
        cli.app, ["runtime", "execute", "--input", "-"], input=control.model_dump_json()
    )
    assert allowed.exit_code == 0, allowed.output
    assert (tmp_path / "control").exists()

    request = _request(tmp_path, _child_script(redirect=redirect, parent_exits=parent_exits))
    started = time.monotonic()
    try:
        result = runner.invoke(
            cli.app, ["runtime", "execute", "--input", "-"], input=request.model_dump_json()
        )
        elapsed = time.monotonic() - started
    finally:
        survived = _release_and_observe(tmp_path)
    assert (tmp_path / "child-ready").exists(), "The child must start before timeout."
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["execution_outcome"] == "failed"
    assert payload["failure_class"] == "timeout"
    assert not survived, "The child wrote after the CLI reported a timeout."
    assert elapsed < 3.0, "Cleanup waited for the child's five-second lifetime."

    retry = runner.invoke(
        cli.app, ["runtime", "execute", "--input", "-"], input=control.model_dump_json()
    )
    assert retry.exit_code == 0, retry.output
    report = runner.invoke(cli.app, ["evidence", "report", "--session-id", "process-cleanup"])
    assert report.exit_code == 0, report.output
    summary = json.loads(report.stdout)
    assert summary["failed_count"] == 1
    assert summary["allowed_count"] == 2
    assert summary["incomplete_count"] == 0
    store = RuntimeEvidenceStore(path=runtime_settings.runtime_evidence_db_path)
    events = store.list_session_events("process-cleanup")
    assert [event.event_kind for event in events] == [
        "decision",
        "allowed",
        "decision",
        "failed",
        "decision",
        "allowed",
    ]
    assert events[3].failure_class == "timeout"


def test_interrupted_runtime_stops_children_before_propagating(
    runtime_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One injected interruption must drain real child processes and remain incomplete."""
    communicate = subprocess.Popen.communicate
    interrupted = False

    def interrupt_once(process, *args, **kwargs):
        """Interrupt after child startup once, then delegate subsequent communication."""
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            deadline = time.monotonic() + 5
            while not (tmp_path / "child-ready").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert (tmp_path / "child-ready").exists()
            raise KeyboardInterrupt("injected after child startup")
        return communicate(process, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupt_once)
    try:
        with create_runtime_execution_service(runtime_settings) as service:
            with pytest.raises(KeyboardInterrupt, match="injected after child startup"):
                service.execute(_request(tmp_path, _child_script(redirect=True)))
    finally:
        survived = _release_and_observe(tmp_path)
    assert not survived
    store = RuntimeEvidenceStore(path=runtime_settings.runtime_evidence_db_path)
    events = store.list_session_events("process-cleanup")
    assert [event.event_kind for event in events] == ["decision"]
