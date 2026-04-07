# Runtime Decision Evidence MVP Day 5 Execution Plan

## Summary
- Day 5 is the hardening, regression, and operator-docs slice: complete the missing runtime test matrix, verify the runtime evidence path against real SQLite-backed flows, rerun the existing `ingest` / `search` / `preflight` / `eval` / MCP regression surfaces, and bring authoring and operator docs up to the same maturity as the shipped CLI/runtime behavior.
- Keep the existing architecture boundaries intact: CLI owns transport and interactive prompting, services own orchestration, storage owns SQLite persistence, typed models remain the shared contract, and docs/env examples describe the existing settings rather than inventing a second configuration surface.
- This day should still be implemented test-first. Add the missing behavior and docs-parity tests before making code or docs changes.
- Use the repo's established day-plan structure and persist it as `.plan/runtime-decision-evidence-mvp/day-5-execution-plan.md`.
- As of April 6, 2026, the current baseline is stable: the runtime-targeted suite passed `82` tests and the broader regression slice passed `114` tests. Day 5 should extend coverage and docs from that known-good baseline, not reopen scope.

## Skills Used
- `failure-path-testing` is primary. Day 5 is about closing remaining matrix gaps around runtime execution outcomes, CLI input behavior, confirmation paths, and durable evidence/report flows.
- `dx-audit` is primary. Operator-facing runtime commands and docs need to be explicit about request shape, exit behavior, session reporting, and SQLite usage without leaking implementation noise.
- `cli-interactive-parity` is primary. The runtime CLI contract, help text, JSON I/O behavior, env examples, and docs should all describe the same command surface.
- `policynim-settings-contract-parity` is primary. Day 5 updates `.env` examples and contributor docs, so settings names, defaults, and docs must stay aligned.
- `sqlite-concurrency-hardening` applies secondarily. The evidence store already exists, but Day 5 should verify round-trip behavior across all runtime action kinds and keep the append-only SQLite contract explicit.
- `audit-boundaries` applies secondarily. Keep doc changes in docs/templates, test changes in test files, and runtime behavior fixes narrow and service-owned if new coverage exposes a real gap.
- `audit-abstractions` applies secondarily. Do not introduce a new docs framework, runtime inspection API, or generic test harness layer just to satisfy Day 5.

## Scope
- Sync local repo with `origin/main`.
- Create `feat/runtime-day5-hardening-docs`.
- Complete the runtime evidence MVP test matrix across:
  - runtime execution outcomes
  - SQLite-backed evidence-store round trips
  - session-summary reporting
  - CLI execution and reporting flows
- Add at least one real end-to-end CLI runtime flow that executes against a temporary SQLite evidence DB and then verifies `evidence report --session-id`.
- Re-run the existing regression surfaces for:
  - `ingest`
  - `search`
  - `preflight`
  - `eval`
  - MCP
  - service factories and settings/types
- Update operator-facing docs for:
  - `runtime_rules` authoring
  - runtime request authoring and execution
  - `session_id` and exit-code behavior
  - SQLite evidence storage location and supported inspection workflow
  - runtime-related env vars in shipped `.env` examples
- Update test coverage documentation to reflect the final Day 5 matrix.
- Do not add new CLI commands, MCP runtime commands, raw event-dump commands, SQLite schema changes, or hosted/shared-runtime features on Day 5.

## Package Layout
- Existing runtime code reused and only edited if new tests expose a real gap:
  - `src/policynim/interfaces/cli.py`
  - `src/policynim/services/runtime_execution.py`
  - `src/policynim/services/runtime_evidence_report.py`
  - `src/policynim/storage/runtime_evidence.py`
- Runtime and regression tests:
  - `tests/test_runtime_execution_service.py`
  - `tests/test_runtime_evidence_store.py`
  - `tests/test_runtime_evidence_report_service.py`
  - `tests/test_cli.py`
  - new `tests/test_docs_runtime_workflows.py`
  - existing regression files remain the verification set, not the main edit surface
- Operator-facing docs and templates:
  - `docs/workflows.md`
  - `docs/contributor-guide.md`
  - `policies/TEMPLATE.md`
  - `.env.example`
  - `.env.development.example`
  - `.env.production.example`
  - `tests/README.md`
- Planning docs:
  - new `.plan/runtime-decision-evidence-mvp/day-5-execution-plan.md`

## Dependencies And Settings
- Add no new third-party dependency on Day 5.
- Reuse the current runtime settings and defaults:
  - `POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH`
  - `POLICYNIM_RUNTIME_EVIDENCE_DB_PATH`
  - `POLICYNIM_RUNTIME_SHELL_TIMEOUT_SECONDS`
