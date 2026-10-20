---
policy_id: ARCH-API-001
title: API Compatibility and Version Boundaries
doc_type: standard
domain: architecture
tags:
  - api
  - engineering
grounded_in:
  - https://semver.org/spec/v2.0.0.html
---

# API Compatibility and Version Boundaries

## Intent

Clients need predictable field meaning, error behavior, and migration paths as an API evolves.

## Applicability

Applies to api-producer, api-consumer. Select this policy when the change affects the behavior described below.

## Required Rules

- **API-1** Classify changes against documented supported clients; test new fields against strict consumers before calling the change additive. Support: PolicyNIM decision.
- **API-2** Publish incompatible behavior behind a reviewed version boundary with a migration path, owner, and removal conditions. Support: PolicyNIM decision.
- **API-3** For packages adopting SemVer, declare the public API and apply its compatibility rules; an HTTP API version scheme remains a separate project decision. Support: SEMVER:1; SEMVER:7; SEMVER:8.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Retain old/new request and response examples, consumer contract results, changed semantics, and the deprecation plan. Field reuse and changed defaults need review.

## Exceptions and Boundaries

A proven compatible optional field can keep the current API version. SemVer doesn't prescribe HTTP path versioning or a universal deprecation duration.

## Examples

Applicable: A renamed response field ships in a new API version while the old supported client still passes its contract suite.

Violating: A field keeps its name but changes from an object to a list in the same supported API version.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- SEMVER: [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html); revision `2.0.0`. Sections: 1, 7, 8.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
