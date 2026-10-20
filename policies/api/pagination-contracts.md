---
policy_id: API-PAGE-001
title: Pagination and Collection Stability
doc_type: standard
domain: api
tags:
  - api
  - engineering
grounded_in:
  - https://datatracker.ietf.org/doc/html/rfc8288
---

# Pagination and Collection Stability

## Intent

A changing collection needs an explicit traversal contract so callers can detect completion and avoid unexpected duplicates or omissions.

## Applicability

Applies to api-producer, api-consumer. Select this policy when the change affects the behavior described below.

## Required Rules

- **PAGE-1** Document ordering, continuation semantics, end-of-list behavior, and behavior under concurrent inserts or deletes. Support: PolicyNIM decision.
- **PAGE-2** Validate and bound page-size and continuation inputs; preserve authorization scope when using continuation tokens. Support: PolicyNIM decision.
- **PAGE-3** If HTTP Link headers are used, follow their relation and serialization semantics; test malformed, expired, and terminal continuations. Support: LINK:2.1; LINK:3; PolicyNIM decision.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Show traversal over empty and multi-page collections plus a concurrent update. State whether snapshot consistency or duplicate-free traversal is actually guaranteed.

## Exceptions and Boundaries

RFC 8288 defines links, not a mandatory pagination algorithm. Cursor format, limits, and consistency guarantees are project choices; fixed small in-memory results can omit pagination.

## Examples

Applicable: A cursor is bound to the caller's tenant and query; terminal pages omit a continuation, with mutation behavior documented.

Violating: A shared cursor exposes another tenant's next page and loops forever after the final item.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- LINK: [RFC 8288: Web Linking](https://datatracker.ietf.org/doc/html/rfc8288); revision `RFC 8288, October 2017`. Sections: 2.1, 3.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
