"""Bound whole pinned policy text to compact untrusted model selections."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, overload

from pydantic import ConfigDict, Field

from policynim.errors import PolicyContextError
from policynim.ingest.chunking import chunk_policy_documents
from policynim.types import (
    CompiledPolicyPacket,
    GeneratedCompiledPolicyDraft,
    GeneratedPolicyConstraint,
    GeneratedPolicyGuidance,
    GeneratedPreflightDraft,
    ParsedDocument,
    ScoredChunk,
    StrictModel,
)

MAX_SOURCE_CHUNKS = 80
MAX_SOURCE_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 4 * MAX_SOURCE_BYTES
EXTRACTIVE_SUMMARY = (
    "Selected policy sources are quoted in full. Review their applicability to the task."
)
EXTRACTIVE_RATIONALE = "Selected source policy; its full text appears in the plan steps."


class SourceSelection(StrictModel):
    """Untrusted compact selectors; no model-authored policy wording is accepted."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_ids: list[str] = Field(max_length=MAX_SOURCE_CHUNKS)
    insufficient_context: bool


@dataclass(frozen=True)
class SourceUnit:
    """One complete body with canonical owner metadata and all source references."""

    source_id: str
    policy_id: str
    title: str
    body: str
    citation_ids: tuple[str, ...]
    provenance_json: str | None = None


