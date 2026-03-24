# PolicyNIM Day 6 Execution Plan

## Summary
- Day 6 should add the first real evaluation layer for PolicyNIM: a gold-case eval
  harness for `search` and grounded `preflight`, broader quality-focused unit
  coverage, and a rerank on/off comparison that shows whether reranking actually
  improves results.
- The deliverable is a local developer workflow, not a CI rollout yet: run evals
  from the CLI, persist results, and inspect them in a browser on `localhost`.
- For the localhost results view, do not add a custom app framework. Use Evidently
  OSS as the evaluation/reporting layer and local UI so the repo stays Python-first,
  lightweight, and easy to maintain.
- If this plan is accepted later, persist it as `.plan/day-6-execution-plan.md`.

## Skills Used
- `failure-path-testing`
  - Expand coverage around fail-closed grounding, malformed outputs, and retrieval
    edge cases instead of adding more happy-path-only tests.
- `audit-boundaries`
  - Keep eval and report-generation logic in service/interface layers, not inside
    providers, storage, or ingest primitives.
- `audit-abstractions`
  - Avoid introducing a generic experiment platform, custom dashboard app, or
    speculative evaluation framework wrappers.
- `dx-audit`
  - Keep the Day 6 workflow obvious from the terminal: run eval, inspect JSON,
    then open localhost UI for comparison.
- `python-pydantic-pyright`
  - Keep the new eval models, settings, and typed CLI JSON boundaries consistent
    with Pydantic validation and pyright expectations.
- `python-typing-contracts`
  - Tighten eval-service typing, protocol-compatible test doubles, and CLI
    control-flow typing so the new Python surface stays structurally honest.

## Scope
- Sync local repo with remote `main`.
- Create `feat/day6-evals-and-quality`.
- Add a checked-in eval suite for both `search` and grounded `preflight`.
- Add a new `policynim eval` CLI workflow for offline-first evaluation.
- Add a localhost results-view workflow backed by Evidently OSS.
- Run each eval suite with reranking enabled and disabled, then persist both runs
  for side-by-side comparison.
- Add unit tests around chunking, citation mapping, response validation, and
  retrieval flow.
- Update README, architecture notes, and test docs for the Day 6 workflow.
- Do not add a custom web dashboard, MCP eval tools, or CI live-eval rollout yet.

## Package Layout
- `src/policynim/services/eval.py`
- `src/policynim/interfaces/cli.py`
- `src/policynim/types.py`
- `src/policynim/settings.py`
- `tests/test_eval_service.py`
- `tests/test_cli.py`
- `tests/test_ingest.py`
- `tests/test_search_service.py`
- `tests/test_preflight_service.py`
- `tests/test_nvidia_generator.py`
- `tests/test_nvidia_reranker.py`
- `evals/`
- `README.md`
- `docs/architecture.md`
- `tests/README.md`

## Dependencies And Settings
- Add `evidently` as the Day 6 eval/reporting dependency.
- Do not add FastAPI, Streamlit, React, or a separate frontend toolchain.
- Keep existing runtime settings for:
  - `NVIDIA_API_KEY`
  - `POLICYNIM_LANCEDB_URI`
  - `POLICYNIM_LANCEDB_TABLE`
  - `POLICYNIM_DEFAULT_TOP_K`
  - NVIDIA model/base-url/timeouts
- Add only the minimum Day 6 settings if implementation requires them:
  - local eval workspace path
  - optional localhost UI port
- Keep Day 6 control primarily in CLI flags rather than environment variables.

## Canonical Contracts
- Public CLI additions:
  - `policynim eval`
- `policynim eval` behavior:
  - runs a bundled eval suite by default
  - supports `--mode offline|live`, default `offline`
  - starts the local Evidently UI by default after persisting results
  - supports `--headless` to skip the automatic UI launch
  - supports rerank comparison by running:
    - rerank enabled
    - rerank disabled
  - supports `--no-compare-rerank` to skip the non-default rerank-disabled run
  - emits JSON-first results to stdout
  - persists run data for localhost inspection
- Internal additions:
  - `EvalCase`
  - `EvalCaseResult`
  - `EvalRunResult`
  - `EvalService`
- Scoring semantics:
  - `search` cases validate expected `chunk_id` recall and
    `insufficient_context` behavior
  - `preflight` cases validate expected `policy_id` recall and
    `insufficient_context` behavior
  - no LLM-as-judge scoring on Day 6
  - summary wording is not a pass/fail criterion

## Eval And Reporting Design
- `EvalService`
  - Load eval cases from bundled fixtures or `--cases`.
  - Run search and preflight cases through the existing service layer.
  - Score results with deterministic code-based checks.
  - Aggregate metrics for:
    - search pass rate
    - preflight pass rate
    - expected chunk recall
    - expected policy recall
    - insufficient-context accuracy
