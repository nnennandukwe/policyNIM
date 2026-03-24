---
policy_id: SEC-SECRET-001
title: Secrets Redaction and Handling
doc_type: security-standard
domain: security
tags:
  - secrets
  - redaction
  - configuration
grounded_in:
  - https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
  - https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
---

# Secrets Redaction and Handling

## Intent

Secrets should exist in the narrowest possible runtime boundary and should never
escape into logs, traces, screenshots, or test artifacts.

## Required Rules

- Store application secrets in a managed secret source, not in committed config,
  screenshots, or example payloads.
- Redact secrets and tokens before they enter logs, traces, analytics payloads, or
  exception text.
- Example code and fixtures must use obvious mock values. Do not include
  “real-looking” secrets in public or shared repositories.
- Secrets rotation steps must be documented for any new external credential the
  service depends on.
- Environment variables are a delivery mechanism, not a validation strategy. Required
  secrets must be validated at startup through a single config surface.

## Review Expectations

- Verify new config surfaces do not encourage scattered secret access.
- Verify failure messages reference the missing secret by key name only and never
  print the supplied value.
- Verify token-bearing responses are not copied into test snapshots or debugging docs.

## Public Grounding

- OWASP Secrets Management guidance informed the storage and validation rules.
- OWASP Logging guidance informed the redaction and error-message requirements.

