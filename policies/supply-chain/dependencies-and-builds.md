---
policy_id: SUPPLY-BUILD-001
title: Dependencies and Reproducible Build Inputs
doc_type: standard
domain: supply-chain
tags:
  - supply-chain
  - engineering
grounded_in:
  - https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-218.pdf
  - https://baseline.openssf.org/versions/2026-08-28
---

# Dependencies and Reproducible Build Inputs

## Intent

Release inputs need a reviewed identity and a build path that can be repeated and inspected.

## Applicability

Applies to build-maintainer, release-operator. Select this policy when the change affects the behavior described below.

## Required Rules

- **BUILD-1** Record direct and transitive dependencies with ecosystem tooling; review updates and vulnerability/support status for the intended release. Support: SSDF:PW.4.1; OSPS:OSPS-BR-05.01; OSPS:OSPS-DO-06.01.
- **BUILD-2** Pin reviewed build/tool inputs and use the lock or equivalent immutable resolution; validate untrusted metadata and isolate unreviewed code from release credentials. Support: OSPS:OSPS-BR-01.01; OSPS:OSPS-BR-01.03; PolicyNIM decision.
- **BUILD-3** Test the packaged artifact from a clean installation and retain the source revision, dependency identities, and changed behavior. Support: PolicyNIM decision.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Record locked dependency resolution, the tested artifact digest, clean-install behavior, and security-relevant release notes. A source-checkout test doesn't establish installed-resource availability.

## Exceptions and Boundaries

Exact patch pins alone don't prove bit-for-bit reproducibility. Document platform, toolchain, network inputs, and remaining nondeterminism before making that claim.

## Examples

Applicable: A clean wheel install includes selected policy packs and passes catalog inspection with locked dependencies.

Violating: A release job downloads an unpinned tool script, exposes signing credentials to unreviewed code, and tests only the checkout.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- SSDF: [NIST SSDF 1.1 final](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-218.pdf); revision `SP 800-218, February 2022`. Sections: PW.4.1.
- OSPS: [OpenSSF OSPS Baseline 2026.08.28](https://baseline.openssf.org/versions/2026-08-28); revision `2026.08.28`. Sections: OSPS-BR-01.01, OSPS-BR-01.03, OSPS-BR-05.01, OSPS-DO-06.01.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
