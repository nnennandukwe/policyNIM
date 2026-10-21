# Capacity Active-Probe Evidence Build Plan

## Summary

This #103 successor will require evidence that successful readiness and discovery probes overlapped active work in normal load scenarios. Native main `c35f18c79bf8dd48daf72e46e0d5f1b46d45484c` is the baseline. Its harness and harness tests match frozen #130 exactly. One PR will expose and apply an existing paired-probe signal; final capacity runs will stay held for the final release source.

## Skills To Use

| Stage | Skill | Responsibility and evidence |
|---|---|---|
| Planning | software-build-plan | Bound the measurement contract and preserve sibling work. |
| Rules | qodo-get-rules | Retrieve telemetry-scope guidance and reuse Python docstring/ASCII/naming standards. |
| Gate regression | failure-path-testing | Prove zero-active and before-only probes can't certify active-load measurement. |
| Report/docs audit | dx-audit | Read the changed report and runbook together; disclose source-only audit limits. |
| Final review | qodo-review / qodo-review-resolver | Review the final whole diff, then collect exact-head remote review and native CI. |

Qodo Local Review is eligible against the connected pushed baseline. Native Ruff/Pyright cover the typed Python change. No transaction-proof workflow is needed: durable admission, ownership and cleanup protocols won't change.

## Scope

In scope: expose the existing paired active-work probe signal; require it for baseline, saturation and slow-provider completeness; actual SQLite positive/negative controls; affected report documentation.

Out of scope: timing increases, new provider or hosted calls, final 25-user/10,000-chunk runs, production capacity claims, managed-generation benchmarking, jobs/compile/preflight workloads, #130/#158 worktrees and unrelated telemetry.

## Package Layout / File-to-Task Mapping

- `scripts/measure_hosted_capacity.py`: expose paired-probe evidence and apply it to relevant load scenarios.
- `tests/test_hosted_capacity_harness.py`: real SQLite reservation transitions around the public measurement driver's probes.
- `docs/hosted-capacity.md`: describe completeness and the remaining workload limits.
- This new plan: retain the bounded contract and verification strategy.

## Dependencies And Settings

No runtime/development dependencies, schemas, environment variables or configuration precedence change. Python 3.11/3.12 and the existing POSIX harness remain supported. Existing delays, sampling cadence, deadlines, active limits and scenario sizes stay unchanged.

## Canonical Contracts

Each scenario will report `paired_active_work_probe_observed`. It becomes true only when one probe cycle reads active work before and after successful discovery and health responses. Baseline, saturation and slow-provider measurements require that signal, a final report and workload state observations. Locked-database intentionally has no admitted work and won't require it. Disconnect/shutdown retain their stricter pre-intervention and drain requirements. Incomplete evidence stays in the JSON report and yields a nonzero overall harness exit.

Historical receipts remain readable evidence with their original fields and revisions; they won't be rewritten or relabeled as satisfying the new gate. No latency threshold or operating envelope will be invented.

## TDD And BDD Implementation Strategy

Drive the actual measurement function with deterministic RPC/health boundaries and real SQLite reservations. Hold a request until a complete probe cycle finishes. For before-only controls, finish the real reservation during discovery; for zero-active controls, never reserve work. Both must fail completeness before the fix. A paired control keeps work active across both observations. Parameterize all three relevant load scenarios, then implement the smallest signal exposure/gate.

## Component Design

Reuse the current `active_probe_observed` event, which already requires paired state samples and successful cheap responses. Keep readiness/HTTP timers, state observation and final/drain observations separate. No new probe framework, source capture, private label or service allocation is needed. #130 owns its frozen source and won't absorb this successor while its gates run.

## Failure And Recovery Rules

Unpaired, failed or zero-active probes must retain incomplete evidence rather than become a supported-load claim. State-read failures and unavailable final reports retain their current safe categories. No reservation, outcome, timeout or drain behavior changes. The operator preserves every failed report, investigates the missing observation and doesn't retry until green or replace the original receipt.

## Commit Plan

One behavior-closed commit will include the paired-probe contract, failing-before controls and affected docs.

## Branch And PR Flow

Use `fix/capacity-active-probe-evidence` in the reused, clean cache worktree; preserve the completed cache branch and receipts. Keep #130/#158 untouched. Run focused/static/offline checks, a source-only DX audit and final context-bearing Qodo Local Review before opening a ready PR linked to #103. Collect matching-head remote review and all CI; root owns merge. Final profile commands stay disabled until the final release source is approved.

## Test Plan

The negative controls will observe actual reservation counts around mocked HTTP boundaries without a provider call. Existing real-process small-profile tests will cover integration, locking, disconnect and shutdown. Run Ruff, formatting, Pyright, lock check and the offline suite. Native CI will cover the actual pushed head; local tests won't claim production or Windows capacity.

## Definition Of Done

All six zero-active/before-only load combinations fail before and pass after the gate correction; three paired controls succeed. The signal is reported explicitly, locked-database remains valid without active work, and intervention guarantees stay intact. Focused/static/offline checks and complete final local review pass; exact-head remote review/CI are collected. No final capacity profile or provider/hosted request is performed.

## Assumptions And Defaults

A paired probe proves overlap within that observed cycle, not continuous load or an SLO. The harness still measures short synthetic search work on a legacy SQLite index with patched health/search factories; managed generation routing, publication and durable job backlog remain separate acceptance limits.
