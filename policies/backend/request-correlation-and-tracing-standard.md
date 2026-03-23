---
policy_id: BE-TRACE-001
title: Request Correlation and Tracing Standard
doc_type: standard
domain: backend
tags:
  - tracing
  - correlation-id
  - observability
grounded_in:
  - https://sre.google/workbook/monitoring-distributed-systems/
  - https://opentelemetry.io/docs/concepts/signals/traces/
---

# Request Correlation and Tracing Standard

## Intent

Requests, jobs, and downstream calls must be traceable across service boundaries
so operators can follow a failure without reconstructing the request manually.

## Required Rules

- Propagate a stable correlation identifier on every inbound request and outbound
  dependency call when the target supports it.
- Emit trace or span identifiers in logs when the platform exposes them.
- Preserve caller context across async work, retries, and queue handoffs when that
  context is needed for debugging or audit.
- Do not generate a new identifier at each hop unless the previous value is missing
  or invalid.
- Tracing metadata must not carry secrets, session tokens, or user payload bodies.

## Review Expectations

- Verify inbound request handling, job enqueueing, and dependency clients use the
  same correlation strategy.
- Verify fallback behavior exists when a downstream service does not support trace
  propagation.
- Verify tests cover at least one cross-service success path and one failure path
  that preserves the same request context.

## Public Grounding

- Google SRE monitoring guidance informed the end-to-end traceability requirements.
- OpenTelemetry trace concepts informed the span and correlation terminology.
