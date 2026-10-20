---
policy_id: BE-LOG-001
title: Safe Structured Logging
doc_type: standard
domain: backend
tags:
  - observability
  - engineering
grounded_in:
  - https://github.com/OWASP/CheatSheetSeries/blob/72865b1e3b5754246a101778dc00b37f9c233373/cheatsheets/Logging_Cheat_Sheet.md
  - https://github.com/open-telemetry/opentelemetry-specification/blob/3e4f2ff15933d70356cc18a9a96719b78d116233/specification/logs/data-model.md
---

# Safe Structured Logging

## Intent

Operators need failure context without turning logs into a copy of private requests.

## Applicability

Applies to application-developer, service-operator. Select this policy when the change affects the behavior described below.

## Required Rules

- **LOG-1** Exclude secrets, tokens, authorization headers, session cookies, reset links, private keys, sensitive payloads, email addresses, phone numbers, and user-provided free text by default. Any personal-data exception needs an approved purpose, access controls, and retention limit. Support: CS-LOG:Data to exclude; PolicyNIM decision.
- **LOG-2** Emit structured logs for service events; free-form string logs are allowed only for local development. Include service, operation, outcome, and safe actor/request identifiers when available; include trace context without copying arbitrary request bodies. Support: OTEL-LOG:Trace Context Fields; OTEL-LOG:Field: Resource; PolicyNIM decision.
- **LOG-3** Record job success, retry, and terminal-failure events with safe correlation; include the operation, outcome, safe cause, and operator action for failures. Choose warning/error severity for operator actionability, bound repeated events, and review access and retention. Support: PolicyNIM decision.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Test success, retry, failure, and hostile payloads against captured logs. Verify a sensitive marker never appears and a failure can be followed through safe identifiers.

## Exceptions and Boundaries

Redaction is defense in depth; prefer not collecting sensitive values. Sampling, retention, and identifiers need workload and privacy review; structured JSON alone doesn't prove safe logging.

## Examples

Applicable: A failed job logs its operation ID, failure class, and retry state while a token marker stays absent.

Violating: The handler serializes an authorization header or raw user body into a debug event.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- CS-LOG: [OWASP Logging Cheat Sheet](https://github.com/OWASP/CheatSheetSeries/blob/72865b1e3b5754246a101778dc00b37f9c233373/cheatsheets/Logging_Cheat_Sheet.md); revision `72865b1e3b5754246a101778dc00b37f9c233373`. Sections: Data to exclude.
- OTEL-LOG: [OpenTelemetry stable Logs Data Model](https://github.com/open-telemetry/opentelemetry-specification/blob/3e4f2ff15933d70356cc18a9a96719b78d116233/specification/logs/data-model.md); revision `3e4f2ff15933d70356cc18a9a96719b78d116233`. Sections: Trace Context Fields, Field: Resource, Severity Fields.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
