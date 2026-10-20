"""Request-side ownership grammar, checked with an independent JSON Schema validator."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from test_nvidia_generator import MockOpenAIClient

from policynim.nvidia_models import NVIDIA_LIGHTNING_CHAT_MODEL
from policynim.providers.nvidia import NVIDIAGenerator
from policynim.types import GeneratedPreflightDraft, PolicyMetadata, PreflightRequest, ScoredChunk


def context() -> list[ScoredChunk]:
    """Provide distinct owners so cross-policy binding errors can't pass accidentally."""
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


def request_schema(chunks: list[ScoredChunk]) -> dict[str, Any]:
    """Capture the exact schema sent by the actual NVIDIA generator adapter."""
    client = MockOpenAIClient('{"summary":"Review sources.","insufficient_context":true}')
    generator = NVIDIAGenerator(
        api_key="fixture-key",
        model=NVIDIA_LIGHTNING_CHAT_MODEL,
        base_url="https://example.invalid/v1",
        timeout_seconds=1,
        max_retries=0,
        client=client,
    )
    generator.generate_preflight(PreflightRequest(task="Review cleanup job"), chunks)
    return cast(dict[str, Any], client.chat.completions.calls[0]["response_format"])["json_schema"][
        "schema"
    ]


@pytest.mark.parametrize(
    "mutation", ["chunk_as_policy", "section_as_title", "foreign_owner", "unknown_global"]
)
def test_request_schema_rejects_supported_metadata_failure(mutation: str) -> None:
    """The provider grammar must rule out invalid owner metadata before generation."""
    schema = request_schema(context())
    Draft202012Validator.check_schema(schema)
    response: dict[str, Any] = {
        "summary": "Review cleanup job.",
        "applicable_policies": [
            {
                "policy_id": "JOB",
                "title": "Background Jobs",
                "rationale": "Review the retained rule.",
                "citation_ids": ["JOB:required-rules"],
            }
        ],
        "citation_ids": ["JOB:required-rules", "SESSION:required-rules"],
    }
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors(response))
    invalid = deepcopy(response)
    policy = invalid["applicable_policies"][0]
    if mutation == "chunk_as_policy":
        policy["policy_id"] = "JOB:required-rules"
    elif mutation == "section_as_title":
        policy["title"] = "Background Jobs > Required Rules"
    elif mutation == "foreign_owner":
        policy["citation_ids"] = ["SESSION:required-rules"]
    else:
        invalid["citation_ids"] = ["unretained-citation"]
    assert list(validator.iter_errors(invalid))


def test_context_schema_doesnt_mutate_public_types_or_bleed_between_requests() -> None:
    """Keep the response contract stable while each request uses only its pinned owners."""
    original = GeneratedPreflightDraft.model_json_schema()
    chunks = context()
    first = request_schema(chunks[:1])
    second = request_schema(chunks[1:])
    assert "JOB:required-rules" in str(first) and "SESSION:required-rules" not in str(first)
    assert "SESSION:required-rules" in str(second) and "JOB:required-rules" not in str(second)
    assert GeneratedPreflightDraft.model_json_schema() == original
    empty = request_schema([])
    Draft202012Validator.check_schema(empty)
    assert not list(
        Draft202012Validator(empty).iter_errors(
            {
                "summary": "No sources.",
                "applicable_policies": [],
                "citation_ids": [],
                "insufficient_context": True,
            }
        )
    )