- Do not change the existing settings names or defaults unless a Day 5 test reveals a contract bug.
- Bring the shipped `.env` examples and contributor docs into parity with the already-supported runtime settings.
- Keep the SQLite evidence DB separate from hosted beta auth storage and keep `evidence report` backed by the existing runtime evidence DB path.

## Canonical Contracts
- Runtime command surface stays unchanged:
  - `policynim runtime decide --input <path|->`
  - `policynim runtime execute --input <path|->`
- Evidence command surface stays unchanged:
  - `policynim evidence report --session-id <id>`
- Input contract stays unchanged:
  - both runtime commands accept one `RuntimeActionRequest` JSON object
  - `--input <path>` reads UTF-8 JSON from a file
  - `--input -` reads UTF-8 JSON from stdin
  - non-object JSON, empty input, invalid JSON, and schema mismatches remain explicit CLI errors
- Execution contract stays unchanged:
  - `runtime execute` always prints `RuntimeExecutionResult` JSON
  - `runtime execute` always includes the resolved `session_id`
  - exit code `0` remains limited to `allowed` and `confirmed`
  - exit code `1` remains used for `blocked`, `refused`, and `failed`
- Reporting contract stays unchanged:
  - `evidence report` remains summary-only over one session
  - raw SQLite row inspection is documented as an operator debugging aid, not as a formal public API
- Authoring contract to document explicitly:
  - `runtime_rules` is an optional frontmatter list
  - each rule uses `action`, `effect`, `reason`, and exactly one matcher family
  - authored `effect` values remain `confirm` or `block`
  - `allow` remains a decision outcome for no-match behavior, not an authored rule effect

## TDD And BDD Implementation Strategy
- Start with behavior statements and encode them as tests before implementation:
  - Given an allowed `file_write`, when `runtime execute` runs, then it writes the file, persists safe evidence, and returns `allowed`.
  - Given an allowed `http_request` with a successful response, when `runtime execute` runs, then it returns safe HTTP metadata and persists the matching terminal event.
  - Given an `http_request` that returns `4xx` or `5xx`, when `runtime execute` runs, then it returns `failed` with `failure_class=http_status` and persists that outcome durably.
  - Given a confirmation callback that raises, when a `confirm` decision is executed, then the service fails closed with explicit confirmation failure metadata.
  - Given `runtime execute --input <file>`, when the file contains a valid request, then the CLI prints execution JSON and preserves or resolves `session_id` exactly as the service contract states.
  - Given non-object JSON from stdin or file, when a runtime command runs, then it fails with an explicit JSON-object validation error.
  - Given an interactive confirmation-required flow, when the operator accepts or refuses, then the CLI returns `confirmed` or `refused` without contaminating stdout JSON.
  - Given a real temporary runtime evidence DB, when `runtime execute` succeeds or fails, then `evidence report --session-id` can summarize that same stored session.
  - Given the runtime docs, templates, and `.env` examples, when the docs-parity tests run, then the documented commands, settings, and authoring examples match the shipped runtime surface.
- Implement in this order:
  1. Write failing execution-service, evidence-store, and report-service tests for the missing runtime matrix.
  2. Write failing CLI tests for file-input execution, interactive confirmation behavior, non-object JSON rejection, and real SQLite-backed end-to-end reporting.
  3. Write failing docs-parity tests for runtime workflows, env examples, and policy authoring guidance.
  4. Make the minimum runtime code changes needed to satisfy the newly added behavior tests.
  5. Update docs, templates, and env examples to satisfy the docs-parity tests.
  6. Re-run the targeted runtime suite, then the broader regression suite, then `ruff`.
- Keep each step behavior-closed:
  - do not add doc text without a testable parity expectation where practical
  - do not widen runtime scope beyond the existing Day 4 contract
  - do not change settings or schema unless a failing test demonstrates contract drift

## Runtime Hardening And Docs Design
- Execution-service coverage to add:
  - successful `file_write`
  - successful `http_request`
  - HTTP status failure path
  - confirmation callback exception path
- Evidence-store coverage to add:
  - round-trip persistence for `file_write` request/metadata payloads
  - round-trip persistence for `http_request` request/metadata payloads
  - multiple-session filtering in the same SQLite DB
- Report-service coverage to add:
  - mixed action kinds in one session
  - incomplete execution with only a decision event
  - summary propagation for non-shell terminal metadata and failure classes
- CLI coverage to add:
  - `runtime execute --input <file>`
  - non-object JSON rejection
  - interactive confirm accept/refuse flows
  - real SQLite-backed `runtime execute` + `evidence report` flow using temp settings
- Docs/template updates to make:
  - `policies/TEMPLATE.md` should show one concise `runtime_rules` example and the exact-one-matcher-family rule
  - `docs/workflows.md` should include request examples for `shell_command`, `file_write`, and `http_request`, plus `session_id`, exit-code, and SQLite evidence guidance
  - `docs/contributor-guide.md` and `.env` templates should expose the runtime artifact path, evidence DB path, and shell timeout settings
  - `tests/README.md` should describe the expanded Day 5 runtime and docs-parity coverage

