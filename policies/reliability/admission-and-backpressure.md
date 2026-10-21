---
policy_id: REL-OVERLOAD-001
title: Admission Control and Backpressure
doc_type: standard
domain: reliability
tags:
  - reliability
  - engineering
grounded_in:
  - https://sre.google/sre-book/handling-overload/
---

# Admission Control and Backpressure

## Intent

One slow or expensive workload mustn't consume unbounded resources or prevent unrelated callers from progressing.

## Applicability

Applies to application-developer, service-operator. Select this policy when the change affects the behavior described below.

## Required Rules

- **OVERLOAD-1** Bound queued and running work using relevant resource costs; reject overload before allocating expensive work. Support: SRE-OVERLOAD:The Pitfalls of "Queries per Second"; PolicyNIM decision.
- **OVERLOAD-2** Define caller fairness and overload responses; avoid retry behavior that amplifies an overloaded dependency. Support: SRE-OVERLOAD:Per-Customer Limits; SRE-OVERLOAD:Client-Side Throttling.
- **OVERLOAD-3** Test saturation, waiting-call cancellation, resource release, and recovery after pressure falls. Support: PolicyNIM decision.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Record workload mix, configured limits, wait/rejection outcomes, resource usage, and the capacity actually tested. Include another caller's progress during saturation.

## Exceptions and Boundaries

Per-customer quotas and degradation require product decisions. A request count alone doesn't certify capacity for unequal-cost operations; this policy supplies no universal throughput claim.

## Examples

Applicable: A saturated expensive-job lane rejects a new job promptly while a lightweight operation still completes.

Violating: Every request enters an unbounded queue and rejected clients retry immediately without limits.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- SRE-OVERLOAD: [Google SRE: Handling Overload](https://sre.google/sre-book/handling-overload/); revision `Site Reliability Engineering, 2016, chapter 21`. Sections: The Pitfalls of "Queries per Second", Per-Customer Limits, Client-Side Throttling.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
