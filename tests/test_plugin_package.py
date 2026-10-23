"""Exercise the real ZIP command and reject unsafe or incomplete package inputs."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import zipfile
from functools import partial
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_plugin.py"
spec = importlib.util.spec_from_file_location("build_plugin", SCRIPT)
assert spec is not None and spec.loader is not None
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture
def package_source(tmp_path: Path) -> Path:
    """Keep mutation tests away from the checked-in package and brand assets."""
    source = tmp_path / "source"
    shutil.copytree(builder.SOURCE, source)
    return source


@pytest.fixture
def release_inputs() -> dict[str, str]:
    """Use synthetic offline URL shapes; these aren't published service evidence."""
    return {
        "mcp_endpoint": "https://review.policynim.org/mcp",
        "websiteURL": "https://review.policynim.org/",
        "supportURL": "https://review.policynim.org/support",
        "privacyPolicyURL": "https://review.policynim.org/privacy",
        "termsOfServiceURL": "https://review.policynim.org/terms",
        "demo_recording_url": "https://review.policynim.org/walkthrough",
    }


def edit_manifest(source: Path, field: str, value: object) -> None:
    """Mutate a listing field through the public source manifest boundary."""
    path = source / "plugin.json"
    manifest = json.loads(path.read_text())
    manifest["extensions"]["com.openai"]["interface"][field] = value
    path.write_text(json.dumps(manifest))


def test_source_cli_produces_repeatable_root_zip_without_operational_files(tmp_path: Path) -> None:
    """Run the public command twice and independently inspect exact archive members."""
    outputs = [tmp_path / "first.zip", tmp_path / "second.zip"]
    for output in outputs:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--output", str(output)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        summary = json.loads(completed.stdout)
        assert summary["status"] == "source_only"
        assert set(summary["missing_release_inputs"]) == set(release_key_names())
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    with zipfile.ZipFile(outputs[0]) as archive:
        assert set(archive.namelist()) == {
            "plugin.json",
            "mcp.json",
            "LICENSE",
            "NOTICE",
            "assets/icon.svg",
        }
        assert archive.testzip() is None
        assert json.loads(archive.read("mcp.json"))["mcpServers"] == {}
        for member in archive.infolist():
            assert member.date_time == (1980, 1, 1, 0, 0, 0)
            assert member.external_attr >> 16 == 0o100644
        manifest = json.loads(archive.read("plugin.json"))
        cases = manifest["extensions"]["com.openai"]["review"]["test_cases"]
        assert len(cases["positive"]) == 5 and len(cases["negative"]) == 3
        mentioned = {
            tool.strip()
            for case in cases["positive"]
            for tool in case["tools_triggered"].split(",")
        }
        assert mentioned == {
            "policy_search",
            "policy_compile",
            "policy_preflight",
            "policy_preflight_evidence",
            "eval_submit",
            "eval_inspect",
            "eval_result",
            "eval_cancel",
        }


def release_key_names() -> set[str]:
    """Keep expected caller input fields independent from the builder constant."""
    return {
        "mcp_endpoint",
        "websiteURL",
        "supportURL",
        "privacyPolicyURL",
        "termsOfServiceURL",
        "demo_recording_url",
    }


def test_release_cli_renders_only_public_urls(tmp_path: Path, release_inputs: dict) -> None:
    """Release rendering is a structural check with explicit live-acceptance gates."""
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps(release_inputs))
    output = tmp_path / "candidate.zip"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output), "--release-inputs", str(inputs)],
        capture_output=True,
        text=True,
        check=True,
    )
    summary = json.loads(completed.stdout)
    assert summary["status"] == "candidate_requires_live_acceptance"
    assert summary["missing_release_inputs"] == []
    assert "production_oauth_and_eight_tool_discovery" in summary["external_gates"]
    with zipfile.ZipFile(output) as archive:
        mcp = json.loads(archive.read("mcp.json"))
        assert mcp["mcpServers"] == {
            "policynim": {"type": "streamable-http", "url": release_inputs["mcp_endpoint"]}
        }
        manifest = json.loads(archive.read("plugin.json"))
        extension = manifest["extensions"]["com.openai"]
        assert extension["interface"]["privacyPolicyURL"] == release_inputs["privacyPolicyURL"]
        assert extension["review"]["demo_recording_url"] == release_inputs["demo_recording_url"]
        assert "headers" not in mcp["mcpServers"]["policynim"]


