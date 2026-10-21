# Source-Extractive Guidance Build Plan

## Summary

Issue [124](https://github.com/nnennandukwe/policyNIM/issues/124) retains a live refresh-cleanup result that invents batch sizes, a Go cancellation API and a refresh-token lifecycle rule despite valid citations. Compiler output also introduces an unsupported JSON-only logging rule. This build will replace authoritative synthesis with bounded selection of whole policy bodies from the held generation. The baseline is `39ced7bc3f9a6b0d15c10713511d711322321483`; one PR will preserve provider reservation accounting and public result schemas.

## Skills To Use

| Stage | Skill | Responsibility and evidence |
|---|---|---|
| Planning | software-build-plan | Pin authority, source ownership and compatibility before edits. |
| Rules | qodo-get-rules | Reuse retrieved guidance/schema and Python rules; require matching consumers, typed boundaries and function docstrings. |
| Implementation | python-pydantic-pyright | Validate compact internal selectors without widening public result models. |
| Failure paths | failure-path-testing | Exercise actual compiler/preflight/custom-provider rejection and regeneration. |
| Interface audit | dx-audit | Explain extractive output semantics, refusal and operational budgets. |
| Delivery | qodo-review, qodo-review-resolver | Review the complete verified diff and exact PR head; retain supported findings until fixed. |
| Writing | voice-canon | Verify plan, guide and final authored text. |

Qodo Local Review is eligible: this worktree has the connected repository and a pushed baseline. Transaction-proof doesn't apply because this build won't mutate durable storage.

## Scope

In scope: internal source units and selector grammar, NVIDIA compiler/generator adapters, service guards, held-snapshot plumbing, focused regressions and a user guide.

Out of scope: independent semantic judging, source trust certification, arbitrary generated advice, new public response fields, case-hash migrations, release packaging, hosted startup and quota lifecycle changes.

## Package Layout / File-to-Task Mapping

- New `src/policynim/source_units.py`: bounded immutable source catalog, selectors and exact materialization/validation.
- `src/policynim/services/router.py`: expose full documents only from its held generation; don't reconstruct bodies from chunks.
- `src/policynim/services/compiler.py`: bind source catalog, validate every compiler statement and citation.
- `src/policynim/services/preflight.py`: validate every authoritative string, fixed summary and source-bound metadata.
- `src/policynim/services/regeneration.py`: preserve the same guard for every attempt.
- `src/policynim/providers/nvidia.py`: request compact source IDs and materialize validated selections locally.
- New focused tests and `docs/source-extractive-guidance.md`: retained failure witnesses, controls and limits.

## Dependencies And Settings

No new dependencies, environment variables or storage versions. Python3.11/3.12 and the locked dependency graph remain. `CompiledPolicyPacket`, `PreflightResult`, `EvalCaseResult` and their nested serialized shapes won't change. Internal provider selection models may change. The effective dispatch configuration, immediate durable provider reservation and known-unsent compensation remain unchanged.

## Canonical Contracts

Each authoritative statement must equal a complete policy body supplied by the held snapshot, with exact canonical owner/title and that body's complete citation set. A body appears once in the authoritative output. Models will select compact unit IDs rather than generate repeated bodies. Summary and rationale will use fixed service-owned explanatory text, never model prose. Task text may describe intent but can't become policy authority.

Missing complete source ownership, an incomplete context, a foreign ID, invented metadata, an altered condition, a free summary/rationale or an exceeded budget will fail closed. Services won't repair an unsupported custom-provider claim. Validation will precede compiled-source merging so duplicate or invented provider data can't be normalized into an accepted result. Missing derived top-level references may still be filled from validated canonical owner references. Public JSON shapes remain compatible; the meaning changes from synthesized instructions to source quotations and will be documented.

## TDD And BDD Implementation Strategy

Test actual compiler/preflight services with controlled providers and a pinned public generation. First retain failures for invented numbers, Go API, title-only JSON citation and shifted authentication condition. Then prove exact full-body acceptance for canonical and custom headings, cross-chunk conditions and independent selected policy IDs. Add SDK wire grammar checks and malformed/custom-provider rejection. Preserve a historical case digest and regeneration guard behavior.

## Component Design

The router owns the index lease and its validated generation snapshot. Its complete document catalog will be the only authoritative source, restricted to already selected effective IDs and the retained chunk inventory. An internal typed Sequence wrapper will carry the immutable catalog alongside the existing chunk sequence during each provider call. Services will rebind it from the compiler service's held snapshot at preflight and regeneration boundaries and recheck ownership after custom-provider calls. Provider protocol signatures will remain stable. No source will be loaded from mutable corpus paths after pinning.

Internal selectors will contain bounded lists of compact IDs, without free text or repeated body enums. The adapter will resolve these IDs to existing draft models, while service validation will independently compare every returned value against the same catalog. Source bodies retain full normalized Markdown content and conditions. Canonical chunk fingerprints will detect lost or mismatched ownership before dispatch, and pinned provenance will retain applicability/requirement-level input. Fixed output placement will prevent repeating a whole body across categories merely to fill every list.

The existing 80-chunk and 256-KiB serialized-context caps remain. Whole source units will have a 256 KiB budget; serialized public packet/result values will have a separate 1 MiB cap because they also retain discovery excerpts and citation metadata. No complete unit will be assembled by joining overlapping or incomplete chunks.

Explicit NVIDIA extractive mode stops after one failed judgment with the existing `no_material_trigger` reason because another accepted attempt can't revise the selected bodies. Its Guardrails wrapper preserves that capability. Custom generators retain their retry behavior unless they explicitly opt into the same mode; public attempts and stop enums stay unchanged.

## Failure And Recovery Rules

Unsupported statements or metadata return the existing insufficient-context outcome with a fixed refusal reason; they won't be recast as authoritative text. Missing snapshot ownership or source budgets stop before chat dispatch. The caller can narrow selected policies or publish a complete source generation; it can't bypass guards by supplying citations alone. No persisted report or previous case is rewritten.

## Commit Plan

1. `test(grounding): retain unsupported compiler and preflight witnesses`
2. `fix(grounding): select and validate whole pinned policy sources`
3. `test(grounding): verify SDK selection grammar and historical compatibility`

## Branch And PR Flow

Use isolated branch `fix/source-extractive-guidance` from the pinned baseline. Run focused checks and the full offline suite, then Qodo Local Review with context against the pushed baseline. Reproduce supported findings, fix them and repeat verification/review after any source change. Open a ready PR linked to124 only after local gates. Don't merge until119/130 dependencies are natively merged or the maintainer routes a reviewed consolidation. Never force-push or manually alter Qodo finding state.

## Test Plan

Run `uv run --no-sync ruff check .`, `uv run --no-sync ruff format --check .`, `uv run --no-sync pyright`, `uv run --no-sync pytest -q -m 'not live and not docker_live'` and `uv lock --check`. Focused tests will cover full source controls, all free-text fields, invalid selectors, unknown/partial contexts,80-chunk/256KiB limits, bounded output and historical case serialization.

After offline and local-review gates, run one bounded live endpoint/task acceptance. Preserve the earlier bad receipt and all new output. A passing quote-fidelity check won't be described as a semantic-quality or production pass.

## Definition Of Done

The retained unsupported compiler and generator outputs are rejected through the real service path. Valid selected whole bodies retain their full conditions and custom headings. SDK requests use compact bounded selectors. Every provider faces the same service guard. Historical case hashes and quota request accounting remain unchanged. Required checks and final local/exact-head review complete with supported findings fixed. The guide states the behavior change and its limits.

## Assumptions And Defaults

Quote fidelity proves only agreement with supplied source text. It doesn't prove applicability, correct category selection, trustworthy policy authorship, resistance to malicious policy content or universal semantic correctness. Generated advice outside these authoritative fields needs a separately versioned contract and isn't part of this build. Existing custom providers that offer only chunks will need complete source ownership or receive a fail-closed result. The synthetic offline evaluator will receive explicitly authored full source units through constructor composition; production factories won't use that injection or any environment fallback. The independent evaluator will exempt only byte-identical full source quotations with validated owner/title/complete citation coordinates from narrative ID detection, while retaining unmatched-prose and structured-ID checks. Gold expectations won't change.

NVIDIA's [structured-generation documentation](https://docs.nvidia.com/nim/large-language-models/1.15.0/structured-generation.html) describes JSON Schema guided decoding but doesn't enumerate support for every keyword on the hosted model. The request grammar will use compact string enums, required fields, bounded arrays and forbidden extra fields. Local validation will enforce uniqueness and cross-field consistency without depending on a `uniqueItems` extension. The bounded endpoint acceptance remains necessary for the actual configured model.
