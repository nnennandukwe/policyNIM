---
policy_id: OBS-READINESS-001
title: Operational Signals and Readiness
doc_type: standard
domain: observability
tags:
  - observability
  - engineering
grounded_in:
  - https://sre.google/sre-book/monitoring-distributed-systems/
---

# Operational Signals and Readiness

## Intent

Traffic admission and incident response need signals tied to usable service behavior.

## Applicability

Applies to application-developer, service-operator. Select this policy when the change affects the behavior described below.

## Required Rules

- **READINESS-1** Observe latency, traffic, errors, and saturation where they explain the supported workload; keep label cardinality and sensitive values bounded. Support: SRE-MONITOR:The Four Golden Signals; PolicyNIM decision.
- **READINESS-2** Define readiness from required local capabilities and validated artifacts; keep routine public probes bounded and free of paid dependency calls. Support: PolicyNIM decision.
- **READINESS-3** Test healthy, unavailable, recovering, and misconfigured states through the actual probe and caller path; document operator action for actionable alerts. Support: SRE-MONITOR:Black-Box Versus White-Box; PolicyNIM decision.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Retain actual probe responses, safe telemetry, and a recovery observation. A process listening on a socket doesn't establish that its required data can be served.

## Exceptions and Boundaries

Liveness, readiness, dependency diagnostics, and end-user success serve different purposes. Probe design and alert targets are project recommendations, not mandated by Google SRE.

## Examples

Applicable: A missing index makes readiness fail safely; a validated replacement restores it without model calls.

Violating: Health always returns success while searches fail, and raw customer prompts become metric labels.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- SRE-MONITOR: [Google SRE: Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/); revision `Site Reliability Engineering, 2016, chapter 6`. Sections: The Four Golden Signals, Black-Box Versus White-Box.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
