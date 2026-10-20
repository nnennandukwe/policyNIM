---
policy_id: DATA-TRANSACTION-001
title: Transaction Boundaries and External Effects
doc_type: standard
domain: data
tags:
  - data
  - engineering
grounded_in:
  - https://www.postgresql.org/docs/18/tutorial-transactions.html
---

# Transaction Boundaries and External Effects

## Intent

Coupled durable changes need a named commit point and an explicit treatment of effects outside the database.

## Applicability

Applies to application-developer, database-operator. Select this policy when the change affects the behavior described below.

## Required Rules

- **TRANSACTION-1** For PostgreSQL multi-step invariants, use an explicit transaction and test that a failure before commit leaves no partial visible update. Support: POSTGRES:3.4.
- **TRANSACTION-2** Treat external messages, files, and provider calls as separate effects unless the implementation proves a shared commit protocol. Support: PolicyNIM decision.
- **TRANSACTION-3** Test concurrent updates, post-commit failure, and retry; report committed work accurately when cleanup or acknowledgement fails. Support: PolicyNIM decision.

## Recommended Guidance

Choose mechanisms that fit the system's actual failure model. Requirements marked `PolicyNIM decision` are project review rules, informed by the cited sources where named.

## Review and Test Expectations

Record the commit operation, invariant checks, failure location, resulting durable state, and duplicate-effect handling. Keep transaction isolation assumptions explicit.

## Exceptions and Boundaries

The PostgreSQL example doesn't establish identical guarantees for every datastore. Outboxes, compensation, and distributed transactions are design options that need their own proof.

## Examples

Applicable: A debit and credit roll back together; a post-commit notification uses durable pending work and deduplication.

Violating: A debit commits before the credit, or a database rollback is claimed to undo a payment already sent to an external provider.

Out of scope: An unrelated editorial-only change by a documentation editor doesn't alter this policy's runtime or delivery behavior.

## Sources and Change Notice

- POSTGRES: [PostgreSQL 18: Transactions](https://www.postgresql.org/docs/18/tutorial-transactions.html); revision `PostgreSQL 18`. Sections: 3.4.

Original PolicyNIM review guidance; source text isn't reproduced. Attribution, source reuse terms, and the reviewed source sections are recorded in `pack.toml` and `licenses/ENGINEERING-SOURCES.txt`. This policy doesn't certify a project against an external framework.
