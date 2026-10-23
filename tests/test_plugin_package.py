"""Exercise the real ZIP command and reject unsafe or incomplete package inputs."""

from __future__ import annotations

import importlib.util
import json
import os
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


def test_release_rejects_a_public_base_prefix(tmp_path: Path, release_inputs: dict) -> None:
    """Match the runtime settings contract that forbids a path in the public origin."""
    release_inputs["mcp_endpoint"] = "https://review.policynim.org/policy/mcp"
    with pytest.raises(ValueError, match="canonical public route"):
        builder.build(tmp_path / "rejected.zip", release_inputs)


@pytest.mark.parametrize("field", sorted(release_key_names()))
@pytest.mark.parametrize("path", ["/sk%2D" + "a" * 16, "/sk%252D" + "a" * 16])
def test_escaped_release_urls_are_rejected(
    tmp_path: Path, release_inputs: dict, field: str, path: str
) -> None:
    """Encoded credential shapes can't bypass scanning in any public URL field."""
    release_inputs[field] = "https://review.policynim.org" + path
    with pytest.raises(ValueError, match="public HTTPS URL"):
        builder.build(tmp_path / "rejected.zip", release_inputs)


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
        ".".join(["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiJmaXh0dXJlIn0", "Zml4dHVyZQ"]),
        "client_secret=fixture-hidden-value",
        "/tmp/private-task.json",
    ],
)
def test_secret_shaped_content_in_allowed_file_is_rejected(
    tmp_path: Path, package_source: Path, secret: str
) -> None:
    """The allowlist doesn't bypass text inspection of intended package files."""
    edit_manifest(package_source, "longDescription", secret)
    with pytest.raises(ValueError, match="credential-shaped|local filesystem path"):
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


def test_cli_identifies_the_rejected_field_without_echoing_input(
    tmp_path: Path, release_inputs: dict
) -> None:
    """Operators see the failed URL field and its recovery rule without the supplied value."""
    release_inputs["mcp_endpoint"] = "https://review.policynim.org/private-admin"
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps(release_inputs))
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--output",
            str(tmp_path / "rejected.zip"),
            "--release-inputs",
            str(inputs),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert "mcp_endpoint" in completed.stderr and "/mcp" in completed.stderr
    assert "private-admin" not in completed.stderr and str(inputs) not in completed.stderr


def test_listing_diagnostic_omits_private_text(
    tmp_path: Path, package_source: Path, monkeypatch, capsys
) -> None:
    """A rejected metadata value is never included in its field-level diagnostic."""
    edit_manifest(package_source, "longDescription", "private-sensitive-" * 300)
    monkeypatch.setattr(builder, "build", partial(builder.build, source=package_source))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--output", str(tmp_path / "rejected.zip")])
    with pytest.raises(SystemExit) as stopped:
        builder.main()
    assert stopped.value.code == 1
    stderr = capsys.readouterr().err
    assert "longDescription" in stderr
    assert "private-sensitive" not in stderr and str(package_source) not in stderr


def test_output_parent_alias_is_supported(tmp_path: Path) -> None:
    """Resolve a directory alias without treating the final output file as a symlink."""
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(physical, target_is_directory=True)
    summary = builder.build(alias / "package.zip")
    assert summary["status"] == "source_only"
    with zipfile.ZipFile(physical / "package.zip") as archive:
        assert "plugin.json" in archive.namelist()


def test_output_file_symlink_preserves_its_target(tmp_path: Path) -> None:
    """An explicit file symlink can't replace an unrelated existing file."""
    target = tmp_path / "manual.txt"
    target.write_bytes(b"manual content")
    output = tmp_path / "package.zip"
    output.symlink_to(target)
    with pytest.raises(ValueError, match="Output file"):
        builder.build(output)
    assert target.read_bytes() == b"manual content"


def test_output_parent_alias_cant_overwrite_source(tmp_path: Path, package_source: Path) -> None:
    """The source-containment guard uses the resolved destination, including directory aliases."""
    alias = tmp_path / "alias"
    alias.symlink_to(package_source, target_is_directory=True)
    manifest = package_source / "plugin.json"
    original = manifest.read_bytes()
    with pytest.raises(ValueError, match="outside package source"):
        builder.build(alias / "plugin.json", source=package_source)
    assert manifest.read_bytes() == original


def test_output_alias_loop_has_a_controlled_cli_failure(tmp_path: Path) -> None:
    """A directory loop returns a safe recovery reason without a traceback or local path."""
    loop = tmp_path / "loop"
    loop.symlink_to(loop, target_is_directory=True)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(loop / "package.zip")],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert "Output directory" in completed.stderr
    assert "Traceback" not in completed.stderr and str(loop) not in completed.stderr


