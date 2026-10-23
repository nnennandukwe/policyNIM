# Plugin Package and Submission Materials Build Plan

## Summary

Build a reproducible, credential-free OpenAI plugin source package and submission
handoff for PolicyNIM. The authorized implementation starts from pushed main
`0956cca74a7d83091839a03b0d4d1838d54b825f` and fits one PR. Production OAuth
provisioning and directory submission remain owned by the existing OAuth and
originating chats. A source archive and a submission candidate have separate gates.

## Skills To Use

| Stage | Skill | Responsibility and evidence |
| --- | --- | --- |
| Planning | Software Build Plan | Lock package layout, behavior and verification before edits. |
| Requirements | OpenAI Docs | Use current package, submission and annotation requirements. |
| Rules | qodo-get-rules | Apply pinning, docstrings, descriptive names and schema-consumer compatibility. |
| Writing | voice-canon, context-gap, curiosity-gap | Ground listing and reviewer materials in actual behavior; verify final language. |
| Review | qodo-review | Review the complete final diff against a pushed merge base with session context. |
| PR gate | qodo-review-resolver | Require completed exact-head review and assess all findings before merge. |

Coverage: Python's standard library handles ZIP construction and validation.
The existing pinned jsonschema test dependency validates vendored 1.0.0 schemas.
Repository-native tests cover packaging failure paths; broader architecture,
runtime-path, CI-hardening and transaction skills aren't needed for this bounded
package builder and metadata correction. Qodo Local Review is eligible: this
isolated Git worktree has a remote and a pushed common base.

## Scope

In scope: portable package metadata, icons derived from existing brand assets,
strict builder/tests, eight-tool inventory, five positive and three negative
review cases, policy/support drafts, recording script and operator handoff.

Out of scope: issuer/client provisioning, OAuth runtime changes, provider
workloads, paid services, new UI, legal approval, upload, attestations and publication.
OAuth acceptance remains tracked by issue #97.

## Package Layout / File-to-Task Mapping

- New `packaging/openai-plugin/`: publishable source content, pinned schemas and
  provenance, with incomplete deployment values omitted.
- New `scripts/build_plugin.py`: allowlisted deterministic archive construction,
  source validation and strict submission-candidate validation.
- New `tests/test_plugin_package.py`: public-command archive and rejection tests.
- Existing `src/policynim/interfaces/evaluation_jobs_mcp.py` and
  `tests/test_evaluation_jobs_interfaces.py`: cancellation annotation and real
  serialized-discovery regression.
- New `docs/plugin-submission/`: review cases, legal drafts, walkthrough and
  release handoff. These operational materials stay outside the install ZIP.

## Dependencies And Settings

No runtime or development dependency additions. Pin Agent Plugins schemas to
1.0.0 and record their hashes. Use Python 3.11/3.12 and the existing uv lockfile.
Accept a local release-input JSON file for finalized public URLs only. Credentials
and OAuth secrets belong in the dashboard or issuer configuration, never package
inputs. Missing production values remain absent in source mode.

## Canonical Contracts

`uv run python scripts/build_plugin.py --output dist/policynim-plugin-source.zip`
will emit a source-only ZIP and explicitly report submission gates. Adding
`--release-inputs <file>` will require finalized endpoint, four public listing
URLs and recording URL before emitting a submission candidate. ZIP members will
start at `plugin.json`, `mcp.json`, assets and LICENSE. The package declares one
remote MCP server; it won't filter or hide the endpoint's eight tools.

The four guidance tools retain their current interface. `eval_cancel` will
advertise destructiveHint=true independently of additive eval_submit metadata.

## TDD And BDD Implementation Strategy

Verification-first slices will exercise the real builder CLI, inspect resulting
ZIP bytes, validate both manifests offline and reject missing/unsafe release
inputs. SDK discovery will check all eight tools and serialized cancellation
metadata. The submission cases describe planned live acceptance separately.

## Component Design

One script will own rendering, semantic validation and ZIP emission. A fixed
allowlist will exclude local environments, evidence, drafts and private data.
Fixed member order, timestamps and permissions will make identical inputs produce
identical bytes. Validation will finish before replacing an output file. OpenAI
metadata stays in the root manifest's extension; no compatibility overlay or UI
will add another source of truth. Icons will preserve existing asset provenance.

## Failure And Recovery Rules

Malformed inputs, unsafe URLs, invalid icons, unknown fields, missing references,
symlinks or missing submission values will fail before output replacement. The
operator will receive field-specific errors without echoing secret values.
Existing output remains intact on validation failure. Source mode reports missing
integration gates rather than claiming readiness. Reviewers use isolated sample
accounts and privately supplied credentials. Live failures remain recorded as
failures or blocked cases.

## Commit Plan

1. `feat(plugin): add reproducible package and submission handoff` with packaging
   tests and drafts.
2. `fix(mcp): mark evaluation cancellation destructive` with SDK regression.

## Branch And PR Flow

Use the attached plugin-submission worktree from fetched origin/main, preserving
the primary checkout. Link the PR to packaging issue #169 and the #97 dependency.
Run focused checks and both locked offline Python suites, Ruff, Pyright, lock
validation and diff checks. Commit the final diff, pin its pushed merge base,
and run context-bearing Qodo Local Review before opening a ready PR. Reproduce
and fix supported findings, verify and re-review changed code. Push within the
authorized workflow, require current-head CI and completed exact-head Qodo PR
review, refresh main and reconcile concurrent changes before merging. Never
force-push or manually clear finding status.

## Test Plan

Test deterministic bytes and root layout; rejected URLs/credentials/local paths;
schema and metadata limits; image dimensions and references; exact allowlist;
five/three case counts and eight-tool coverage; failure leaves existing output
untouched; serialized cancellation metadata over authenticated HTTP.

Run `uv run --no-sync pytest -q tests/test_plugin_package.py
tests/test_evaluation_jobs_interfaces.py`, then repository-native Ruff, formatting,
Pyright, `uv lock --check`, `git diff --check`, and offline suites on 3.11/3.12.
Run language verification on all authored documents and package strings.

Manual acceptance will require approved public policies, finalized endpoint,
reviewer login and scopes, production discovery and real ChatGPT execution of
the eight cases, plus an accessible recording. Qodo review must cover the final
verified diff; any tool failure stays explicit.

## Definition Of Done

Source ZIP is reproducible, validated and secret-free by allowlist and content
inspection. The strict builder blocks incomplete candidates. All eight tools
appear in the review materials, and cancellation metadata matches documented
behavior. Draft policy and live-test status are clear. Required checks and both
review gates pass before merge. Upload and owner approvals remain explicit
handoff gates with no fabricated execution evidence.

## Assumptions And Defaults

Use portable Agent Plugins 1.0.0, one streamable HTTP server, English listing
copy, existing PolicyNIM brand assets and repository support links. The plugin
package version starts at 0.1.0 independently of runtime version 0.2.1. The
maintainer will approve policy text, trademark rights, public URLs and availability
before submission. No new service or support commitment will be implied.
