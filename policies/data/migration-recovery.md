---
policy_id: DATA-MIGRATE-001
title: Data Migration and Recovery
doc_type: standard
domain: data
tags:
  - data
  - engineering
grounded_in:
  - https://sre.google/sre-book/data-integrity/
---

# Data Migration and Recovery

## Intent

Schema and data changes need a safe path through partial execution and mixed application versions.

## Applicability

Applies to application-developer, database-operator. Select this policy when the change affects the behavior described below.

## Required Rules

- **MIGRATE-1** Declare compatibility across the rollout, durable progress, interruption behavior, and the safe retry or forward-recovery action before migration. Support: PolicyNIM decision.
- **MIGRATE-2** Verify recovery from retained data with the real reader; a backup file alone isn't evidence of a working restore. Support: SRE-DATA:Delivering a Recovery System, Rather Than a Backup System.
- **MIGRATE-3** Identify destructive or irreversible steps, approvals, and retention/deletion obligations; don't claim rollback after information has been discarded. Support: PolicyNIM decision.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Run the migration on representative old data, interrupt it, resume or restore, and verify old/new readers within the declared compatibility window.

## Exceptions and Boundaries

Database DDL and transaction behavior vary by engine. Online migration patterns and recovery time targets require a workload-specific decision; no universal numeric target is prescribed.

## Examples

Applicable: A staged backfill stores progress, survives a killed worker, and resumes without overwriting a concurrent write.

Violating: A migration drops the only old column before old instances drain and calls an untested backup its rollback plan.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- SRE-DATA: [Google SRE: Data Integrity](https://sre.google/sre-book/data-integrity/); revision `Site Reliability Engineering, 2016, chapter 26`. Sections: Delivering a Recovery System, Rather Than a Backup System.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
