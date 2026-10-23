"""Build an allowlisted portable plugin archive without reading runtime secrets."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import ipaddress
import json
import os
import re
import struct
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "packaging" / "openai-plugin"
MEMBERS = ("LICENSE", "NOTICE", "assets/icon.svg", "mcp.json", "plugin.json")
SCHEMA_HASHES = {
    "plugin": "0a4aad95ce337878ad38802ebf0daa3fde76abe3f65400c86bcbb1ec0b3ab883",
    "mcp": "6539175bfcdf43085855183e86da40ea94b166547a72b47ae9a0a390516d3acb",
}
RELEASE_KEYS = frozenset(
    {
        "mcp_endpoint",
        "websiteURL",
        "supportURL",
        "privacyPolicyURL",
        "termsOfServiceURL",
        "demo_recording_url",
    }
)
TOOLS = frozenset(
    {
        "policy_search",
        "policy_compile",
        "policy_preflight",
        "policy_preflight_evidence",
        "eval_submit",
        "eval_inspect",
        "eval_cancel",
        "eval_result",
    }
)
ICON_SOURCE_HASH = "c719b04a70e7399cd36d9587487cd7475aa6b25574c9e1a79e7922524bd9fc65"


class PackageValidationError(ValueError):
    """Carry an input-free validation reason that the CLI can safely display."""


def read_json(path: Path) -> dict:
    """Read an object while rejecting duplicate keys rather than silently replacing them."""

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict:
        """Reject ambiguous objects at every nesting level."""
        result = {}
        for key, value in pairs:
            if key in result:
                raise PackageValidationError("Duplicate JSON key.")
            result[key] = value
        return result

    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)
    if not isinstance(result, dict):
        raise PackageValidationError("JSON input must be an object.")
    return result


def public_url(value: object, field: str) -> str:
    """Require public HTTPS without credentials, queries, fragments or sample hosts."""
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise PackageValidationError(
            f"{field}: supply a public HTTPS URL of at most 1024 characters."
        )
    decoded = unquote(value)
    if any(character.isspace() or ord(character) < 33 for character in decoded):
        raise PackageValidationError(f"{field}: URL contains whitespace or control characters.")
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if (
            parts.scheme != "https"
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or parts.port not in (None, 443)
            or "\\" in decoded
            or "{" in decoded
            or "}" in decoded
            or "<" in decoded
            or ">" in decoded
            or "%" in value
            or host.endswith(".")
        ):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if (
                len(host) > 253
                or "." not in host
                or not re.fullmatch(r"[a-z0-9.-]+", host)
                or any(
                    not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in host.split(".")
                )
                or host.rsplit(".", 1)[-1].isdigit()
                or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))
                or host in {"example.com", "example.org", "example.net"}
                or ".example." in host
                or host.endswith(".example")
                or any(part in {"placeholder", "changeme", "tbd"} for part in host.split("."))
            ):
                raise ValueError
        else:
            if not address.is_global or address.is_multicast:
                raise ValueError
    except ValueError:
        raise PackageValidationError(
            f"{field}: use a public HTTPS URL with no credentials or sample host."
        ) from None
    return value


def safe_text(value: object) -> None:
    """Reject credential-shaped or local operational content in package strings."""
    if isinstance(value, dict):
        for key, item in value.items():
            if re.search(r"credential|password|secret|authorization|api[_-]?key|token", key, re.I):
                raise PackageValidationError("Package contains a forbidden credential field.")
            safe_text(item)
    elif isinstance(value, list):
        for item in value:
            safe_text(item)
    elif isinstance(value, str):
        if re.search(
            r"(?i)\b(?:bearer\s+(?!tokens?\b)\S+|sk-[a-z0-9_-]{12,}|nvapi-[a-z0-9_-]+|"
            r"gh[pousr]_[a-z0-9]+|github_pat_[a-z0-9_]+|"
            r"eyJ[a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+)|"
            r"-----BEGIN .*PRIVATE KEY",
            value,
        ):
            raise PackageValidationError("Package contains credential-shaped text.")

        if re.search(r"\b(?:api|private|signing)\s+key[\"']?\s*[=:]", value, re.I):
            raise PackageValidationError("Package contains credential-shaped text.")

        for assignment in re.finditer(r"\b([a-z_][a-z0-9_-]*)[\"']?\s*[=:]", value, flags=re.I):
            if re.search(
                r"token|pass(?:word|wd)|secret|credential|authorization|"
                r"(?:api|private|signing)[_-]?key",
                assignment.group(1),
                re.I,
            ):
                raise PackageValidationError("Package contains credential-shaped text.")

        def remove_public_url(match: re.Match[str]) -> str:
            """Exclude validated public URLs from filesystem-path inspection only."""
            authority = re.split(r"[/#?]", match.group().split("://", 1)[1], maxsplit=1)[0]
            if "@" in authority:
                raise PackageValidationError("Package URL contains credential userinfo.")
            public_url(match.group().rstrip(".,;!)]}"), "Package URL")
            return ""

        path_text = re.sub(r"https?://[^\s<>\"]+", remove_public_url, value, flags=re.I)
        if re.search(
            r"(?:^|[\s:=('\"`\[])/(?!/)[^\s<>\"']+|"
            r"file://|\b[A-Z]:[\\/]|\\\\[^\s]",
            path_text,
            re.I,
        ):
            raise PackageValidationError("Package contains a local filesystem path.")


def validate_schema(manifest: dict, name: str, source: Path) -> None:
    """Validate with pinned vendored schemas without fetching remote references."""
    schema_path = source / "schemas" / f"{name}.schema.json"
    if schema_path.is_symlink():
        raise PackageValidationError("Schema must be a regular file.")
    schema_bytes = schema_path.read_bytes()
    if hashlib.sha256(schema_bytes).hexdigest() != SCHEMA_HASHES[name]:
        raise PackageValidationError(f"{name}: vendored schema hash mismatch.")
    validator = Draft202012Validator(json.loads(schema_bytes))
    if not validator.is_valid(manifest):
        raise PackageValidationError(f"{name}: manifest doesn't match Agent Plugins 1.0.0.")


def validate_icon(payload: bytes) -> None:
    """Accept only the square, self-contained wrapper around the known brand asset."""
    if len(payload) > 5 * 1024 * 1024:
        raise PackageValidationError("Icon exceeds 5 MiB.")
    if b"<!" in payload or b"<?" in payload:
        raise PackageValidationError(
            "Icon mustn't contain XML declarations, comments or processing instructions."
        )
    svg = ET.fromstring(payload)
    namespace = "{http://www.w3.org/2000/svg}"
    if (
        svg.tag != namespace + "svg"
        or svg.get("width") != "512"
        or svg.get("height") != "512"
        or svg.get("viewBox") != "240 100 430 430"
        or len(svg) != 1
        or svg[0].tag != namespace + "image"
        or set(svg.attrib) != {"width", "height", "viewBox", "role", "aria-label"}
        or svg.get("aria-label") != "PolicyNIM shield and chip mark"
        or svg.get("role") != "img"
        or set(svg[0].attrib) != {"width", "height", "href"}
        or svg[0].get("width") != "2048"
        or svg[0].get("height") != "640"
        or len(svg[0]) != 0
        or any(
            (element.text or "").strip() or (element.tail or "").strip() for element in svg.iter()
        )
    ):
        raise PackageValidationError("Icon must retain the reviewed square SVG structure.")
    href = svg[0].get("href", "")
    prefix = "data:image/png;base64,"
    if not href.startswith(prefix):
        raise PackageValidationError("Icon must embed its image without external references.")
    bitmap = base64.b64decode(href[len(prefix) :], validate=True)
    if hashlib.sha256(bitmap).hexdigest() != ICON_SOURCE_HASH or struct.unpack(
        ">II", bitmap[16:24]
    ) != (2048, 640):
        raise PackageValidationError("Icon bitmap differs from its recorded source.")


def render(source: Path, release: dict | None) -> tuple[dict[str, bytes], list[str]]:
    """Render and validate the entire allowlist before opening an output file."""
    payloads = {}
    for member in MEMBERS:
        path = source / member
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise PackageValidationError("Package sources mustn't use symlinks.")
        if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            raise PackageValidationError(f"{member}: missing, oversized or non-regular source.")
        payloads[member] = path.read_bytes()
    manifest = read_json(source / "plugin.json")
    mcp = read_json(source / "mcp.json")
    extension = manifest["extensions"]["com.openai"]
    if set(extension) != {"interface", "review", "publication"}:
        raise PackageValidationError("Unsupported OpenAI extension fields.")
    interface = extension["interface"]
    expected_interface = {
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
        "capabilities",
        "defaultPrompt",
        "composerIcon",
        "logo",
    }
    if set(interface) != expected_interface or mcp["mcpServers"] != {}:
        raise PackageValidationError("Keep deployment URLs outside the canonical source manifests.")
    missing = sorted(RELEASE_KEYS)
    if release is not None:
        if set(release) != RELEASE_KEYS:
            raise PackageValidationError(
                "Release inputs must contain exactly: " + ", ".join(sorted(RELEASE_KEYS))
            )
        urls = {field: public_url(value, field) for field, value in release.items()}
        if urlsplit(urls["mcp_endpoint"]).path != "/mcp":
            raise PackageValidationError(
                "mcp_endpoint: use the canonical public route /mcp at the service origin."
            )
        for field in ("websiteURL", "supportURL", "privacyPolicyURL", "termsOfServiceURL"):
            interface[field] = urls[field]
        extension["review"]["demo_recording_url"] = urls["demo_recording_url"]
        mcp["mcpServers"] = {"policynim": {"type": "streamable-http", "url": urls["mcp_endpoint"]}}
        missing = []
    validate_schema(manifest, "plugin", source)
    validate_schema(mcp, "mcp", source)
    if manifest["name"] != "policynim" or not re.fullmatch(
        r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)",
        manifest.get("version", ""),
    ):
        raise PackageValidationError("Supply the stable plugin name and a semantic version.")
    for field, maximum in {
        "displayName": 30,
        "shortDescription": 30,
        "longDescription": 4000,
        "developerName": 80,
    }.items():
        value = interface[field]
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise PackageValidationError(f"{field}: required text exceeds its submission limit.")
    if interface["category"] != "Developer Tools":
        raise PackageValidationError("Use the reviewed Developer Tools category.")
    capabilities = interface["capabilities"]
    if (
        not isinstance(capabilities, list)
        or len(capabilities) > 20
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > 120 or "\n" in item
            for item in capabilities
        )
    ):
        raise PackageValidationError("Invalid capability labels.")
    prompts = interface["defaultPrompt"]
    if (
        not isinstance(prompts, list)
        or not 1 <= len(prompts) <= 3
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > 128 or "@" in item
            for item in prompts
        )
        or len(set(prompts)) != len(prompts)
    ):
        raise PackageValidationError("Invalid starter prompts.")
    for field in ("composerIcon", "logo"):
        if interface[field] != "./assets/icon.svg":
            raise PackageValidationError(f"{field}: reference the bundled reviewed icon.")
    validate_icon(payloads["assets/icon.svg"])
    review = extension["review"]
    if set(review) != (
        {"test_cases", "demo_recording_url"} if release is not None else {"test_cases"}
    ):
        raise PackageValidationError("Unsupported review fields; access instructions stay private.")
    cases = review["test_cases"]
    if set(cases) != {"positive", "negative"}:
        raise PackageValidationError("Expected positive and negative case lists.")
    covered = set()
    for kind, count in (("positive", 5), ("negative", 3)):
        if not isinstance(cases[kind], list) or len(cases[kind]) != count:
            raise PackageValidationError(f"Expected exactly {count} {kind} review cases.")
        for case in cases[kind]:
            if not isinstance(case, dict) or set(case) != {
                "description",
                "prompt",
                "tools_triggered",
                "expected_behavior",
            }:
                raise PackageValidationError("Unsupported review case fields.")
            for field, value in case.items():
                if (
                    not isinstance(value, str)
                    or len(value) > 4000
                    or (not value.strip() and (kind == "positive" or field != "tools_triggered"))
                ):
                    raise PackageValidationError("Invalid review case text.")
            names = {name.strip() for name in case["tools_triggered"].split(",") if name.strip()}
            if not names <= TOOLS:
                raise PackageValidationError("Review case names an unexposed tool.")
            if kind == "positive":
                covered.update(names)
    if covered != TOOLS:
        raise PackageValidationError("Positive review cases must cover all eight exposed tools.")
    if (
        set(extension["publication"]) != {"release_notes"}
        or not isinstance(extension["publication"]["release_notes"], str)
        or not extension["publication"]["release_notes"].strip()
    ):
        raise PackageValidationError(
            "Supply release notes without unreviewed publication settings."
        )
    for field in ("homepage", "repository"):
        public_url(manifest[field], field)
    public_url(manifest["author"]["url"], "author.url")
    safe_text(manifest)
    safe_text(mcp)
    for member in ("LICENSE", "NOTICE"):
        safe_text(payloads[member].decode("utf-8"))
    for member, document in (("plugin.json", manifest), ("mcp.json", mcp)):
        payloads[member] = (json.dumps(document, indent=2, ensure_ascii=True) + "\n").encode()
    return payloads, missing


def build(output: Path, release: dict | None = None, *, source: Path = SOURCE) -> dict:
    """Validate, construct, read back and atomically replace one deterministic ZIP."""
    payloads, missing = render(source, release)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for member in MEMBERS:
            info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payloads[member])
    contents = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(contents)) as archive:
        if tuple(archive.namelist()) != MEMBERS or archive.testzip() is not None:
            raise PackageValidationError("ZIP readback failed.")
        if any(archive.read(member) != payloads[member] for member in MEMBERS):
            raise PackageValidationError("ZIP member readback failed.")
    if len(contents) > 100 * 1024 * 1024:
        raise PackageValidationError("ZIP exceeds 100 MiB.")
    if output.is_symlink():
        raise PackageValidationError("Output file mustn't be a symlink.")
    try:
        output_directory = output.parent.resolve()
    except (OSError, RuntimeError):
        raise PackageValidationError(
            "Output directory can't be resolved; select a physical directory."
        ) from None
    output = output_directory / output.name
    if output.resolve().is_relative_to(source.resolve()):
        raise PackageValidationError("Output must stay outside package source.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {
        "status": "source_only" if missing else "candidate_requires_live_acceptance",
        "sha256": hashlib.sha256(contents).hexdigest(),
        "members": list(MEMBERS),
        "missing_release_inputs": missing,
        "external_gates": [
            "approved_policies_and_brand_rights",
            "public_url_readback",
            "production_oauth_and_eight_tool_discovery",
            "reviewer_access",
            "executed_review_cases_and_recording",
            "owner_submission",
        ],
    }


def main() -> int:
    """Expose the package workflow without reading environment configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-inputs", type=Path)
    arguments = parser.parse_args()
    try:
        release = read_json(arguments.release_inputs) if arguments.release_inputs else None
        result = build(arguments.output, release)
    except PackageValidationError as exc:
        parser.exit(1, f"Plugin build failed validation: {exc}\n")
    except (ValueError, KeyError, TypeError, OSError, ET.ParseError, struct.error):
        # Don't echo malformed input or filesystem exceptions containing private paths.
        parser.exit(1, "Plugin build failed validation. Check source fields and release inputs.\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
