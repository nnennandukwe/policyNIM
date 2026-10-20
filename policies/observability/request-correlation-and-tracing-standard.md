---
policy_id: BE-TRACE-001
title: Request Correlation and Trace Context
doc_type: standard
domain: backend
tags:
  - observability
  - engineering
grounded_in:
  - https://www.w3.org/TR/2021/REC-trace-context-1-20211123/
  - https://github.com/open-telemetry/opentelemetry-specification/blob/3e4f2ff15933d70356cc18a9a96719b78d116233/specification/logs/data-model.md
---

# Request Correlation and Trace Context

## Intent

A request that crosses a worker or dependency boundary needs context operators can follow through failures.

## Applicability

Applies to application-developer, service-operator. Select this policy when the change affects the behavior described below.

## Required Rules

- **TRACE-1** Where W3C Trace Context is supported, validate inbound traceparent, create or continue context according to its processing rules, and propagate it to supported downstream calls. Support: TRACE:3.2; TRACE:4.
- **TRACE-2** Preserve safe operation correlation through asynchronous work and retries; log TraceId/SpanId when available. Support: OTEL-LOG:Trace Context Fields; PolicyNIM decision.
- **TRACE-3** Keep secrets and user payloads out of trace metadata; never use a caller-supplied trace ID as an authorization principal. Support: TRACE:6; TRACE:7; PolicyNIM decision.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Test a cross-boundary success and failure, malformed inbound context, and unsupported downstream propagation. Show how operators locate the resulting spans and logs.

## Exceptions and Boundaries

Trace and span IDs have different roles. A new child span normally gets a new span ID; preserve valid trace identity without requiring every hop to repeat the same span ID.

## Examples

Applicable: A queued request retains safe correlation after retry; invalid inbound context is replaced without changing caller permissions.

Violating: Each service discards all correlation, or a bearer token is inserted into tracestate.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- TRACE: [W3C Trace Context Recommendation](https://www.w3.org/TR/2021/REC-trace-context-1-20211123/); revision `W3C Recommendation 2021-11-23`. Sections: 3.2, 4, 6, 7.
- OTEL-LOG: [OpenTelemetry stable Logs Data Model](https://github.com/open-telemetry/opentelemetry-specification/blob/3e4f2ff15933d70356cc18a9a96719b78d116233/specification/logs/data-model.md); revision `3e4f2ff15933d70356cc18a9a96719b78d116233`. Sections: Trace Context Fields, Field: Resource, Severity Fields.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
