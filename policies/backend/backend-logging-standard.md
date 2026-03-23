---
policy_id: BE-LOG-001
title: Backend Logging Standard
doc_type: standard
domain: backend
tags:
  - logging
  - observability
  - pii
grounded_in:
  - https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
  - https://sre.google/sre-book/monitoring-distributed-systems/
---

# Backend Logging Standard

## Intent

Logs must help operators debug failures without leaking secrets, tokens, or personal
data. Logging is an observability tool, not a data lake.

## Required Rules

- Emit structured logs for service events. Free-form string logs are acceptable only
  for local development.
- Include stable identifiers when available: request ID, actor ID, service name,
  operation name, and outcome.
- Never log secrets, access tokens, session cookies, raw authorization headers,
  password reset links, or private key material.
- Redact email addresses, phone numbers, and user-provided free text unless a policy
  explicitly allows them for the workflow.
- Log failures with safe context and a remediation clue. An error log that lacks an
  operation name or outcome is incomplete.
- Use warning or error severity for operator actionability, not for control flow.

## Review Expectations

- Confirm auth-sensitive code paths do not serialize request bodies directly to logs.
- Confirm new background jobs emit success, retry, and terminal-failure events with a
  correlation ID.
- Confirm sampling or rate limiting exists if a failure can repeat at high volume.

## Public Grounding

- OWASP Logging Cheat Sheet informed the secrecy and redaction requirements.
- Google SRE monitoring guidance informed the structured and operator-actionable
  logging requirements.

