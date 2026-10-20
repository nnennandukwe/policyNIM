---
policy_id: TEST-FAIL-001
title: Failure and Recovery Verification
doc_type: standard
domain: testing
tags:
  - testing
  - engineering
grounded_in:
  - https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-218.pdf
---

# Failure and Recovery Verification

## Intent

Changes that can reject work or interrupt an effect need evidence that recovery preserves the intended state.

## Applicability

Applies to application-developer, service-operator. Select this policy when the change affects the behavior described below.

## Required Rules

- **FAIL-1** Exercise rejection, dependency failure, interruption, and retry through the affected runtime boundary; assert durable state and absence of forbidden effects. Support: PolicyNIM decision.
- **FAIL-2** Choose test types from the change risk and record failures, remediations, and the exact tested revision. Support: SSDF:PW.8.1; SSDF:PW.8.2.
- **FAIL-3** Verify the documented recovery action works after the injected failure before describing the workflow as recoverable. Support: PolicyNIM decision.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Retain the failing trigger, expected state, actual runtime result, recovery attempt, and revision. Test doubles alone can verify component decisions; they don't establish process or deployment recovery.

## Exceptions and Boundaries

A static text correction without a runtime effect doesn't need fault injection. These are project verification rules; SSDF addresses security testing and doesn't mandate every listed general reliability case.

## Examples

Applicable: A publisher is killed before activation; restart reads the old complete state, a retry succeeds, and the report includes the tested commit.

Violating: Only a mocked happy path passes; the change is declared crash-safe without restarting the process.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- SSDF: [NIST SSDF 1.1 final](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-218.pdf); revision `SP 800-218, February 2022`. Sections: PW.8.1, PW.8.2.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
