---
policy_id: ARCH-JOB-001
title: Background Jobs and Duplicate Effects
doc_type: standard
domain: architecture
tags:
  - reliability
  - engineering
grounded_in:
  - https://builder.aws.com/content/3EumjoZascWd1oZiEgL8ORlv3qE/timeouts-retries-and-backoff-with-jitter
---

# Background Jobs and Duplicate Effects

## Intent

Workers need bounded retries and a recovery contract when delivery or acknowledgement repeats.

## Applicability

Applies to application-developer, service-operator. Select this policy when the change affects the behavior described below.

## Required Rules

- **JOB-1** Use idempotent effects or durable deduplication at the effect boundary; a lost acknowledgement can occur after an effect succeeds. Support: AWS-RETRY:Retries and backoff; PolicyNIM decision.
- **JOB-2** Bound retries and worker concurrency, classify terminal failures, and give operators a documented retry or dead-letter action. Support: PolicyNIM decision.
- **JOB-3** Keep credentials out of job payloads and telemetry; preserve job correlation and report progress for long operations. Support: PolicyNIM decision.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Test duplicate delivery, crash after the effect before acknowledgement, exhausted retries, and an operator retry. Verify the intended side effect occurs once within the documented deduplication window.

## Exceptions and Boundaries

Exactly-once delivery isn't inferred from a queue setting. Deduplication scope and retention are workload decisions; an explicitly harmless repeat can be documented as such.

## Examples

Applicable: A payment job reuses a durable operation key after worker restart and records the prior effect instead of charging again.

Violating: An acknowledgement timeout triggers a second charge, while poison jobs retry without a terminal state.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- AWS-RETRY: [AWS: Timeouts, retries, and backoff with jitter](https://builder.aws.com/content/3EumjoZascWd1oZiEgL8ORlv3qE/timeouts-retries-and-backoff-with-jitter); revision `Published and last modified 2019-12-05`. Sections: Timeouts, Retries and backoff, Jitter.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
