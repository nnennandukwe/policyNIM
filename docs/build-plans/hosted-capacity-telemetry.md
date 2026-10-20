# Hosted Capacity and Telemetry Build Plan

## Summary

Contribute to #103 with bounded, content-free operational reporting and a reproducible real-process HTTP load harness. Stack on the reviewed #98 operation ledger. Synthetic doubles don't establish live NVIDIA or production capacity; root owns deployed acceptance.

## Skills To Use

| Stage | Skill | Responsibility |
| --- | --- | --- |
| Design | software-build-plan | Define measured behavior and evidence boundaries before implementation. |
| Standards | qodo-get-rules | Reuse current repository layer/type/docstring requirements and retrieve telemetry-specific guidance. |
| Storage | sqlite-concurrency-hardening, transaction-proof | Retain today's charges and active rows during bounded terminal-history cleanup. |
| Failure | failure-path-testing | Cover locked state, saturation, slow provider, disconnect and shutdown through real HTTP. |
| Interface | dx-audit | Compare report fields, CLI recovery and operator guidance. |
| Review | qodo-review, qodo-review-resolver | Eligible repository; complete final local review and pushed-head review plus CI. |

## Scope

In scope: aggregate active/outcome/latency report, retention, opaque correlation IDs, raw-exception log removal, measured deterministic HTTP harness. Out of scope: production measurements, private text analytics, live issuer login, autoscaling, financial billing and changing evaluation-job ownership (#101).

## Package Layout / File-to-Task Mapping

- `storage/operation_store.py`: bounded terminal retention and aggregate snapshot.
- `services/operation_admission.py`: operation correlation and retention configuration.
- `operations.py`, `interfaces/mcp.py`: correlate safe stage/tool events and remove raw callback/startup exception logging.
- `interfaces/cli.py`: operator-only local `beta-admin operations` report.
- `scripts/measure_hosted_capacity.py`: real-process HTTP synthetic-provider measurement and failure scenarios.
- Tests and operator docs: report/redaction/retention and harness verification.

## Dependencies And Settings

No dependencies added. Operation ledger migration adds opaque correlation identity; retention defaults to 30 days and preserves active rows and current-day usage. Metrics expose bounded aggregates, not principal labels. Existing log sink retention stays an operator deployment setting.

## Canonical Contracts

`policynim beta-admin operations` emits only aggregate JSON, active counts, outcomes, sampled latency window and explicit unavailable provider totals. Missing/unreadable state fails with sanitized recovery guidance and never creates a database. Synthetic capacity reports name exact Git revision, environment, model-double configuration, chunks/domains, scenarios, latency percentiles, throughput and failure outcomes. Unknown/live evidence remains explicit.

## TDD And BDD Implementation Strategy

Use real SQLite retention/report tests, captured-log redaction tests and a subprocess server with actual MCP HTTP dispatch, quota admission and sqlite-vec retrieval. Provider calls use deterministic delays/failures. No live model calls. Every report carries measured configuration and source revision.

## Component Design

Local CLI delegates reporting to a service; storage returns typed aggregate models. Stage/tool correlation uses an internally generated opaque ID; raw client request IDs are hashed before logs. Admission/pruning share SQLite ownership and terminal rows older than retention alone may be deleted. Bounded samples limit percentile computation. Existing provider event usage remains measured-or-unknown and isn't added across stage/attempt latency.

## Failure And Recovery Rules

| Event | Expected state and recovery |
| --- | --- |
| Report read/open failure | No mutation; sanitized error tells operator to inspect the state volume. |
| Cleanup interruption or writer lock | Transaction rollback preserves usage; retry cleanup after lock repair. |
| Concurrent admission/cleanup | Immediate transaction preserves current-day allowance and active rows. |
| Malicious exception or request ID | Logs contain opaque ID and stable error class, never input text, token, path or traceback source. |
| Saturation/slow provider | Accepted work stays bounded; cheap discovery remains independently measurable. |
| Disconnect/shutdown | Workers drain under existing deadlines; persisted outcomes/report retain accepted work. |

Commit point: SQLite COMMIT of retention deletion. Canonical state is every active row plus retained terminal history; current-day charges are never eligible for cleanup. Cleanup is idempotent and can't refund current usage. Lifecycle covers create/inspect, historical insertion, cleanup/reopen, current/active preservation, writer lock rollback and interrupted/retry behavior.

## Commit Plan

1. Add aggregate reporting, bounded retention and safe correlation/redaction tests.
2. Add reproducible HTTP capacity harness, measured fixture report and runbook.

## Branch And PR Flow

Use `codex/hosted-capacity` in a separate worktree stacked on the quota commit. Keep quota review evidence separate. Run focused/full gates, final local Qodo against the pushed parent/base, reproduce/fix findings, then PR and exact pushed-head Qodo/CI. Root coordinates merge and deployed measurements.

## Test Plan

Run repository Ruff, Pyright and offline pytest gates. Run the harness with 25 users, a 10-operation cap, 10,000 deterministic chunks and named fault scenarios; preserve the generated report with precise synthetic labels. Run a small smoke harness in offline tests. Don't infer production health/readiness from the fixture's generated corpus.

## Definition Of Done

Operator reports contain no principal/query/secret fields; retention preserves active/current-day usage; logs pass redaction regressions; real HTTP fault outcomes are measured and reproducible. Required gates and final reviews pass. #103 stays open while dependent generation/job integration or deployed acceptance is missing.

## Assumptions And Defaults

Single instance and persistent SQLite volume only. Synthetic embedding dimensions and provider delays are reported. Host memory measurements use process RSS when available; absent measurements remain unavailable. No unsupported claim follows from a successful unit suite.
