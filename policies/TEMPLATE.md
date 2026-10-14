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
# Optional runtime guardrails for local execution.
# Each entry must use exactly one matcher family: `path_globs`,
# `command_regexes`, or `url_host_patterns`.
# Authored effect values are `confirm` or `block`.
# allow is not an authored runtime rule effect.
# runtime_rules:
#   - action: shell_command
#     effect: confirm
#     reason: Review deploy commands before execution.
#     command_regexes:
#       - "^make deploy$"
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
- Add `runtime_rules` only when the policy needs deterministic local execution
  controls; keep each rule narrow and use exactly one matcher family.

## Register A New Policy

1. Save a separate Markdown file with a unique `policy_id`.
2. If the corpus has `pack.toml`, add a `[[policies]]` entry with that ID and its
   path relative to the pack directory. Only listed files enter the catalog.
3. Run `policynim policies validate --format json`. Check `unlisted_markdown` for
   files that aren't selected; a valid manifest doesn't automatically include them.
4. Review `policynim policies show <policy-id>` before rebuilding into fresh index
   and runtime-rules destinations. See `docs/policy-packs.md` for source metadata.