- Rerank on/off comparison
  - Each suite execution runs twice from the same cases:
    - once with reranking enabled
    - once with reranking disabled
  - Persist both runs with shared suite metadata plus a rerank mode label so they
    can be compared in the localhost UI.
  - Report deltas for expected chunk recall, expected policy recall, and overall
    pass rate.
- Offline mode
  - Uses deterministic fixtures and service doubles.
  - Requires no NVIDIA key.
  - Must be the default contributor workflow.
- Live mode
  - Uses the real ingest/search/preflight stack.
  - Requires `NVIDIA_API_KEY`.
  - Must write to an isolated temporary LanceDB path so it does not mutate the
    caller’s normal runtime index.
- Localhost results view
  - Use Evidently OSS workspace-backed local UI.
  - Day 6 browser scope is:
    - inspect suite summaries
    - compare rerank on/off runs
    - inspect failed cases
  - Do not add custom filtering or custom dashboard code unless Evidently proves
    insufficient.

## Eval Suite Design
- Create a small bundled gold suite under `evals/`.
- Include:
  - positive `search` cases for backend and security retrieval
  - positive `preflight` cases that should surface known policy IDs
  - insufficient-context cases that should remain fail-closed
- Keep expectations minimal and stable:
  - expected `chunk_id` values for `search`
  - expected `policy_id` values for `preflight`
  - expected `insufficient_context`
- Do not snapshot full generated summaries or full guidance arrays on Day 6.

## Unit Test Additions
- `tests/test_ingest.py`
  - Add chunking edge cases around line-span boundaries, blank sections, and
    repeated heading patterns beyond the current deterministic-ID coverage.
- `tests/test_preflight_service.py`
  - Add citation-mapping and validation cases for:
    - duplicate citation IDs collapsing deterministically
    - draft-level vs policy-level citation mismatches
    - one invalid cited chunk invalidating the full grounded result
    - stable citation ordering from validated chunk IDs
- `tests/test_nvidia_generator.py`
  - Add malformed response validation cases for missing fields, wrong field types,
    and invalid citation payload shapes.
- `tests/test_nvidia_reranker.py`
  - Add response-shape and score-count mismatch coverage where gaps remain.
- `tests/test_search_service.py`
  - Add retrieval-flow cases for dense-only behavior when reranking is absent and
    eval-oriented rerank comparison inputs.
- `tests/test_eval_service.py`
  - Add direct scoring coverage for search cases, preflight cases, aggregate
    metrics, and rerank delta reporting.
- `tests/test_cli.py`
  - Add CLI coverage for `policynim eval`.

## Commit Plan
1. `build(day6): add eval dependency and day-6 workspace wiring`
2. `feat(eval): add eval types, fixtures, and scoring service`
3. `feat(cli): add eval and localhost UI commands`
4. `test(day6): expand chunking, citation, response-validation, and retrieval coverage`
5. `feat(eval): add rerank on-off comparison reporting`
6. `docs: document day-6 eval workflow and localhost inspection`

## Branch And PR Flow
1. `git fetch origin` and confirm local `main` matches `origin/main`.
2. Create `feat/day6-evals-and-quality` from updated `main`.
3. Land eval types and fixtures before wiring the CLI.
4. Run offline eval and unit tests before any live NVIDIA verification.
5. If `NVIDIA_API_KEY` is present, run one opt-in live eval pass and verify the
   localhost UI can inspect both rerank modes.
6. Open a real PR only after offline eval, rerank comparison, localhost viewing,
   and expanded unit coverage are all working.

## Test Plan
- `policynim eval` runs the bundled suite in offline mode and returns valid JSON.
- `policynim eval --cases ...` loads an alternate suite.
- Failed eval cases return a non-zero exit code.
- `policynim eval` persists both rerank-enabled and rerank-disabled runs for
  comparison.
- Search evals detect retrieval regressions when expected `chunk_id` values drop
  out of results.
- Preflight evals detect grounding regressions when expected `policy_id` values
  disappear or `insufficient_context` changes unexpectedly.
- Citation validation tests still fail closed when chunk references are invalid.
- Generator and reranker response validation rejects malformed provider payloads.
- Live Day 6 evaluation remains opt-in behind `NVIDIA_API_KEY`.

## Definition Of Done
- Fresh Day 6 branch exists off updated `main`.
- Bundled eval suite exists for both `search` and `preflight`.
- `policynim eval` runs offline-first, emits JSON, and persists runs for local
  inspection.
- Rerank on/off comparison is built into the eval workflow and visible in the
  localhost results view.
- Local browser inspection works through Evidently OSS on `localhost` without a
  custom dashboard framework.
- Unit coverage is expanded around chunking, citation mapping, response
  validation, and retrieval flow.
- README, architecture notes, and test docs reflect the Day 6 workflow.
- No custom web app or CI rollout work leaks into Day 6.