@pytest.mark.parametrize(
    "field",
    [
        "websiteURL",
        "supportURL",
        "privacyPolicyURL",
        "termsOfServiceURL",
        "demo_recording_url",
    ],
)
@pytest.mark.parametrize("path", ["/home/", "/legal/private/privacy", "/owner's/home/"])
def test_public_url_paths_arent_local_filesystem_paths(
    tmp_path: Path, release_inputs: dict, field: str, path: str
) -> None:
    """Public HTTPS paths can share directory names with local filesystem paths."""
    release_inputs[field] = "https://review.policynim.org" + path
    output = tmp_path / "candidate.zip"
    assert builder.build(output, release_inputs)["status"] == "candidate_requires_live_acceptance"


def test_prose_can_link_a_public_url_without_hiding_a_local_path(
    tmp_path: Path, package_source: Path
) -> None:
    """Only validated URL spans are excluded; surrounding text still receives path inspection."""
    description = "Read https://policynim.org/home/ for product information."
    edit_manifest(package_source, "longDescription", description)
    assert builder.build(tmp_path / "source.zip", source=package_source)["status"] == "source_only"
    edit_manifest(package_source, "longDescription", description + " Read /Users/private/task.txt.")
    with pytest.raises(ValueError, match="local filesystem path"):
        builder.build(tmp_path / "rejected.zip", source=package_source)


def test_url_path_exemption_doesnt_hide_credential_shapes(
    tmp_path: Path, release_inputs: dict
) -> None:
    """Credential scanning covers the full URL before any filesystem-path exemption."""
    release_inputs["websiteURL"] = "https://review.policynim.org/home/ghp_" + "a" * 36
    with pytest.raises(ValueError, match="credential-shaped"):
        builder.build(tmp_path / "rejected.zip", release_inputs)


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "/root/.ssh/id_rsa",
        "/srv/private/notes",
        "/mnt/customer/data",
        r"\\review-host\share\private.txt",
        r"C:\local\task.txt",
        "C:/local/task.txt",
    ],
)
@pytest.mark.parametrize("wrapper", ["plain", "backtick", "quote"])
def test_rooted_filesystem_paths_are_rejected(
    tmp_path: Path, package_source: Path, path: str, wrapper: str
) -> None:
    """Public package metadata can't carry POSIX, Windows-drive or UNC filesystem paths."""
    if wrapper == "backtick":
        path = "`" + path + "`"
    elif wrapper == "quote":
        path = '"' + path + '"'
    edit_manifest(package_source, "longDescription", "Read " + path + " before proceeding.")
    with pytest.raises(ValueError, match="local filesystem path"):
        builder.build(tmp_path / "rejected.zip", source=package_source)


@pytest.mark.parametrize(
    "name",
    [
        "token",
        "password",
        "passwd",
        "secret",
        "clientSecret",
        "client_secret",
        "client-secret",
        "accessToken",
        "refreshToken",
        "refresh_token",
        "apiKey",
        "oauth_token",
        "AUTH_TOKEN",
        "servicePassword",
        "privateKey",
        "signing_key",
        "api key",
        "private key",
        "signing key",
    ],
)
@pytest.mark.parametrize("style", ["assignment", "quoted_json"])
def test_credential_assignments_cant_enter_listing_text(
    tmp_path: Path, package_source: Path, name: str, style: str
) -> None:
    """Build synthetic rejection inputs at runtime instead of committing credential literals."""
    value = "fixture-sensitive-value"
    text = name + "=" + value if style == "assignment" else json.dumps({name: value})
    edit_manifest(package_source, "longDescription", text)
    with pytest.raises(ValueError, match="credential-shaped"):
        builder.build(tmp_path / "rejected.zip", source=package_source)


@pytest.mark.parametrize("scheme", ["https", "http"])
@pytest.mark.parametrize("userinfo", ["fixture-user:fixture-pass", "fixture-user", ":", ""])
def test_prose_urls_cant_embed_userinfo(
    tmp_path: Path, package_source: Path, scheme: str, userinfo: str
) -> None:
    """Reject URL authority credentials in allowlisted prose before replacing an archive."""
    url = scheme + "://" + userinfo + "@policynim.org/docs"
    edit_manifest(package_source, "longDescription", "Read " + url + " for help.")
    output = tmp_path / "retained.zip"
    output.write_bytes(b"retained archive")
    with pytest.raises(ValueError, match="credential"):
        builder.build(output, source=package_source)
    assert output.read_bytes() == b"retained archive"


@pytest.mark.parametrize(
    "host", ["224.0.0.1", "239.255.255.255", "[ff0e::1]", ".".join(["a" * 63] * 5)]
)
def test_nonservice_release_hosts_are_rejected(
    tmp_path: Path, release_inputs: dict, host: str
) -> None:
    """Candidate endpoints need unicast IPs or hostnames within the DNS length limit."""
    release_inputs["mcp_endpoint"] = "https://" + host + "/mcp"
    with pytest.raises(ValueError, match="public HTTPS URL"):
        builder.build(tmp_path / "rejected.zip", release_inputs)


