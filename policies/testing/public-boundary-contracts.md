---
policy_id: TEST-CONTRACT-001
title: Public Boundary Contract Tests
doc_type: standard
domain: testing
tags:
  - testing
  - engineering
grounded_in:
  - https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-218.pdf
  - https://sre.google/sre-book/testing-reliability/
---

# Public Boundary Contract Tests

## Intent

A producer change can pass its own tests while breaking a strict consumer.

## Applicability

Applies to application-developer, library-maintainer. Select this policy when the change affects the behavior described below.

## Required Rules

- **CONTRACT-1** Exercise changed requests, serialized responses, errors, and persisted records through the public consumer contract. Support: PolicyNIM decision.
- **CONTRACT-2** Keep a named regression for a fixed defect and verify behavior at the integration boundary that exposed it. Support: SSDF:PW.8.2; SRE-TEST:Traditional Tests.
- **CONTRACT-3** Report unsupported platforms and skipped live checks separately from passing offline fixtures. Support: PolicyNIM decision.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Use a strict consumer fixture or real supported client, the previous supported payload shape, and a rejected malformed shape. Record platform and dependency identities.

## Exceptions and Boundaries

Internal refactors with unchanged public behavior can reuse existing contract tests. Synthetic fixtures don't prove semantic retrieval or live provider quality.

## Examples

Applicable: A strict consumer accepts the new optional generation field and still rejects malformed IDs; older payloads remain readable.

Violating: The producer tests only its internal dictionary while an installed consumer rejects the new field.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- SSDF: [NIST SSDF 1.1 final](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-218.pdf); revision `SP 800-218, February 2022`. Sections: PW.8.1, PW.8.2, PW.4.1, PS.2.1, PS.3.1.
- SRE-TEST: [Google SRE: Testing for Reliability](https://sre.google/sre-book/testing-reliability/); revision `Site Reliability Engineering, 2016, chapter 17`. Sections: Types of Software Testing, Traditional Tests, Production Tests.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
