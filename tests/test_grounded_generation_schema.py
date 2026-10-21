"""Compact source-selection grammar checked with an independent JSON Schema validator."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from test_nvidia_generator import MockOpenAIClient

from policynim.nvidia_models import NVIDIA_LIGHTNING_CHAT_MODEL
from policynim.providers.nvidia import NVIDIAGenerator
from policynim.source_units import SourceCatalog, SourceContext, SourceUnit
from policynim.types import GeneratedPreflightDraft, PolicyMetadata, PreflightRequest, ScoredChunk


def context() -> list[ScoredChunk]:
    """Supply separate canonical owners with explicitly authored full source fixtures."""
    return [
        ScoredChunk(
            chunk_id=f"{policy_id}:required-rules",
            path=f"{policy_id}.md",
            section="Required Rules",
            lines="1-3",
            text="Review the retained rule.",
            policy=PolicyMetadata(
                policy_id=policy_id, title=title, doc_type="guidance", domain=domain
            ),
        )
        for policy_id, title, domain in [
            ("JOB", "Background Jobs", "architecture"),
            ("SESSION", "Session Boundaries", "security"),
        ]
    ]


def request_wire(chunks: list[ScoredChunk]) -> dict[str, Any]:
    """Capture actual SDK options for a source-bound compact refusal payload."""
    client = MockOpenAIClient('{"source_ids":[],"insufficient_context":true}')
    catalog = SourceCatalog(
        tuple(
            SourceUnit(
                f"s{index + 1}",
                chunk.policy.policy_id,
                chunk.policy.title,
                "Review the retained rule.",
                (chunk.chunk_id,),
            )
            for index, chunk in enumerate(chunks)
        )
    )
    generator = NVIDIAGenerator(
        api_key="fixture-key",
        model=NVIDIA_LIGHTNING_CHAT_MODEL,
        base_url="https://example.invalid/v1",
        timeout_seconds=1,
        max_retries=0,
        client=client,
    )
    generator.generate_preflight(
        PreflightRequest(task="Review cleanup job"), SourceContext(tuple(chunks), catalog)
    )
    return cast(dict[str, Any], client.chat.completions.calls[0])


@pytest.mark.parametrize(
    "mutation", ["chunk_as_policy", "section_as_title", "foreign_owner", "unknown_global"]
)
def test_request_schema_rejects_supported_metadata_failure(mutation: str) -> None:
    """Models can't supply owner/title/citation prose through the internal selector grammar."""
    schema = request_wire(context())["response_format"]["json_schema"]["schema"]
    Draft202012Validator.check_schema(schema)
    response: dict[str, Any] = {"source_ids": ["s1"], "insufficient_context": False}
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors(response))
    invalid = deepcopy(response)
    if mutation == "chunk_as_policy":
        invalid["source_ids"] = ["JOB:required-rules"]
    elif mutation == "section_as_title":
        invalid["title"] = "Background Jobs > Required Rules"
    elif mutation == "foreign_owner":
        invalid["policy_id"] = "SESSION"
    else:
        invalid["citation_ids"] = ["unretained-citation"]
    assert list(validator.iter_errors(invalid))


def test_context_schema_doesnt_mutate_public_types_or_bleed_between_requests() -> None:
    """Compact IDs are request-local and don't carry another request's source ownership."""
    original = GeneratedPreflightDraft.model_json_schema()
    chunks = context()
    first = request_wire(chunks[:1])
    second = request_wire(chunks[1:])
    first_sources = json.loads(first["messages"][1]["content"])["sources"]
    second_sources = json.loads(second["messages"][1]["content"])["sources"]
    assert [source["policy_id"] for source in first_sources] == ["JOB"]
    assert [source["policy_id"] for source in second_sources] == ["SESSION"]
    assert GeneratedPreflightDraft.model_json_schema() == original
    for wire in [first, second]:
        schema = wire["response_format"]["json_schema"]["schema"]
        assert "summary" not in schema["properties"]
        assert "applicable_policies" not in schema["properties"]
        assert schema["properties"]["source_ids"]["items"]["enum"] == ["s1"]
