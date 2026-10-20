---
policy_id: SUPPLY-PROVENANCE-001
title: Release Provenance and Consumer Verification
doc_type: standard
domain: supply-chain
tags:
  - supply-chain
  - engineering
grounded_in:
  - https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-218.pdf
  - https://baseline.openssf.org/versions/2026-08-28
  - https://slsa.dev/spec/v1.2/verifying-artifacts
---

# Release Provenance and Consumer Verification

## Intent

Producing provenance describes a build; a consumer still needs to verify the artifact against trusted expectations.

## Applicability

Applies to build-maintainer, release-operator, artifact-consumer. Select this policy when the change affects the behavior described below.

## Required Rules

- **PROVENANCE-1** Bind released bytes to source/build identity and distribute authenticated integrity evidence appropriate to the release channel. Support: SSDF:PS.2.1; OSPS:OSPS-BR-06.01.
- **PROVENANCE-2** Before accepting an artifact, verify its digest, authenticated provenance, trusted builder, and expected build parameters according to the selected SLSA verification policy. Support: SLSA:How to verify; SLSA:Forming Expectations.
- **PROVENANCE-3** Record producer evidence and consumer verification separately; don't claim a SLSA level or compliance result from an uploaded attestation alone. Support: PolicyNIM decision.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Show a successful verification plus altered artifact, untrusted signer/builder, and unexpected parameter rejection. Retain the verifier policy and exact artifact digest.

## Exceptions and Boundaries

SLSA levels depend on their full track requirements and trust model. This selected guidance doesn't assess a builder or certify SSDF, SLSA, or OSPS compliance.

## Examples

Applicable: A consumer validates the envelope and trusted builder, matches the subject digest, and rejects a modified artifact.

Violating: A pipeline uploads unsigned provenance and labels every download verified without running a consumer check.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- SSDF: [NIST SSDF 1.1 final](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-218.pdf); revision `SP 800-218, February 2022`. Sections: PS.2.1.
- OSPS: [OpenSSF OSPS Baseline 2026.08.28](https://baseline.openssf.org/versions/2026-08-28); revision `2026.08.28`. Sections: OSPS-BR-06.01.
- SLSA: [SLSA 1.2: Verifying artifacts](https://slsa.dev/spec/v1.2/verifying-artifacts); revision `1.2`. Sections: How to verify, Forming Expectations.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
