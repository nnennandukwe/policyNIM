---
policy_id: ARCH-API-001
title: API Versioning Guidance
doc_type: architecture-guidance
domain: architecture
tags:
  - api
  - versioning
  - compatibility
grounded_in:
  - https://github.com/microsoft/api-guidelines/blob/vNext/Guidelines.md
  - https://semver.org/
---

# API Versioning Guidance

## Intent

API changes should preserve predictable client behavior and make breaking changes
obvious, deliberate, and testable.

## Required Rules

- Additive changes do not justify a new API version if existing clients continue to
  work without modification.
- Breaking changes must ship behind an explicit new version boundary. Do not mix
  breaking and non-breaking semantics in the same version.
- Deprecation plans must include the old version, the new version, the migration
  path, and the removal target date.
- Version behavior must be covered by contract tests. Documentation alone is not a
  compatibility strategy.
- Response shapes must not repurpose existing fields with new meaning. Add new fields
  or add a new version.

## Review Expectations

- Reviewers should ask whether the change is additive, breaking, or ambiguous.
- If the change is breaking, verify that rollout, migration, and monitoring notes are
  included before approval.
- Verify example requests and responses reflect the exact versioned behavior.

## Public Grounding

- Microsoft REST API Guidelines informed the compatibility and deprecation rules.
- Semantic Versioning informed the change-classification language.

