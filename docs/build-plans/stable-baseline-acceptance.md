# Stable Baseline Acceptance Build Plan

## Summary

- Outcome: reconcile roadmap #88's mandatory acceptance criteria with retained delivery evidence, then update its status when the evidence supports closure.
- Authority: [#88](https://github.com/nnennandukwe/policyNIM/issues/88), its linked issue criteria, and the requested acceptance reconciliation.
- Starting baseline: `75250b3b79b4185bd39a7847f194ce5d200b48d3`.
- Review unit: one documentation PR and evidence-backed tracker reconciliation.
- Primary boundary: optional [Tasks #102](https://github.com/nnennandukwe/policyNIM/issues/102) will stay open while released SDK support and actual client negotiation remain unavailable.

## Skills To Use

| Stage | Skill | Responsibility and evidence |
| --- | --- | --- |
| Requirements | `software-build-plan` | This plan will bind edits and checks to the accepted criteria. |
| Context and rules | `qodo-get-rules`, `qodo-codebase-wisdom` | Retrieved standards and the pinned compatibility page will guide historical identity and public-contract preservation. |
| Documentation | `voice-canon` | Plain-English review and the language checker will cover authored documents and tracker copy. |
| Verification | `dx-audit --focus docs` | The review will compare acceptance/status wording with the original criteria and delivery records, within this program's scope. |
| Review and delivery | `qodo-review`, `qodo-review-resolver` | Final local review, completed hosted review at the PR head, relevant checks, guarded merge and post-merge CI will gate delivery. |

Coverage notes: runtime, storage, workflow-state and publication logic won't change. Language implementation, TDD, failure-path-testing, workflow-invariants and transaction-proof aren't triggered by this documentation unit. Existing behavior tests and retained failure/recovery receipts will supply the acceptance evidence. Qodo Local Review is eligible: this isolated Git worktree has a connected remote and pushed base `75250b3`.

## Scope

In scope:

- Map all six original parent completion criteria and linked mandatory issue criteria to tested/reviewed/merged evidence.
- Correct stale acceptance wording in the roadmap and compatibility matrix.
- Record the current Tasks prerequisite check, preserve synchronous/application jobs, and reconcile tracker checkboxes only after the documentation passes delivery gates.

Out of scope: new features, redesign, new milestones, a general audit, model-quality experiments, package publication, manual live provider calls, account changes and manual deployment operations.

## Package Layout / File-to-Task Mapping

- `docs/evidence/stable-baseline-acceptance.md` (new): criterion matrix, revision identities, receipt limits and remaining optional gate.
- `docs/roadmap.md`: maintained summary and link to the acceptance matrix.
- `docs/release-compatibility.md`: actual dated client and OAuth results.
- `docs/hosted-authorization.md`, `docs/evaluation-jobs.md`, `deploy/keycloak/README.md`: correct the same stale OAuth status in directly related operator guidance.
- `docs/build-plans/stable-baseline-acceptance.md` (new): this review unit's design and verification contract.
- `.codex/acceptance-88/` (ignored): unchanged source snapshots, fresh read-only checks, hashes and review/check receipts.

## Dependencies And Settings

Runtime/development dependencies, schemas, storage versions, environment variables and configuration precedence will stay unchanged. The runtime remains Python 3.11/3.12 with the existing lockfile. Existing project environments will be used without syncing or modifying their installed packages. Repository CI will run the large supported platform/package/Docker matrices because local disk has about 3 GiB free.

## Canonical Contracts

Every completion statement will name its evidence class and tested revision. Historical source, policy-generation, artifact, client, model and deployment identities will remain separate. Published targets won't become measured production capacity. Provider doubles won't become live NVIDIA or model-quality evidence.

Compatibility: no CLI/MCP signature, schema, artifact format, policy source or generation will change. Historical records and failed attempts will remain unchanged. Optional Tasks capability won't be advertised.

## TDD And BDD Implementation Strategy

This documentation change will use verification before editing, without new implementation-mirroring tests.

1. Reproduce stale status through the current roadmap/compatibility pages and current issue/PR records.
2. Read acceptance criteria, delivery receipts and relevant existing behavioral tests; check source ancestry and retained receipt hashes.
3. Write the matrix and correct only supported status wording.
4. Run existing documentation/content and relevant acceptance checks, the language checker and repository CI.

## Component Design

The evidence matrix will own this dated reconciliation. The roadmap will link to it; the compatibility page will summarize actual dated client checks. Original issues and delivery receipts will remain the underlying sources. Tracker edits will follow the merged evidence document rather than replacing historical comments. No new runtime abstraction or evidence schema will be introduced.

## Failure And Recovery Rules

- Missing or conflicting mandatory evidence will keep the relevant criterion open and identify the exact missing check.
- A failed, stale or incomplete review won't be treated as passed. Its receipt will remain available for bounded recovery.
- The existing PowerShell host failures will stay explicit; this unit won't repair the host.
- An unavailable Tasks prerequisite will leave #102 open with the owning upstream sources and a usable fallback.
- Normal repository-triggered preview/production deployment is covered by the existing permission. Merge checks will inspect those triggers; publication workflows won't be manually invoked.

## Commit Plan

1. `docs: reconcile stable baseline acceptance evidence` will contain the matrix and supported status corrections.
2. Any supported review correction will be a separate commit with affected verification repeated.

## Branch And PR Flow

1. Use isolated branch `codex/stable-baseline-acceptance-88` from the pinned base and preserve the original dirty checkout.
2. Commit only this unit's files, run checks and prepare review context with #88/#97/#103/#104/#102 links and evidence limits.
3. Run final auto Qodo Local Review against the pushed base, assess every finding, fix supported issues, reverify and review changed work again.
4. Push and open one ready-for-review PR linked to #88. Require completed hosted review at its current head, no supported actionable open findings and passing required checks. Finding statuses won't be manually changed.
5. Recheck the head and gates, merge guarded by that reviewed SHA, verify the merged tree and post-merge CI, then reconcile the tracker from the merged matrix.

## Test Plan

Focused checks will cover the current documentation contracts, source/catalog/scenario bindings, generation recovery, HTTP authorization, grounded MCP resources and retained job lifecycle evidence as needed. These tests will use fixtures; the fresh hosted readback will use only health, public metadata and missing-token refusal.

```bash
python3 /Users/nnennandukwe/.agents/skills/voice-canon/scripts/verify_language.py docs/build-plans/stable-baseline-acceptance.md docs/evidence/stable-baseline-acceptance.md docs/roadmap.md docs/release-compatibility.md
uv run --no-sync ruff check .
uv run --no-sync pyright
uv lock --check
git diff --check
```

Existing Python 3.11/3.12 offline suites, installed-package platform jobs, optional SDK jobs, Windows checks and offline Docker checks will run in established CI. Retained original client, OAuth, rollback and live-provider receipts will be checked without repeating their external operations. Final local Qodo review will cover the complete committed change set; runtime/access failures will remain explicit if recovery can't succeed.

## Definition Of Done

- Every mandatory parent criterion will have source-linked, revision-bound evidence or a named unresolved blocker.
- Status wording will agree with the evidence, with optional Tasks and operational limits explicit.
- Authored language and relevant checks will pass; completed exact-head local/hosted reviews and CI will support the merged documentation.
- Tracker reconciliation will preserve historical text and leave #102 open.
- No unapproved model traffic, publication, account mutation or unrelated work will enter this build.

## Assumptions And Defaults

The original child acceptance criteria define the work. A closed child issue alone won't establish completion. Dated actual-client and live-provider results will keep their observed scope; later source CI won't relabel them as new runtime checks. This reconciliation will close the stable program only if all mandatory criteria are supported.
