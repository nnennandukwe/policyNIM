---
# Recommended policy shape. Individual docs may omit fields when they are not
# available yet, as long as the markdown stays readable and the policy intent is
# still clear.
policy_id: TEMPLATE-000
title: Replace with policy title
doc_type: standard
domain: backend
tags:
  - replace-me
grounded_in:
  - https://example.com/public-reference
---

# Replace with policy title

## Intent

State the engineering outcome this policy protects.

## Required Rules

- State the rules that must be followed.
- Keep them specific and enforceable.

## Review Expectations

- State what reviewers should check.
- Include rollout, observability, and test expectations when relevant.

## Public Grounding

- Link the public standards or guidance the synthetic team policy was derived from.

## Optional Notes

- Use this shape as a guide, not a parser contract.
- Some policies may not have `grounded_in` yet, or may add extra sections for
  exceptions, rollout notes, or operator steps.