@pytest.mark.parametrize("missing", sorted(release_key_names()))
def test_incomplete_release_preserves_existing_output(
    tmp_path: Path, release_inputs: dict, missing: str
) -> None:
    """An incomplete release can't replace a previous validated archive."""
    release_inputs.pop(missing)
    output = tmp_path / "retained.zip"
    output.write_bytes(b"retained archive")
    with pytest.raises(ValueError, match="exactly"):
        builder.build(output, release_inputs)
    assert output.read_bytes() == b"retained archive"


@pytest.mark.parametrize("path", ["", "/", "/admin", "/mcp/", "/mcp/other"])
def test_release_requires_the_canonical_mcp_route(
    tmp_path: Path, release_inputs: dict, path: str
) -> None:
    """A public HTTPS website or admin route isn't a usable PolicyNIM MCP endpoint."""
    release_inputs["mcp_endpoint"] = "https://review.policynim.org" + path
    output = tmp_path / "retained.zip"
    output.write_bytes(b"retained archive")
    with pytest.raises(ValueError, match="canonical public route"):
        builder.build(output, release_inputs)
    assert output.read_bytes() == b"retained archive"


def test_release_preserves_a_public_base_prefix(tmp_path: Path, release_inputs: dict) -> None:
    """A reverse proxy can publish the MCP route under its configured public base."""
    release_inputs["mcp_endpoint"] = "https://review.policynim.org/policy/mcp"
    output = tmp_path / "prefixed.zip"
    builder.build(output, release_inputs)
    with zipfile.ZipFile(output) as archive:
        assert (
            json.loads(archive.read("mcp.json"))["mcpServers"]["policynim"]["url"]
            == (release_inputs["mcp_endpoint"])
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://policynim.org/mcp",
        "https://localhost/mcp",
        "https://127.0.0.1/mcp",
        "https://10.0.0.1/mcp",
        "https://[::1]/mcp",
        "https://example.com/mcp",
        "https://demo.example.net/mcp",
        "https://review.test/mcp",
        "https://host.local/mcp",
        "https://user:private@policynim.org/mcp",
        "https://policynim.org/mcp?token=private",
        "https://policynim.org/mcp#private",
        "https://<railway-domain>/mcp",
        "https://policynim.org/%0Aprivate",
        "file:///Users/private/mcp",
        "https://policynim.org:8443/mcp",
        "https://policynim.org/\\private",
        "https://bad..policynim.org/mcp",
        "https://-bad.policynim.org/mcp",
        "https://127.1/mcp",
    ],
)
def test_private_sample_or_credential_urls_fail_without_echoing_values(
    tmp_path: Path, release_inputs: dict, url: str
) -> None:
    """Exercise endpoint validation through the CLI with credential-safe diagnostics."""
    release_inputs["mcp_endpoint"] = url
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps(release_inputs))
    output = tmp_path / "rejected.zip"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output), "--release-inputs", str(inputs)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert url not in completed.stderr
    assert "private" not in completed.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("displayName", "x" * 31),
        ("shortDescription", "x" * 31),
        ("longDescription", "x" * 4001),
        ("developerName", "x" * 81),
        ("defaultPrompt", ["x" * 129]),
        ("defaultPrompt", ["repeat", "repeat"]),
        ("composerIcon", "../outside.svg"),
        ("logo", "/Users/private/icon.svg"),
        ("capabilities", ["x" * 121]),
        ("unknownField", "unapproved"),
    ],
)
def test_invalid_listing_is_rejected(
    tmp_path: Path, package_source: Path, field: str, value: object
) -> None:
    """Listing and reference failures prevent any archive from being written."""
    edit_manifest(package_source, field, value)
    with pytest.raises(ValueError):
        builder.build(tmp_path / "rejected.zip", source=package_source)


def test_extra_files_never_enter_archive(tmp_path: Path, package_source: Path) -> None:
    """Local credentials and draft evidence outside the allowlist stay outside the ZIP."""
    (package_source / ".env").write_text("PRIVATE_FIXTURE=exclude-me")
    (package_source / "reviewer-account.txt").write_text("exclude-me")
    output = tmp_path / "safe.zip"
    builder.build(output, source=package_source)
    assert b"exclude-me" not in output.read_bytes()


