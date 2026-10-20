---
policy_id: API-ERROR-001
title: HTTP Error Contracts
doc_type: standard
domain: api
tags:
  - api
  - engineering
grounded_in:
  - https://www.rfc-editor.org/rfc/rfc9110.html
  - https://www.rfc-editor.org/rfc/rfc9457.html
---

# HTTP Error Contracts

## Intent

API callers need stable, safe error information they can handle without parsing internal exception text.

## Applicability

Applies to api-producer, api-consumer. Select this policy when the change affects the behavior described below.

## Required Rules

- **ERROR-1** Use HTTP status semantics consistently; where problem details are adopted, keep their documented member meanings. Support: HTTP:15; PROBLEM:3.
- **ERROR-2** Exclude stack traces, credentials, internal paths, and sensitive request content from public problem details. Support: PROBLEM:5.
- **ERROR-3** Test caller recovery for validation, authorization, missing resources, overload, and dependency failure where those outcomes apply. Support: PolicyNIM decision.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Capture sanitized wire responses and strict client handling, including content type and status. Document retryability without treating every server error as transient.

## Exceptions and Boundaries

RFC 9457 is an optional common error format. Existing documented error formats can remain; this policy doesn't require converting every response into problem details.

## Examples

Applicable: An API returns a documented problem type and safe instance reference; the client handles the status and type.

Violating: A public error body includes a database connection string and tells callers to retry every authorization denial.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- HTTP: [RFC 9110: HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html); revision `RFC 9110, June 2022`. Sections: 15.
- PROBLEM: [RFC 9457: Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457.html); revision `RFC 9457, July 2023`. Sections: 3, 5.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
