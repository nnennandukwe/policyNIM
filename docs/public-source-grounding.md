# Public-Source Grounding Notes

The PolicyNIM source corpus contains project guidance informed by public
standards and references. Maintained security adaptations carry pinned sources
and licenses; older policies keep unknown provenance fields where the original
revision or reuse terms weren't recorded. No proprietary handbook is included.

Each policy below maps the shipped document to its public grounding and explains
how the repo uses that source material.

## Architecture Policies

### `ARCH-API-001`

- Path: `policies/architecture/api-versioning-guidance.md`
- Public grounding:
  - Microsoft REST API Guidelines
  - Semantic Versioning
- Provenance note:
  - The repo policy adapts public compatibility and deprecation guidance into a
    short internal review checklist for API evolution. It does not reproduce a
    proprietary standard.

### `ARCH-JOB-001`

- Path: `policies/architecture/background-job-design-rules.md`
- Public grounding:
  - Google SRE Workbook
  - AWS Builders' Library guidance on timeouts, retries, and backoff with jitter
- Provenance note:
  - The repo policy condenses public reliability guidance into practical rules for
    retry behavior, idempotency, operator visibility, and failure handling in
    background jobs.

## Backend Policies

### `BE-LOG-001`

- Path: `policies/backend/backend-logging-standard.md`
- Public grounding:
  - OWASP Logging Cheat Sheet
  - Google SRE guidance on monitoring distributed systems
- Provenance note:
  - The repo policy turns public logging and observability guidance into a tighter
    set of backend logging expectations around structure, redaction, and operator
    actionability.

### `BE-CONFIG-001`

- Path: `policies/backend/config-validation-and-fail-closed.md`
- Public grounding:
  - The Twelve-Factor App config guidance
  - OWASP Secrets Management Cheat Sheet
- Provenance note:
  - The repo policy adapts public config and secret-management principles into one
    internal-style rule set for typed settings, startup validation, and safe error
    messages.

### `BE-TRACE-001`

- Path: `policies/backend/request-correlation-and-tracing-standard.md`
- Public grounding:
  - Google SRE Workbook monitoring guidance
  - OpenTelemetry trace concepts
- Provenance note:
  - The repo policy uses public observability references to define a compact
    tracing and correlation standard for requests, jobs, and downstream calls.

## Security Policies

The maintained security expansion preserves `SEC-AUTH-001`, `SEC-SECRET-001`,
and `SEC-SESSION-001`, and adds six focused application/MCP policies. See the
[security coverage matrix](security-mcp-coverage.md) for exact editions, control
references, role boundaries, paired scenarios, source notices, and omissions.
Their adaptation metadata is declared in `policies/pack.toml`; it doesn't establish
which generation an existing index serves.

`SEC-PUBLIC-ENDPOINT-002` retains its existing safe readiness-response guidance
and unpinned legacy references to OWASP error/logging and Twelve-Factor config
material. This change doesn't invent a reviewed revision or license for those
legacy declarations. The architecture/backend policies above likewise retain
their prior provenance until their sources are reviewed.

## Template Note

`policies/TEMPLATE.md` is authoring guidance rather than part of the runtime corpus.
Its example `grounded_in` entry shows the expected shape for future policy
documents.

## Why This Matters

- It keeps the repo public-safe.
- It makes it clear that the corpus is adapted from public standards rather than
  copied from an internal handbook.
- It gives contributors a canonical place to extend provenance notes as new sample
  policies are added.
