# Grounded MCP Build Plan

## Summary

Expose compiled constraints, evidence from one preflight run, immutable policy resources and an explicit review prompt for #100. Baseline: #93 branch e54feaa, based on origin/main ba2c19d. Review unit: one stacked PR. Policy administration stays in files/Git/CLI.

## Skills To Use

| Stage | Skill | Responsibility |
|---|---|---|
| Planning | software-build-plan | Pin ticket acceptance and dependency boundaries. |
| Standards | qodo-get-rules | Apply schema compatibility, typed service boundaries, English identifiers and docstrings. |
| Failure paths | failure-path-testing | Reject missing, purged and malformed resources; prove tracing doesn't repeat generation. |
| Interface audit | dx-audit | Inspect schemas, errors, examples and provider-free discovery. |
| Review | qodo-review | Review the complete diff against the pushed #93 base before PR creation. |

Qodo Local Review eligibility: eligible Git repository with pushed base e54feaa. No runtime dependencies change. System-protocol abstraction guidance targets platform services; existing application factories remain the injection seam.

## Scope

In scope: policy_compile, policy_preflight_evidence, versioned catalog/policy/chunk resources, citation links, review prompt, modern/legacy integration tests and docs.

Out of scope: administration, remote execution, native Tasks (#102), durable jobs (#101, separate build).

## Package Layout / File-to-Task Mapping

- services/grounded_mcp.py: service composition, immutable resource lookup and replay-free evidence materialization.
- interfaces/grounded_mcp.py: tool/resource/prompt registration using shared executor and authorization.
- interfaces/mcp.py: registration composition only.
- types.py: additive generation-bound citation and evidence identity fields.
- tests/test_grounded_mcp.py: actual SDK discovery/invocation/resource tests.
- docs/grounded-mcp.md: public usage, retention and security boundaries.

## Dependencies And Settings

Use Python 3.11/3.12 and locked MCP 2.2.0. No added dependencies, storage schema or environment keys. Generation retention follows #93. Auth permissions are coordinated with #98.

## Canonical Contracts

policy_compile(task, domain?, top_k?) returns CompileResult. policy_preflight_evidence(task, domain?, top_k?) returns PreflightEvidenceTraceResult with run ID and packet identity from one pipeline execution. Resource URIs bind immutable generation IDs and hashed logical identifiers. Catalog discovery points to the active generation, but historical reads never substitute it. Unknown and invalid references fail explicitly.

## TDD And BDD Implementation Strategy

Verification-first integration slices: (1) provider-free resource/prompt discovery and strict historical reads; (2) typed compiler tool using shared admission; (3) evidence tool proving a single preflight_with_trace call and exact packet identity; (4) modern/legacy discovery and principal authorization. Reuse deterministic real-generation fixtures and fake providers only at provider seams.

## Component Design

GenerationStore reader leases own each resource read. Lookup uses validated catalog documents and index chunks with equality on hashed logical IDs, never arbitrary filesystem access. Services create/close compiler and preflight dependencies. Registration receives the existing executor and principal resolver, preserving one operation budget. No second server or execution loop.

## Failure And Recovery Rules

Malformed identifiers and unknown/purged generation IDs fail without active-generation fallback. Missing policy/chunk IDs fail without path reads. Service failures retain their existing sanitized MCP errors and close owned resources. Historical references remain readable while #93 retention keeps their generation; operators must retain generations needed by callers.

## Commit Plan

1. feat(mcp): expose generation-bound read resources and review prompt with integration tests.
2. feat(mcp): expose compiled constraints and replay-free evidence with schema tests and docs.

## Branch And PR Flow

Use feat/grounded-mcp-jobs stacked on feat/atomic-policy-generations. Run focused tests, Ruff, Pyright and full offline pytest. Commit changes, run context-bearing Qodo Local Review against e54feaa, reproduce findings and fix supported issues. Rerun checks and final review after changes. Open a stacked PR referencing #100 only when final review completes; attach it. Wait for exact-head CI and Qodo, coordinate root before merge.

## Test Plan

Run uv run --no-sync pytest -q tests/test_grounded_mcp.py tests/test_mcp.py and required full gates: uv run --no-sync ruff check; uv run --no-sync pyright; uv run --no-sync pytest -q -m 'not live and not docker_live'. Verify missing/purged/encoded-path references, historical content after activation, shared admission, principal denial before providers, typed tool schemas and no admin tools. Qodo uses exact pushed e54feaa base and final verified snapshot. Live client and production checks remain release-level evidence owned by root.

## Definition Of Done

All #100 acceptance behavior passes offline integration tests. No provider runs for resource/prompt discovery. Tracing never repeats generation. No confirmed actionable Qodo findings remain, CI passes on exact PR head, documentation matches shipped contracts. Root approves merge timing.

## Assumptions And Defaults

Resource listing exposes a current catalog entry plus immutable URI templates; catalog contents include exact generation-specific links. Resources contain maintained public corpus content only. Caller-specific reports belong to #101 and require authentication.


## Verification status

Dedicated SDK tests cover immutable resources, one-run evidence, authorization and invalid-request admission. Full offline suites pass on intermediate integrated heads. Supported local-review findings were fixed in code. Final review identified a carried runtime command-screening issue under #93; its owner fixed it and that commit is integrated. Review completion, exact-head CI and production readiness remain separate gates.
