---
policy_id: SEC-AUTH-001
title: Auth-Sensitive Code Review Standard
doc_type: review-standard
domain: security
tags:
  - auth
  - code-review
  - threat-model
grounded_in:
  - https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
  - https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
---

# Auth-Sensitive Code Review Standard

## Intent

Changes that alter authentication, session state, token handling, or privilege
boundaries require a higher review bar than routine feature work.

## Required Rules

- Every auth-sensitive change must include a short threat-model note in the PR or
  implementation notes. It must name the asset, attacker action, and primary abuse
  path being controlled.
- Token lifecycle changes must document issuance, refresh, revocation, expiry, and
  rollback behavior.
- Code paths that fail closed are preferred over code paths that silently degrade to
  permissive behavior.
- Auth changes must emit audit-friendly events without exposing credentials or token
  bodies.
- Reviewers must verify negative-path tests for invalid, expired, revoked, and
  replayed credentials when those states apply.

## Review Expectations

- Require at least one reviewer who did not author the auth logic.
- Verify secrets are read from a managed source and not introduced via test fixtures,
  logs, or ad hoc environment usage.
- Verify migration or rollback notes exist for schema or token-format changes.

## Public Grounding

- OWASP Authentication and Session Management guidance informed the threat-model,
  fail-closed, and token lifecycle requirements.