def test_negative_cases_cant_replace_positive_tool_coverage(
    tmp_path: Path, package_source: Path
) -> None:
    """Each exposed tool must appear in a positive workflow even if negatives name it."""
    path = package_source / "plugin.json"
    manifest = json.loads(path.read_text())
    cases = manifest["extensions"]["com.openai"]["review"]["test_cases"]
    cases["positive"][4]["tools_triggered"] = "eval_submit, eval_inspect"
    cases["negative"][0]["tools_triggered"] = "eval_cancel"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Positive review cases"):
        builder.build(tmp_path / "rejected.zip", source=package_source)


# Non-English input is required to test the ASCII-only version contract.
@pytest.mark.parametrize(
    "version",
    ["01.2.3", "1.02.3", "1.2.003", ".".join(chr(0x0660 + digit) for digit in (1, 2, 3))],
)
def test_nonsemantic_versions_are_rejected(
    tmp_path: Path, package_source: Path, version: str
) -> None:
    """Stable version components use ASCII digits without leading zeros."""
    path = package_source / "plugin.json"
    manifest = json.loads(path.read_text())
    manifest["version"] = version
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="semantic version"):
        builder.build(tmp_path / "rejected.zip", source=package_source)


@pytest.mark.parametrize(
    "text", ["Run each task-independently.", "Access requires a bearer token."]
)
def test_ordinary_listing_prose_doesnt_match_token_prefixes(
    tmp_path: Path, package_source: Path, text: str
) -> None:
    """Credential prefix checks don't reject common words inside ordinary listing prose."""
    edit_manifest(package_source, "longDescription", text)
    assert builder.build(tmp_path / "source.zip", source=package_source)["status"] == "source_only"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/private",
        "https://localhost/internal",
        "https://10.0.0.1/private",
        "http://policynim.org/help",
        "https://policynim.org/%41",
    ],
)
def test_invalid_prose_links_are_rejected(tmp_path: Path, package_source: Path, url: str) -> None:
    """All prose links must meet the same public HTTPS contract as release URLs."""
    edit_manifest(package_source, "longDescription", "Read " + url + " for help.")
    with pytest.raises(ValueError, match="public HTTPS URL"):
        builder.build(tmp_path / "rejected.zip", source=package_source)


@pytest.mark.parametrize(
    "link", ["(https://policynim.org)", "https://policynim.org.", "[https://policynim.org]"]
)
def test_prose_link_punctuation_isnt_part_of_its_hostname(
    tmp_path: Path, package_source: Path, link: str
) -> None:
    """Sentence and wrapper punctuation don't invalidate an otherwise public link."""
    edit_manifest(package_source, "longDescription", "Read " + link + " for help.")
    assert builder.build(tmp_path / "source.zip", source=package_source)["status"] == "source_only"


@pytest.mark.parametrize("prefix", ["sk-proj-", "sk-svcacct-"])
@pytest.mark.parametrize("location", ["listing", "review", "notice"])
def test_hyphenated_api_key_prefixes_cant_enter_allowed_files(
    tmp_path: Path, package_source: Path, prefix: str, location: str
) -> None:
    """Construct synthetic key shapes at runtime and reject them before output replacement."""
    text = prefix + "fixture_" * 8
    if location == "listing":
        edit_manifest(package_source, "longDescription", text)
    elif location == "review":
        path = package_source / "plugin.json"
        manifest = json.loads(path.read_text())
        case = manifest["extensions"]["com.openai"]["review"]["test_cases"]["positive"][0]
        case["prompt"] = text
        path.write_text(json.dumps(manifest))
    else:
        (package_source / "NOTICE").write_text(text)
    output = tmp_path / "retained.zip"
    output.write_bytes(b"retained archive")
    with pytest.raises(ValueError, match="credential-shaped"):
        builder.build(output, source=package_source)
    assert output.read_bytes() == b"retained archive"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Named pipes require a POSIX filesystem.")
@pytest.mark.parametrize("name", ["plugin", "mcp"])
def test_schema_named_pipes_are_rejected_without_waiting(
    tmp_path: Path, package_source: Path, name: str
) -> None:
    """A nonregular schema must fail promptly without waiting for a FIFO writer."""
    path = package_source / "schemas" / (name + ".schema.json")
    path.unlink()
    os.mkfifo(path)
    output = tmp_path / "retained.zip"
    output.write_bytes(b"retained archive")
    program = (
        "import runpy,sys;from pathlib import Path;"
        "builder=runpy.run_path(sys.argv[1]);"
        "builder['build'](Path(sys.argv[3]),source=Path(sys.argv[2]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", program, str(SCRIPT), str(package_source), str(output)],
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert result.returncode == 1
    assert "Schema must be a regular file" in result.stderr
    assert output.read_bytes() == b"retained archive"


@pytest.mark.parametrize(
    "url",
    ["https://review.policynim.org/sk-service-status", "https://sk-service-status.policynim.org/"],
)
def test_ordinary_release_slugs_arent_project_credentials(
    tmp_path: Path, release_inputs: dict, url: str
) -> None:
    """Known project-key prefixes don't ban unrelated hyphenated URL words."""
    release_inputs["websiteURL"] = url
    assert builder.build(tmp_path / "candidate.zip", release_inputs)["status"] == (
        "candidate_requires_live_acceptance"
    )
