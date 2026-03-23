---
policy_id: BE-CONFIG-001
title: Config Validation and Fail-Closed Startup
doc_type: standard
domain: backend
tags:
  - configuration
  - startup
  - reliability
grounded_in:
  - https://12factor.net/config
  - https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
---

# Config Validation and Fail-Closed Startup

## Intent

Runtime configuration should be validated once, early, and in one place so the
service fails fast instead of drifting into a partially safe state.

## Required Rules

- Required config values must be validated during startup, not lazily on first use.
- Missing or malformed config must stop the service from starting when the value
  affects correctness, security, or data integrity.
- Defaults are allowed only when they are safe, explicit, and documented.
- Config parsing must not depend on scattered environment lookups across the code
  base.
- Validation errors should name the missing key or invalid shape without printing
  secret values.

## Review Expectations

- Verify there is a single, testable config surface for the service.
- Verify startup failure messages are actionable for operators and safe to expose.
- Verify tests cover missing, malformed, and boundary-value config cases for new
  settings.

## Public Grounding

- The Twelve-Factor App config guidance informed the environment-as-config model.
- OWASP secrets guidance informed the fail-closed and safe-error-message rules.