@dataclass(frozen=True)
class SourceCatalog:
    """An operation's selected whole policies proven against one held snapshot."""

    units: tuple[SourceUnit, ...] = ()
    chunk_fingerprints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Apply budgets to explicit composition as well as pinned-document construction."""
        if (
            len(self.units) > MAX_SOURCE_CHUNKS
            or sum(len(unit.citation_ids) for unit in self.units) > MAX_SOURCE_CHUNKS
            or len(self.prompt_payload().encode("utf-8")) > MAX_SOURCE_BYTES
            or sum(len(value.encode("utf-8")) for value in self.chunk_fingerprints)
            > MAX_SOURCE_BYTES
        ):
            raise PolicyContextError()
        if len({unit.source_id for unit in self.units}) != len(self.units) or len(
            {unit.policy_id for unit in self.units}
        ) != len(self.units):
            raise PolicyContextError(identity_mismatch=True)
        if any(
            not re.fullmatch(r"s(?:[1-9]|[1-7][0-9]|80)", unit.source_id)
            or not unit.body.strip()
            or not unit.policy_id
            or not unit.title
            or not unit.citation_ids
            or len(set(unit.citation_ids)) != len(unit.citation_ids)
            for unit in self.units
        ):
            raise PolicyContextError(identity_mismatch=True)

    @classmethod
    def from_documents(
        cls, documents: Sequence[ParsedDocument], context: Sequence[ScoredChunk]
    ) -> SourceCatalog:
        """Require complete canonical chunks; never reconstruct a body from retrieved pieces."""
        if not context:
            return cls()
        selected = {chunk.policy.policy_id for chunk in context}
        selected_documents = [doc for doc in documents if doc.metadata.policy_id in selected]
        if len(selected_documents) != len(selected):
            return cls()
        expected = chunk_policy_documents(selected_documents)
        actual = [chunk.model_dump(exclude={"score"}) for chunk in context]
        if len({chunk.chunk_id for chunk in context}) != len(context) or sorted(
            actual, key=lambda row: row["chunk_id"]
        ) != sorted([chunk.model_dump() for chunk in expected], key=lambda row: row["chunk_id"]):
            return cls()
        if (
            len(context) > MAX_SOURCE_CHUNKS
            or len(
                json.dumps(
                    [chunk.model_dump(mode="json") for chunk in context], ensure_ascii=True
                ).encode()
            )
            > MAX_SOURCE_BYTES
        ):
            raise PolicyContextError()
        units = tuple(
            SourceUnit(
                source_id=f"s{index + 1}",
                policy_id=doc.metadata.policy_id,
                title=doc.metadata.title,
                body=doc.body,
                citation_ids=tuple(
                    chunk.chunk_id
                    for chunk in expected
                    if chunk.policy.policy_id == doc.metadata.policy_id
                ),
                provenance_json=(
                    doc.metadata.provenance.model_dump_json()
                    if doc.metadata.provenance is not None
                    else None
                ),
            )
            for index, doc in enumerate(selected_documents)
        )
        if (
            any(not unit.body.strip() for unit in units)
            or len(json.dumps([unit.__dict__ for unit in units], ensure_ascii=True).encode())
            > MAX_SOURCE_BYTES
        ):
            raise PolicyContextError()
        return cls(
            units,
            tuple(chunk.model_dump_json() for chunk in sorted(expected, key=lambda c: c.chunk_id)),
        )

    def selection_schema(self) -> dict[str, Any]:
        """Constrain only compact IDs, without repeating policy bodies in grammar enums."""
        schema = SourceSelection.model_json_schema()
        schema["properties"]["source_ids"]["items"] = {
            "type": "string",
            "enum": [unit.source_id for unit in self.units],
        }
        return schema

    def compiled_sources(self, packet: CompiledPolicyPacket) -> SourceCatalog:
        """Restrict generation to exact source bodies already authorized by compilation."""
        draft = GeneratedCompiledPolicyDraft.model_validate(
            packet.model_dump(
                include={
                    "required_steps",
                    "forbidden_patterns",
                    "architectural_expectations",
                    "test_expectations",
                    "style_constraints",
                    "insufficient_context",
                },
                exclude={
                    name: {"__all__": {"source_policy_ids"}}
                    for name in (
                        "required_steps",
                        "forbidden_patterns",
                        "architectural_expectations",
                        "test_expectations",
                        "style_constraints",
                    )
                },
            )
        )
        if not self.accepts_compiler(draft):
            return SourceCatalog()
        selected = []
        for constraint in packet.required_steps:
            unit = next(
                unit
                for unit in self.units
                if (
                    unit.body == constraint.statement
                    and list(unit.citation_ids) == constraint.citation_ids
                )
            )
            if constraint.source_policy_ids != [unit.policy_id]:
                return SourceCatalog()
            selected.append(unit)
        owners = {unit.policy_id for unit in selected}
        fingerprints = tuple(
            value
            for value in self.chunk_fingerprints
            if json.loads(value)["policy"]["policy_id"] in owners
        )
        return SourceCatalog(tuple(selected), fingerprints)

    def resolve(self, selection: SourceSelection) -> tuple[SourceUnit, ...]:
        """Reject unknown, repeated or inconsistent selections instead of repairing them."""
        if selection.insufficient_context:
            if selection.source_ids:
                raise ValueError("Insufficient source selection must be empty.")
            return ()
        if not selection.source_ids or len(set(selection.source_ids)) != len(selection.source_ids):
            raise ValueError("Source selection must contain unique supplied IDs.")
        by_id = {unit.source_id: unit for unit in self.units}
        if any(source_id not in by_id for source_id in selection.source_ids):
            raise ValueError("Source selection contains an unknown ID.")
        # Selectors are an unordered set. Output follows the catalog's stable source order.
        return tuple(unit for unit in self.units if unit.source_id in selection.source_ids)

    def compiler_draft(self, selection: SourceSelection) -> GeneratedCompiledPolicyDraft:
        """Materialize exact complete bodies once in the required source list."""
        selected = self.resolve(selection)
        return GeneratedCompiledPolicyDraft(
            required_steps=[
                GeneratedPolicyConstraint(statement=unit.body, citation_ids=list(unit.citation_ids))
                for unit in selected
            ],
            insufficient_context=not selected,
        )

    def preflight_draft(self, selection: SourceSelection) -> GeneratedPreflightDraft:
        """Materialize source text once, keeping summaries and rationale service-owned."""
        selected = self.resolve(selection)
        return GeneratedPreflightDraft(
            summary=EXTRACTIVE_SUMMARY,
            applicable_policies=[
                GeneratedPolicyGuidance(
                    policy_id=unit.policy_id,
                    title=unit.title,
                    rationale=EXTRACTIVE_RATIONALE,
                    citation_ids=list(unit.citation_ids),
                )
                for unit in selected
            ],
            plan_steps=[unit.body for unit in selected],
            citation_ids=list(
                dict.fromkeys(citation_id for unit in selected for citation_id in unit.citation_ids)
            ),
            insufficient_context=not selected,
        )

    def accepts_compiler(self, draft: GeneratedCompiledPolicyDraft) -> bool:
        """Validate every custom-provider statement with exact owner-bound source references."""
        if draft.insufficient_context:
            return False
        if len(draft.model_dump_json().encode("utf-8")) > MAX_OUTPUT_BYTES:
            return False
        if any(
            (
                draft.forbidden_patterns,
                draft.architectural_expectations,
                draft.test_expectations,
                draft.style_constraints,
            )
        ):
            return False
        seen: set[str] = set()
        for item in draft.required_steps:
            unit = next(
                (
                    unit
                    for unit in self.units
                    if (
                        unit.body == item.statement and list(unit.citation_ids) == item.citation_ids
                    )
                ),
                None,
            )
            if (
                unit is None
                or unit.source_id in seen
                or item.citation_ids != list(unit.citation_ids)
            ):
                return False
            seen.add(unit.source_id)
        return bool(seen)

    def accepts_preflight(
        self, draft: GeneratedPreflightDraft, *, allow_missing_references: bool = False
    ) -> bool:
        """Reject all free policy prose, altered metadata and incomplete whole-source outputs."""
        if len(draft.model_dump_json().encode("utf-8")) > MAX_OUTPUT_BYTES:
            return False
        if (
            draft.insufficient_context
            or draft.summary != EXTRACTIVE_SUMMARY
            or any((draft.implementation_guidance, draft.review_flags, draft.tests_required))
        ):
            return False
        selected: list[SourceUnit] = []
        if len(draft.plan_steps) != len(draft.applicable_policies):
            return False
        for statement, guidance in zip(draft.plan_steps, draft.applicable_policies, strict=True):
            matches = [
                unit
                for unit in self.units
                if (
                    unit.body == statement
                    and unit.policy_id == guidance.policy_id
                    and unit.title == guidance.title
                )
            ]
            unit = matches[0] if len(matches) == 1 else None
            if unit is None or unit in selected:
                return False
            selected.append(unit)
        if not selected:
            return False
        expected = self.preflight_draft(
            SourceSelection(
                source_ids=[unit.source_id for unit in selected], insufficient_context=False
            )
        )
        if allow_missing_references:
            if len(set(draft.citation_ids)) != len(draft.citation_ids) or not set(
                draft.citation_ids
            ) <= set(expected.citation_ids):
                return False
            return draft.model_copy(update={"citation_ids": expected.citation_ids}) == expected
        return draft == expected

    def prompt_payload(self) -> str:
        """Serialize each complete body once as untrusted source data."""
        return json.dumps(
            [
                {
                    "source_id": unit.source_id,
                    "policy_id": unit.policy_id,
                    "title": unit.title,
                    "body": unit.body,
                    "citation_ids": unit.citation_ids,
                    "provenance": json.loads(unit.provenance_json)
                    if unit.provenance_json
                    else None,
                }
                for unit in self.units
            ],
            ensure_ascii=True,
        )


@dataclass(frozen=True)
class SourceContext(Sequence[ScoredChunk]):
    """Keep the existing provider sequence contract with operation-local source ownership."""

    chunks: tuple[ScoredChunk, ...]
    catalog: SourceCatalog

    def __post_init__(self) -> None:
        """Reject a catalog/context mismatch at the typed dispatch boundary."""
        self.validate_binding()

    def validate_binding(self) -> None:
        """Recheck mutable chunk objects against the immutable captured source coordinates."""
        units_by_citation = {
            citation: unit for unit in self.catalog.units for citation in unit.citation_ids
        }
        if (
            len({chunk.chunk_id for chunk in self.chunks}) != len(self.chunks)
            or set(units_by_citation) != {chunk.chunk_id for chunk in self.chunks}
            or any(
                units_by_citation[chunk.chunk_id].policy_id != chunk.policy.policy_id
                or units_by_citation[chunk.chunk_id].title != chunk.policy.title
                for chunk in self.chunks
            )
        ):
            raise PolicyContextError(identity_mismatch=True)
        if (
            self.catalog.chunk_fingerprints
            and tuple(
                chunk.model_dump_json(exclude={"score"})
                for chunk in sorted(self.chunks, key=lambda c: c.chunk_id)
            )
            != self.catalog.chunk_fingerprints
        ):
            raise PolicyContextError(identity_mismatch=True)

    def __len__(self) -> int:
        """Expose the retained chunk count without copying source bodies."""
        return len(self.chunks)

    @overload
    def __getitem__(self, index: int) -> ScoredChunk:
        """Read one retained source chunk."""
        ...

    @overload
    def __getitem__(self, index: slice) -> tuple[ScoredChunk, ...]:
        """Read a retained chunk window."""
        ...

    def __getitem__(self, index: int | slice) -> ScoredChunk | tuple[ScoredChunk, ...]:
        """Preserve ordinary sequence reads for existing custom providers."""
        return self.chunks[index]
