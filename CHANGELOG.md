# Changelog

## [Unreleased]

## [0.2.1] - 2026-10-20

PolicyNIM 0.2.1 expands the policy library, adds source-bound MCP guidance and
retained evaluation jobs, and strengthens package and hosted-runtime checks.

### Policy sources and guidance

- The public library contains 26 policies and 230 indexed chunks. Versioned
  generations bind the policy bodies, catalog, index and runtime rules, with
  explicit activation, reader leases, retention and rollback.
- Local policy packs support reviewed additions, overrides and exclusions.
  Catalog, generation and profile identities travel with evaluation evidence;
  reports from earlier content remain historical evidence.
- MCP exposes policy search, compilation, preflight, evidence and immutable source
  resources. Default compiler constraints and preflight actions contain complete
  quoted policy sources, including their conditions and examples. Callers must
  assess applicability before treating a quotation as an implementation step.

### Evaluations and operations

- Evaluation jobs retain their owner, accepted configuration, source generation,
  case results and reports. Submission, status, cancellation and report access
  use the existing authenticated MCP surface.
- Admission uses principal-scoped work allowances and active-operation limits.
  Accepted work stays charged after cancellation or failure. Work units aren't
  provider tokens or monetary cost; unavailable usage remains unknown.
- Interrupted jobs retain available results and don't automatically replay provider
  calls. Quota finalization and job publication use separate databases: an
  interruption between their commits can leave completed quota accounting with an
  interrupted job. Reports preserve that outcome.
- Operational reports and the synthetic HTTP capacity harness account for caller
  outcomes, refusal and recovery. Synthetic load results don't establish live
  NVIDIA throughput or production capacity.

### Installation and security

- Version 0.2.1 uses Beta package metadata and ships the Python typing marker.
  Release gates verify fresh wheels and native standalone archives against source,
  dependency locks, packaged policies and archive checksums. Signed provenance,
  installer verification and retained evidence have separate checks.
- Runtime dependencies include the patched AnyIO, Starlette and HTTPX2 pins.
  Auth SQLite storage uses private permissions and rejects unsafe aliases while
  preserving live SQLite locks. Release verifiers isolate their settings home.
- Phoenix runs in a separate tool environment. PolicyNIM retains its Phoenix
  client/evaluators, NVIDIA model adapters and Guardrails. The unused bundled NeMo
  launcher extra was removed; existing launcher installations need their own
  compatible environment.
- The optional FastEmbed dependency still selects Pillow 12.3.0, which has known
  advisories. Its Pillow constraint prevents selecting the patched major version.
  The standard hosted runtime doesn't install Pillow; the optional extras aren't
  covered by a claim that every selected dependency is patched.

### Compatibility and remaining limits

- Beta and operator bearer keys remain supported. Production OAuth issuer and
  registered-client acceptance are pending under #97; no issuer has been selected.
- Durable evaluation tools don't claim MCP Tasks extension interoperability.
  That optional integration remains blocked under #102.
- SDK protocol checks, native coding-client checks, live provider behavior,
  production persistence and artifact provenance establish different results.
  Release evidence records each executed scope against its tested source and
  generation; a skipped check isn't a pass.
- Preserve compatible application, policy and quota-state backups. An older quota
  writer mustn't run against migrated weighted-ledger state, and policy rollback
  must preserve local overrides.

### Native build runtime

- Native builds select managed CPython 3.11.13 and check SQLite extension loading
  and cryptographic bindings before freezing. The packaged generation and
  transport checks still gate publication on every supported platform.
- The earlier 0.2.0 build candidate wasn't published: macOS archive checks failed.
  Its tag and failure evidence remain preserved.

## [0.2.0] - 2026-10-20

PolicyNIM 0.2 expands the policy library, adds source-bound MCP guidance and
retained evaluation jobs, and strengthens package and hosted-runtime checks.

### Policy sources and guidance

- The public library contains 26 policies and 230 indexed chunks. Versioned
  generations bind the policy bodies, catalog, index and runtime rules, with
  explicit activation, reader leases, retention and rollback.
- Local policy packs support reviewed additions, overrides and exclusions.
  Catalog, generation and profile identities travel with evaluation evidence;
  reports from earlier content remain historical evidence.
- MCP exposes policy search, compilation, preflight, evidence and immutable source
  resources. Default compiler constraints and preflight actions contain complete
  quoted policy sources, including their conditions and examples. Callers must
  assess applicability before treating a quotation as an implementation step.

### Evaluations and operations

- Evaluation jobs retain their owner, accepted configuration, source generation,
  case results and reports. Submission, status, cancellation and report access
  use the existing authenticated MCP surface.
- Admission uses principal-scoped work allowances and active-operation limits.
  Accepted work stays charged after cancellation or failure. Work units aren't
  provider tokens or monetary cost; unavailable usage remains unknown.
- Interrupted jobs retain available results and don't automatically replay provider
  calls. Quota finalization and job publication use separate databases: an
  interruption between their commits can leave completed quota accounting with an
  interrupted job. Reports preserve that outcome.
- Operational reports and the synthetic HTTP capacity harness account for caller
  outcomes, refusal and recovery. Synthetic load results don't establish live
  NVIDIA throughput or production capacity.

### Installation and security

- Version 0.2.0 uses Beta package metadata and ships the Python typing marker.
  Release gates verify fresh wheels and native standalone archives against source,
  dependency locks, packaged policies and archive checksums. Signed provenance,
  installer verification and retained evidence have separate checks.
- Runtime dependencies include the patched AnyIO, Starlette and HTTPX2 pins.
  Auth SQLite storage uses private permissions and rejects unsafe aliases while
  preserving live SQLite locks. Release verifiers isolate their settings home.
- Phoenix runs in a separate tool environment. PolicyNIM retains its Phoenix
  client/evaluators, NVIDIA model adapters and Guardrails. The unused bundled NeMo
  launcher extra was removed; existing launcher installations need their own
  compatible environment.
- The optional FastEmbed dependency still selects Pillow 12.3.0, which has known
  advisories. Its Pillow constraint prevents selecting the patched major version.
  The standard hosted runtime doesn't install Pillow; the optional extras aren't
  covered by a claim that every selected dependency is patched.

### Compatibility and remaining limits

- Beta and operator bearer keys remain supported. Production OAuth issuer and
  registered-client acceptance are pending under #97; no issuer has been selected.
- Durable evaluation tools don't claim MCP Tasks extension interoperability.
  That optional integration remains blocked under #102.
- SDK protocol checks, native coding-client checks, live provider behavior,
  production persistence and artifact provenance establish different results.
  Release evidence records each executed scope against its tested source and
  generation; a skipped check isn't a pass.
- Preserve compatible application, policy and quota-state backups. An older quota
  writer mustn't run against migrated weighted-ledger state, and policy rollback
  must preserve local overrides.
