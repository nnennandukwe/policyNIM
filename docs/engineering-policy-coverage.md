# Engineering policy coverage

The `policynim.baseline@0.3.0` aggregate selects 26 policies. Six independently selectable engineering packs and the maintained security/MCP pack use the same source files. The full profile is `policies/profiles/full-engineering.toml`; it selects the seven topic roots and pins their actual source digests.

This library supplies review guidance and authored expectations. It doesn't implement an application's transactions, quotas, tracing, or release infrastructure, and it doesn't establish SSDF, SLSA, or OSPS compliance. The 64 engineering scenarios check authored verdict categories, source identity, role-specific scope exclusions, requirement evidence and documented boundaries through real parsing/chunking. They aren't independent model judgments or live retrieval measurements.

## Coverage matrix

| Area and pack | Policies | Requirement IDs | Source sections | Authored cases |
| --- | --- | --- | --- | --- |
| testing / `policynim.testing` | [TEST-FAIL-001](../policies/testing/failure-and-recovery.md) | FAIL-1, FAIL-2, FAIL-3 | PolicyNIM decision; SSDF:PW.8.1; SSDF:PW.8.2 | `test-fail-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| testing / `policynim.testing` | [TEST-CONTRACT-001](../policies/testing/public-boundary-contracts.md) | CONTRACT-1, CONTRACT-2, CONTRACT-3 | PolicyNIM decision; SSDF:PW.8.2; SRE-TEST:Traditional Tests | `test-contract-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| api / `policynim.api` | [ARCH-API-001](../policies/api/api-versioning-guidance.md) | API-1, API-2, API-3 | PolicyNIM decision; SEMVER:1; SEMVER:7; SEMVER:8 | `arch-api-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| api / `policynim.api` | [API-ERROR-001](../policies/api/http-errors.md) | ERROR-1, ERROR-2, ERROR-3 | HTTP:15; PROBLEM:3; PROBLEM:5; PolicyNIM decision | `api-error-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| api / `policynim.api` | [API-PAGE-001](../policies/api/pagination-contracts.md) | PAGE-1, PAGE-2, PAGE-3 | PolicyNIM decision; LINK:2.1; LINK:3 | `api-page-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| data / `policynim.data` | [DATA-MIGRATE-001](../policies/data/migration-recovery.md) | MIGRATE-1, MIGRATE-2, MIGRATE-3 | PolicyNIM decision; SRE-DATA:Delivering a Recovery System, Rather Than a Backup System | `data-migrate-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| data / `policynim.data` | [DATA-TRANSACTION-001](../policies/data/transaction-boundaries.md) | TRANSACTION-1, TRANSACTION-2, TRANSACTION-3 | POSTGRES:3.4; PolicyNIM decision | `data-transaction-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| reliability / `policynim.reliability` | [ARCH-JOB-001](../policies/reliability/background-job-design-rules.md) | JOB-1, JOB-2, JOB-3 | AWS-RETRY:Retries and backoff; PolicyNIM decision | `arch-job-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| reliability / `policynim.reliability` | [BE-CONFIG-001](../policies/reliability/config-validation-and-fail-closed.md) | CONFIG-1, CONFIG-2, CONFIG-3 | PolicyNIM decision; CS-SECRET:2 General Secrets Management | `be-config-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| reliability / `policynim.reliability` | [REL-TIMEOUT-001](../policies/reliability/deadlines-and-retries.md) | TIMEOUT-1, TIMEOUT-2, TIMEOUT-3 | AWS-RETRY:Timeouts; PolicyNIM decision; AWS-RETRY:Retries and backoff; AWS-RETRY:Jitter; HTTP:10.2.3 | `rel-timeout-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| reliability / `policynim.reliability` | [REL-OVERLOAD-001](../policies/reliability/admission-and-backpressure.md) | OVERLOAD-1, OVERLOAD-2, OVERLOAD-3 | SRE-OVERLOAD:The Pitfalls of "Queries per Second"; PolicyNIM decision; SRE-OVERLOAD:Per-Customer Limits; SRE-OVERLOAD:Client-Side Throttling | `rel-overload-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| observability / `policynim.observability` | [BE-LOG-001](../policies/observability/backend-logging-standard.md) | LOG-1, LOG-2, LOG-3 | CS-LOG:Data to exclude; OTEL-LOG:Trace Context Fields; OTEL-LOG:Field: Resource; PolicyNIM decision | `be-log-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| observability / `policynim.observability` | [BE-TRACE-001](../policies/observability/request-correlation-and-tracing-standard.md) | TRACE-1, TRACE-2, TRACE-3 | TRACE:3.2; TRACE:4; OTEL-LOG:Trace Context Fields; PolicyNIM decision; TRACE:6; TRACE:7 | `be-trace-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| observability / `policynim.observability` | [OBS-READINESS-001](../policies/observability/metrics-and-readiness.md) | READINESS-1, READINESS-2, READINESS-3 | SRE-MONITOR:The Four Golden Signals; PolicyNIM decision; SRE-MONITOR:Black-Box Versus White-Box | `obs-readiness-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| supply-chain / `policynim.supply-chain` | [SUPPLY-BUILD-001](../policies/supply-chain/dependencies-and-builds.md) | BUILD-1, BUILD-2, BUILD-3 | SSDF:PW.4.1; OSPS:OSPS-BR-05.01; OSPS:OSPS-DO-06.01; OSPS:OSPS-BR-01.01; OSPS:OSPS-BR-01.03; PolicyNIM decision | `supply-build-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |
| supply-chain / `policynim.supply-chain` | [SUPPLY-PROVENANCE-001](../policies/supply-chain/provenance-and-verification.md) | PROVENANCE-1, PROVENANCE-2, PROVENANCE-3 | SSDF:PS.2.1; OSPS:OSPS-BR-06.01; SLSA:How to verify; SLSA:Forming Expectations; PolicyNIM decision | `supply-provenance-001-applicable`, `-violating`, `-out-of-scope`, `-boundary` |

## Select a topic or the full baseline

Select one topic as a replacement corpus:

```bash
POLICYNIM_CORPUS_DIR=policies/testing policynim policies validate --source --format json
```

Select the reviewed full profile instead:

```bash
policynim profile validate policies/profiles/full-engineering.toml --format json
POLICYNIM_PROFILE=policies/profiles/full-engineering.toml policynim policies list --source --format json
```

Unset `POLICYNIM_CORPUS_DIR` when setting `POLICYNIM_PROFILE`. The root aggregate is the default corpus and already includes all topic members. Don't combine that aggregate with its topic roots in a profile: the same stable IDs would be declared twice. A custom subset profile can select any nonoverlapping topic roots with `policynim profile init`.

The aggregate's selected provenance names `policynim.baseline`; topic selection names the actual topic pack. Per-policy source descriptors, requirements, and content digests stay the same. Each selected root retains the source notices it needs under its own `licenses/` directory. No nested manifest implicitly changes an aggregate member's provenance.

## Source revisions and reuse limits

The manifest pins SSDF 1.1 final (February 2022), the 2016 Google SRE book chapters, AWS's article published/last modified December 5, 2019, immutable RFC editions, PostgreSQL 18, the November 23, 2021 W3C Trace Context Recommendation, an exact OpenTelemetry specification commit, SLSA 1.2, and OSPS 2026.08.28. Source aliases in `evals/engineering_scenarios.json` match each policy's manifest sources. Review the exact sections listed there; a framework name alone isn't evidence for a requirement.

BE-LOG-001 and BE-CONFIG-001 retain their OWASP adaptation labels. The other engineering prose is original project guidance. `PolicyNIM decision` marks project choices such as pagination consistency, migration rollout, readiness design, limits, and integration proof. PostgreSQL transaction guidance is platform-specific. RFC 9457 and HTTP Link pagination remain optional format choices. AWS example latency percentiles aren't universal timeout thresholds. SSDF security-test guidance isn't presented as a general reliability mandate.

SLSA 1.2 uses Community Specification License 1.0. OpenTelemetry's selected specification uses Apache 2.0. W3C, IETF, NIST, PostgreSQL, SemVer, OpenSSF, and retained OWASP material keep their recorded terms and attribution. Public availability of Google SRE/AWS prose doesn't supply a reuse grant; no book/article text is redistributed. See `policies/licenses/ENGINEERING-SOURCES.txt` and the selected pack's notices.

## Stable IDs and source-path migration

The original 15 policy IDs remain. Five overlapping files move into their owning topic directories: `ARCH-API-001` to `api/`; `ARCH-JOB-001` and `BE-CONFIG-001` to `reliability/`; `BE-LOG-001` and `BE-TRACE-001` to `observability/`. Their architecture/backend domains remain. None of these five policies declares runtime matchers, so this move doesn't change runtime matcher semantics. Existing security source files and matchers stay intact.

Review source/profile updates before ingesting; source paths, authored revisions, pack digests, and catalog identity change. Existing immutable generations retain their historical source evidence and remain inspectable until retention removes unreferenced objects. Refresh the public bundle only from the final reviewed source tree, then verify its source match and grounded-suite pins before release.

## Verification

```bash
uv run --no-sync pytest -q tests/test_engineering_content.py tests/test_security_content.py
uv run --no-sync policynim policies validate --source --format json
```

Paired authored scenarios cover incompatible API changes, pagination/errors, interrupted migration, partial commit, duplicate effects, exhausted retries, overload, sensitive telemetry, correlation, and provenance verification. The source tests reject changed requirement text or revisions, missing source sections, inconsistent topic descriptors, stale profile pins, and missing selected notices. Live quality and independent scenario judgments belong to the grounded evaluation gate.

Boundary cases exercise compatible API additions, optional error formats, small collections, harmless repeats, disabled integrations, new child span IDs and limits on framework claims. Their expected outcomes are reviewed source expectations; structural checks don't independently establish a model's semantic judgment.

The [full source review](evidence/engineering-source-review/full.json) and
[role-only follow-up](evidence/engineering-source-review/final.json) are historical
receipts for their stated source revisions. Later review found that two boundary
inputs described harmless refactors while their paired exclusions expected no
policy selection. Those boundary inputs now exercise changed recovery and public
serialization, preserving the expected boundary verdicts and the unchanged
out-of-scope cases. Their source policies and other affected policies now list
only the sections cited for their requirements; broader retryability and severity
choices remain labeled as project guidance rather than borrowed source mandates.

The [current fixture binding](evidence/engineering-source-review/current-fixture-binding.json)
marks these author revisions as pending independent source review. The original
receipts haven't been rewritten to review the new inputs or source revisions.
The checked-in generation and grounded-suite binding are also historical until
the reviewed source receives a new bundle. The final independent-review and
bundle-equality checks must pass before this revision can ship. These receipts
don't measure model or retrieval quality, runtime compliance, external framework
compliance, or release readiness.
