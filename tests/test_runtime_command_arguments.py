"""Check argv fidelity and validation through CLI execution and stored evidence."""

from __future__ import annotations

import json
import re
import shlex
import sqlite3
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_index_identity import chunk, settings_for
from typer.testing import CliRunner

from policynim.interfaces import cli
from policynim.settings import Settings
from policynim.storage import RuntimeEvidenceStore, create_index_store
from policynim.types import (
    CompiledRuntimeRule,
    RuntimeRulesArtifact,
    ShellCommandActionRequest,
    ShellCommandExecutionRequest,
)


@pytest.fixture
def runtime_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Prepare real runtime dependencies without calling a provider."""
    settings = settings_for(
        tmp_path / "index.sqlite3",
        runtime_rules_artifact_path=tmp_path / "rules.json",
        runtime_evidence_db_path=tmp_path / "evidence.sqlite3",
    )
    create_index_store(settings).replace([chunk()])
    settings.runtime_rules_artifact_path.write_text(RuntimeRulesArtifact().model_dump_json())
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def _payload(tmp_path: Path, command: list[str]) -> str:
    """Send raw caller input without first passing through a request model."""
    return json.dumps(
        {
            "kind": "shell_command",
            "task": "Check argument fidelity.",
            "session_id": "argv-check",
            "cwd": str(tmp_path),
            "command": command,
        }
    )


def _command(*arguments: str) -> list[str]:
    """Capture the actual argv received by a real child process."""
    return [
        sys.executable,
        "-c",
        "import json,sys; from pathlib import Path; "
        'Path("received.json").write_text(json.dumps(sys.argv[1:]))',
        *arguments,
    ]


# Unicode test-data exception: café verifies that argv retains non-ASCII characters.
@pytest.mark.parametrize("argument", ["  padded  ", "", " \t ", "\nline\n", "café"])
def test_runtime_execute_preserves_arguments_and_evidence(
    runtime_settings: Settings, tmp_path: Path, argument: str
) -> None:
    """Process arguments, returned data, and reopened evidence must match caller input."""
    command = _command(argument)
    result = CliRunner().invoke(
        cli.app, ["runtime", "execute", "--input", "-"], input=_payload(tmp_path, command)
    )
    assert result.exit_code == 0, result.output
    assert json.loads((tmp_path / "received.json").read_text()) == [argument]
    assert json.loads(result.stdout)["request"]["command"] == command
    store = RuntimeEvidenceStore(path=runtime_settings.runtime_evidence_db_path)
    events = store.list_session_events("argv-check")
    assert [event.event_kind for event in events] == ["decision", "allowed"]
    for event in events:
        assert isinstance(event.request, ShellCommandExecutionRequest)
        assert event.request.command == command
    report = CliRunner().invoke(cli.app, ["evidence", "report", "--session-id", "argv-check"])
    assert report.exit_code == 0, report.output
    assert json.loads(report.stdout)["incomplete_count"] == 0


def test_runtime_decision_matches_unmodified_arguments(
    runtime_settings: Settings, tmp_path: Path
) -> None:
    """A rule matching the original padded argument must block the child process."""
    command = _command("  restricted  ")
    rule = CompiledRuntimeRule(
        policy_id="P",
        title="Block this exact command",
        domain="security",
        source_path="policies/p.md",
        start_line=1,
        end_line=1,
        action="shell_command",
        effect="block",
        reason="This exact command needs a policy change.",
        command_regexes=["^" + re.escape(shlex.join(command)) + "$"],
    )
    runtime_settings.runtime_rules_artifact_path.write_text(
        RuntimeRulesArtifact(rules=[rule]).model_dump_json()
    )
    runner = CliRunner()
    decided = runner.invoke(
        cli.app, ["runtime", "decide", "--input", "-"], input=_payload(tmp_path, command)
    )
    assert decided.exit_code == 0, decided.output
    assert json.loads(decided.stdout)["decision"] == "block"
    executed = runner.invoke(
        cli.app, ["runtime", "execute", "--input", "-"], input=_payload(tmp_path, command)
    )
    assert executed.exit_code != 0, executed.output
    assert json.loads(executed.stdout)["execution_outcome"] == "blocked"
    assert not (tmp_path / "received.json").exists()
    events = RuntimeEvidenceStore(
        path=runtime_settings.runtime_evidence_db_path
    ).list_session_events("argv-check")
    assert [event.event_kind for event in events] == ["decision", "blocked"]


@pytest.mark.parametrize("operation", ["decide", "execute"])
@pytest.mark.parametrize(
    "command,message",
    [
        ([], "at least 1 item"),
        ([""], "executable must not be empty"),
        (["bad\x00executable"], "NUL"),
        (_command("bad\x00argument"), "NUL"),
    ],
)
def test_runtime_rejects_invalid_command_before_side_effects(
    runtime_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    command: list[str],
    message: str,
) -> None:
    """Invalid argv must stop before service creation; a corrected retry must work."""

    def unexpected_service(*args: object, **kwargs: object) -> None:
        """Fail if invalid input advances beyond the CLI validation boundary."""
        pytest.fail("Invalid input created a runtime service.")

    runner = CliRunner()
    with monkeypatch.context() as patch:
        patch.setattr(
            cli,
            f"create_runtime_{'decision' if operation == 'decide' else 'execution'}_service",
            unexpected_service,
        )
        rejected = runner.invoke(
            cli.app, ["runtime", operation, "--input", "-"], input=_payload(tmp_path, command)
        )
    assert rejected.exit_code == 1, rejected.output
    assert "command" in rejected.output
    assert message in rejected.output
    assert not runtime_settings.runtime_evidence_db_path.exists()
    assert not (tmp_path / "received.json").exists()
    retry = runner.invoke(
        cli.app, ["runtime", "execute", "--input", "-"], input=_payload(tmp_path, _command("fixed"))
    )
    assert retry.exit_code == 0, retry.output
    assert json.loads((tmp_path / "received.json").read_text()) == ["fixed"]


@pytest.mark.parametrize("model", [ShellCommandActionRequest, ShellCommandExecutionRequest])
def test_shell_models_preserve_argument_contents(
    model: type[ShellCommandActionRequest] | type[ShellCommandExecutionRequest],
) -> None:
    """Action inputs and evidence readers must retain argument contents unchanged."""
    command = [" padded executable ", "", " \t ", "\nvalue\n"]
    request = model.model_validate_json(_payload(Path.cwd(), command))
    assert request.command == command
    assert model.model_validate_json(request.model_dump_json()).command == command
    with pytest.raises(ValidationError):
        model.model_validate_json(_payload(Path.cwd(), []))


def test_evidence_report_reads_legacy_nul_decision(
    runtime_settings: Settings, tmp_path: Path
) -> None:
    """Historical NUL decisions must remain readable as incomplete executions."""
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["runtime", "execute", "--input", "-"], input=_payload(tmp_path, _command("old"))
    )
    assert result.exit_code == 0, result.output
    # Recreate the decision-only row shape written by the old NUL acceptance bug.
    with sqlite3.connect(runtime_settings.runtime_evidence_db_path) as connection:
        connection.execute("DELETE FROM runtime_execution_events WHERE event_kind != 'decision'")
        connection.execute(
            "UPDATE runtime_execution_events SET request_json = ?",
            (_payload(tmp_path, [sys.executable, "\x00"]),),
        )
    report = runner.invoke(cli.app, ["evidence", "report", "--session-id", "argv-check"])
    assert report.exit_code == 0, report.output
    assert json.loads(report.stdout)["incomplete_count"] == 1
    events = RuntimeEvidenceStore(
        path=runtime_settings.runtime_evidence_db_path
    ).list_session_events("argv-check")
    assert isinstance(events[0].request, ShellCommandExecutionRequest)
    assert events[0].request.command == [sys.executable, "\x00"]
