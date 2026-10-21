# Generation Readiness Cache Build Plan

## Summary

Issue #150 will separate readiness metadata from reusable generation bodies. The baseline is native main `ac9ad3e024fc691365d17fe0810a269aba8def95`; one PR will change only in-memory retention and its regressions. Artifact validation, reader admission and durable publication won't change.

## Skills To Use

| Stage | Skill | Responsibility and evidence |
|---|---|---|
| Planning | software-build-plan | Bound this cache-only change and pin the public witnesses. |
| Rules | qodo-get-rules | Apply retrieved cache-scope rules and previously loaded Python quality rules: descriptive names, ASCII authored strings and function docstrings. |
| Readiness rejection | failure-path-testing | Preserve rejection of changed artifacts through the public readiness path after mixed-size churn. |
| Final review | qodo-review / qodo-review-resolver | Review the full final diff, then require completed exact-head PR review and CI. |

Qodo Local Review is eligible: this Git worktree has the connected repository and pushed baseline. Native Ruff/Pyright cover the small typed Python change. Transaction-proof isn't selected because no durable-state protocol or file ownership changes. Developer commands, schemas, settings and errors won't change.

## Scope

In scope: independently bounded readiness metadata and reusable bodies; retained-reader/readiness regressions; cache budget documentation.

Out of scope: new caching dependencies, configurable budgets, latency claims, semantic policy changes, source-extractive PR158, URL PR160 and hosted replay.

## Package Layout / File-to-Task Mapping

- `src/policynim/services/generations.py`: keep metadata-only entries outside the body LRU.
- `tests/test_policy_generations.py`: public mixed-size retention and rejection witnesses; this file already runs in native Windows CI.
- This new plan: retain the capacity and evidence boundary.

## Dependencies And Settings

Runtime/development dependencies, storage versions, environment variables and precedence stay unchanged. Python 3.11/3.12 remain supported. Bodies retain eight entries and a 32 MiB serialized-body budget; readiness retains at most eight metadata-only entries independently.

## Canonical Contracts

A warm small `GenerationStore.pin(id)` remains reusable after reading a body too large for the body cache. Public `readiness(id)` still admits a lease, checks artifact identities and probes SQLite readability. A changed artifact forces validation/rejection, regardless of either cache. Cache hits never own reader handles or share mutable models with callers.

## TDD And BDD Implementation Strategy

Use actual published generations and `pin`/`readiness` calls. First reproduce additional sealed-artifact reads after an oversized retained reader with reduced test budgets. Then isolate metadata capacity and retain the warm body. Follow with bounded churn and changed-source rejection; keep the existing warm-health/no-clone and concurrency controls.

## Component Design

GenerationStore keeps ownership. The existing cache lock and striped validation locks remain. Metadata-only records enter a separate bounded LRU; bodies enter the existing byte/entry-bounded LRU only when eligible. Readiness may reuse verified metadata from either cache, without evicting a body to store metadata. Keys retain root, generation and embedding identity; artifact tokens must still match. No new manager or public abstraction is needed.

## Failure And Recovery Rules

Changed or unavailable artifact stamps continue through full validation. Corrupt artifacts continue raising GenerationError; the previous pointer and object bytes stay untouched. Readers retain their own leases and callers receive independent model copies. Normal public inspection/recovery remains the operator path; no cache-flush command is added.

## Commit Plan

One behavior-closed commit will include the isolated cache change, failing-before regressions and this plan.

## Branch And PR Flow

Use `fix/generation-readiness-cache` from the pinned main baseline. Reproduce first, implement, run focused/static/offline checks, then run context-bearing Qodo Local Review against the pushed baseline. Fix supported findings and rerun affected checks/review before a ready PR linking #150. Push without force, attach the PR and collect matching-head remote review plus native CI. Merge only after the release coordinator routes adoption; close #150 only with native merge proof.

## Test Plan

The main witness observes actual sealed-artifact reads, rather than a private cache-hit counter. Reduced budgets make mixed-size eviction deterministic without large resources. Capacity checks may inspect private containers only to assert bounded metadata/body retention. Existing artifact-tamper, embedding, concurrent validation and lease controls stay in the focused suite. Native Windows CI must run this test file; local macOS results aren't Windows proof.

Use the existing dependency environment without synchronization, explicit `PYTHONPATH` for this worktree, Ruff, Pyright, lock check and offline pytest excluding live/Docker opt-ins. No paid provider call or production latency measurement is needed.

## Definition Of Done

The public eviction witness fails before and passes after; both capacities stay bounded; changed bytes still fail closed. Focused/static/offline checks pass. Final local review has full coverage with supported findings resolved; PR review matches its current head and required CI passes. Frozen sibling branches and hosted helpers stay untouched.

## Assumptions And Defaults

The existing eight-entry metadata capacity remains sufficient for bounded process-local reuse. Metadata churn may require validation when neither cache retains a generation. This is a capacity fix, not a production speed measurement or an integrity-policy change.