@pytest.mark.parametrize(
    "secret",
    [
        "Bearer fixture-access-value",
        "/Users/private/task.txt",
        "nvapi-private-fixture",
        "-----BEGIN RSA PRIVATE KEY-----",
        "ghp_" + "a" * 36,
        "github_pat_" + "b" * 22 + "_" + "c" * 59,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaXh0dXJlIn0.Zml4dHVyZQ",
        "client_secret=fixture-hidden-value",
        "/tmp/private-task.json",
    ],
)
def test_secret_shaped_content_in_allowed_file_is_rejected(
    tmp_path: Path, package_source: Path, secret: str
) -> None:
    """The allowlist doesn't bypass text inspection of intended package files."""
    edit_manifest(package_source, "longDescription", secret)
    with pytest.raises(ValueError, match="credential-shaped"):
        builder.build(tmp_path / "rejected.zip", source=package_source)


@pytest.mark.parametrize("mutation", ["icon", "schema", "symlink", "cases", "tools"])
def test_tampered_assets_schemas_and_review_cases_fail(
    tmp_path: Path, package_source: Path, mutation: str
) -> None:
    """Reject asset/script injection, schema drift, file escape and review-case omissions."""
    if mutation == "icon":
        path = package_source / "assets" / "icon.svg"
        path.write_text(path.read_text().replace('width="512"', 'width="47"'))
    elif mutation == "schema":
        (package_source / "schemas" / "mcp.schema.json").write_text("{}")
    elif mutation == "symlink":
        path = package_source / "NOTICE"
        path.unlink()
        path.symlink_to(ROOT / "README.md")
    else:
        path = package_source / "plugin.json"
        manifest = json.loads(path.read_text())
        cases = manifest["extensions"]["com.openai"]["review"]["test_cases"]
        if mutation == "cases":
            cases["negative"].pop()
        else:
            cases["positive"][4]["tools_triggered"] = "eval_submit, eval_inspect"
        path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        builder.build(tmp_path / "rejected.zip", source=package_source)


def test_unknown_release_fields_and_duplicate_keys_are_rejected(
    tmp_path: Path, release_inputs: dict
) -> None:
    """No private credential or ambiguous override is accepted as a release input."""
    release_inputs["client_secret"] = "excluded-fixture"
    with pytest.raises(ValueError, match="exactly"):
        builder.build(tmp_path / "rejected.zip", release_inputs)
    inputs = tmp_path / "duplicates.json"
    inputs.write_text('{"mcp_endpoint":"first","mcp_endpoint":"second"}')
    with pytest.raises(ValueError, match="Duplicate"):
        builder.read_json(inputs)


def test_output_cant_overwrite_package_source(package_source: Path) -> None:
    """A mistaken output path can't replace the manifest with ZIP bytes."""
    output = package_source / "plugin.json"
    original = output.read_bytes()
    with pytest.raises(ValueError, match="outside package source"):
        builder.build(output, source=package_source)
    assert output.read_bytes() == original


@pytest.mark.parametrize("insertion", ["text", "tail", "nested", "comment"])
def test_icon_cant_carry_unreviewed_content(
    tmp_path: Path, package_source: Path, insertion: str
) -> None:
    """Keep text, tail nodes, nested elements and ignored XML out of the icon."""
    path = package_source / "assets" / "icon.svg"
    icon = path.read_text()
    secret = "Bearer fixture-hidden-value"
    if insertion == "text":
        icon = icon.replace("<image", secret + "<image")
    elif insertion == "tail":
        icon = icon.replace("</svg>", secret + "</svg>")
    elif insertion == "nested":
        icon = icon.replace("/></svg>", "><title>private</title></image></svg>")
    else:
        icon = icon.replace("</svg>", "<!--private--></svg>")
    path.write_text(icon)
    with pytest.raises(ValueError, match="Icon"):
        builder.build(tmp_path / "rejected.zip", source=package_source)


def test_malformed_utf8_notice_has_controlled_cli_failure(
    tmp_path: Path, package_source: Path, monkeypatch, capsys
) -> None:
    """UnicodeDecodeError already inherits ValueError and must produce no traceback."""
    output = tmp_path / "retained.zip"
    output.write_bytes(b"retained archive")
    (package_source / "NOTICE").write_bytes(b"\xff")
    monkeypatch.setattr(builder, "build", partial(builder.build, source=package_source))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--output", str(output)])
    with pytest.raises(SystemExit) as stopped:
        builder.main()
    assert stopped.value.code == 1
    stderr = capsys.readouterr().err
    assert "failed validation" in stderr and "Traceback" not in stderr
    assert str(package_source) not in stderr
    assert output.read_bytes() == b"retained archive"
