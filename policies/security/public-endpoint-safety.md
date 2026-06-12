---
policy_id: SEC-PUBLIC-ENDPOINT-002
title: Public Endpoint Safety
doc_type: security-standard
domain: security
tags:
  - public-endpoints
  - readiness
  - redaction
  - logging
  - configuration
grounded_in:
  - https://cheatsheetseries.owasp.org/cheatsheets/Error_Handling_Cheat_Sheet.html
  - https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
  - https://12factor.net/config
---

# Public Endpoint Safety

## Intent

Public operational endpoints should give operators enough signal to understand
service readiness without exposing internals that help an attacker or leak
environment-specific secrets.

## Required Rules

- Public readiness endpoints may expose stable status fields, but they must not
  return raw exception text, traceback content, local filesystem paths, database
  paths, bearer tokens, API keys, session secrets, passwords, or private config
  values.
- Failure messages returned from public endpoints must be allowlisted or
  sanitized before leaving the service boundary.
- Runtime setup failures, missing indexes, malformed configuration, and weak
  policy grounding must fail closed with an operator-safe reason.
- Application settings must be read through the central settings surface. Public
  route handlers and service helpers must not introduce scattered environment
  lookups.
- Logs emitted from public endpoint failure paths must use safe, structured
  context. Do not log raw request bodies, raw authorization headers, or raw
  exception strings that can include secret-like values.

## Review Expectations

- Verify public response schemas stay stable unless the change explicitly
  coordinates a schema migration.
- Verify failure tests include local path, token-like, and secret-like examples
  when endpoint output or logging changes.
- Verify safe messages still preserve enough detail for an operator to identify
  the failure class and next diagnostic step.
- Verify public endpoint changes keep CLI and MCP behavior aligned when both
  surfaces expose the same readiness or runtime state.

## Public Grounding

- OWASP error-handling guidance informed the rule against exposing raw exception
  details through public responses.
- OWASP logging guidance informed the safe diagnostic logging requirements.
- The Twelve-Factor App config guidance informed the central configuration
  boundary and fail-closed setup expectations.