## Failure And Recovery Rules
- Keep all existing runtime fail-closed semantics unchanged and verified:
  - blocked actions do not execute
  - refused confirmation does not execute
  - confirmation-unavailable remains explicit
  - evidence persistence remains part of the enforcement contract
- Runtime CLI JSON success output must remain machine-readable with no extra prose on stdout.
- Interactive confirmation prompts must continue to use stderr/Typer prompt flow only.
- Docs must not imply that:
  - MCP can execute runtime actions
  - raw SQLite rows are the preferred operator API
  - `allow` is a valid authored `runtime_rules.effect`
- `.env` templates must not omit supported runtime settings once Day 5 lands.

## Commit Plan
1. `test(day5): add failing runtime execution, evidence-store, and report matrix coverage`
2. `test(day5): add failing cli runtime end-to-end and interactive confirmation coverage`
3. `fix(runtime): close action-kind and cli gaps exposed by the day5 matrix`
4. `test(day5): add runtime docs and settings parity coverage`
5. `docs(day5): update runtime authoring, execution, sqlite evidence, and env guidance`
6. `test(day5): refresh coverage notes and rerun regressions`

## Branch And PR Flow
1. `git fetch origin` and confirm Day 4 runtime CLI/reporting work is on `origin/main`.
2. Fast-forward local `main` to `origin/main`.
3. Create `feat/runtime-day5-hardening-docs` from updated `main`.
4. Land the new failing runtime tests first so the remaining matrix is explicit before any fixes.
5. Land the minimum runtime code changes next, only where the new matrix exposes a real gap.
6. Land docs-parity tests before editing docs so the documentation contract is pinned.
7. Land docs/templates/env updates after those parity checks exist.
8. Open the PR only after the targeted runtime suite, docs-parity suite, broader regression suite, and `ruff` are green.

## Test Plan
- Execution-service tests for:
  - successful `file_write`
  - successful `http_request`
  - HTTP `4xx/5xx` execution failure classification
  - confirmation callback exception handling
- Evidence-store tests for:
  - shell, file-write, and HTTP payload round trips
  - multiple-session filtering
  - persisted metadata for non-shell action kinds
- Report-service tests for:
  - mixed-action session summaries
  - incomplete executions
  - action-kind preservation and failure propagation
- CLI tests for:
  - `runtime execute` with file input
  - JSON array/scalar/null rejection
  - interactive confirm accepted path
  - interactive confirm refused path
  - real SQLite-backed `runtime execute` followed by `evidence report`
- Docs-parity tests for:
  - runtime commands and settings coverage in docs
  - `.env` examples aligned with runtime settings
  - `policies/TEMPLATE.md` includes runtime authoring guidance
  - `tests/README.md` reflects the final runtime matrix
- Verification commands:
  - `uv run pytest -q tests/test_runtime_decision_service.py tests/test_runtime_execution_service.py tests/test_runtime_evidence_store.py tests/test_runtime_evidence_report_service.py tests/test_cli.py`
  - `uv run pytest -q tests/test_docs_hosted_onboarding.py tests/test_docs_runtime_workflows.py`
  - `uv run pytest -q tests/test_ingest.py tests/test_ingest_service.py tests/test_search_service.py tests/test_preflight_service.py tests/test_eval_service.py tests/test_mcp.py tests/test_service_factories.py tests/test_settings_and_types.py`
  - `uv run ruff check`

## Definition Of Done
- `feat/runtime-day5-hardening-docs` exists off updated `main`.
- The runtime matrix covers all three action kinds across execution, evidence persistence, reporting, and CLI surfaces.
- At least one real SQLite-backed CLI execution flow is covered end to end through `evidence report --session-id`.
- Operator docs explain `runtime_rules` authoring, runtime request authoring, execution behavior, session reporting, and SQLite evidence usage.
- The shipped `.env` examples and contributor docs describe the supported runtime settings accurately.
- Targeted runtime tests, docs-parity tests, broader regression tests, and `ruff` all pass.
- No new public runtime command, MCP runtime surface, or SQLite schema was added.

## Assumptions And Defaults
- Day 1 through Day 4 runtime work is already on `main` and remains the functional baseline.
- Day 5 keeps `evidence report` as the supported reporting surface; raw row dumping stays out of scope.
- Runtime authoring guidance belongs in the existing docs split and `policies/TEMPLATE.md`, not in a new standalone guide.
- The current local-first, single-user runtime model remains unchanged on Day 5.
- If the new Day 5 matrix finds no production-code gap, runtime code edits can stay minimal and the main deliverable becomes tests plus docs parity.
