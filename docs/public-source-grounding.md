# Public-Source Grounding Notes

The shipped PolicyNIM corpus is synthetic internal-style guidance derived from
publicly available standards, guidance, and reference material. The goal is to
make the repo feel like a realistic engineering handbook without copying
proprietary internal documents.

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

### `SEC-AUTH-001`

- Path: `policies/security/auth-sensitive-code-review-standard.md`
- Public grounding:
  - OWASP Authentication Cheat Sheet
  - OWASP Session Management Cheat Sheet
- Provenance note:
  - The repo policy translates public authentication and session guidance into a
    stricter code-review bar for auth-sensitive changes, with emphasis on threat
    modeling and fail-closed behavior.

### `SEC-SECRET-001`

- Path: `policies/security/secrets-redaction-and-handling.md`
- Public grounding:
  - OWASP Secrets Management Cheat Sheet
  - OWASP Logging Cheat Sheet
- Provenance note:
  - The repo policy combines public guidance on secret storage, redaction, and
    safe diagnostics into one practical handling standard for application teams.

### `SEC-SESSION-001`

- Path: `policies/security/session-lifetime-and-token-boundaries.md`
- Public grounding:
  - OWASP Authentication Cheat Sheet
  - OWASP Session Management Cheat Sheet
- Provenance note:
  - The repo policy uses public session and token guidance to define bounded
    lifetime, revocation, and error-handling rules for token-based systems.
  - This document keeps its grounding in the body text rather than the
    `grounded_in` frontmatter field. The provenance is still public and
    explicit, but the metadata format is less normalized than the other shipped
    policies.

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
