---
policy_id: REL-TIMEOUT-001
title: Deadlines and Bounded Retries
doc_type: standard
domain: reliability
tags:
  - reliability
  - engineering
grounded_in:
  - https://builder.aws.com/content/3EumjoZascWd1oZiEgL8ORlv3qE/timeouts-retries-and-backoff-with-jitter
  - https://www.rfc-editor.org/rfc/rfc9110.html
---

# Deadlines and Bounded Retries

## Intent

Dependency slowness and retries consume the caller's finite time and resources.

## Applicability

Applies to application-developer, service-operator. Select this policy when the change affects the behavior described below.

## Required Rules

- **TIMEOUT-1** Set connect/request timeouts and an operation deadline that includes admission waits, attempts, backoff, and cleanup as supported by the client. Support: AWS-RETRY:Timeouts; PolicyNIM decision.
- **TIMEOUT-2** Retry only classified transient failures when repeated effects are safe; bound attempts and elapsed time, with backoff and jitter. Support: AWS-RETRY:Retries and backoff; AWS-RETRY:Jitter.
- **TIMEOUT-3** Respect cancellation and server retry guidance within the remaining budget; stop rather than starting an attempt that can't fit the contract. Support: HTTP:10.2.3; PolicyNIM decision.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Use a controlled slow dependency and fake clock where appropriate; assert attempt count, exhaustion, cancellation, released resources, and safe errors.

## Exceptions and Boundaries

Timeout values follow measured latency, acceptable failure rates, and caller budgets. AWS example percentiles aren't universal requirements; non-idempotent effects need extra protection.

## Examples

Applicable: A transient read timeout retries within one deadline, then stops and releases the connection when the budget expires.

Violating: Each layer retries independently forever, or cancellation is swallowed and more paid calls start.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- AWS-RETRY: [AWS: Timeouts, retries, and backoff with jitter](https://builder.aws.com/content/3EumjoZascWd1oZiEgL8ORlv3qE/timeouts-retries-and-backoff-with-jitter); revision `Published and last modified 2019-12-05`. Sections: Timeouts, Retries and backoff, Jitter.
- HTTP: [RFC 9110: HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html); revision `RFC 9110, June 2022`. Sections: 9.2.2, 15, 10.2.3.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
