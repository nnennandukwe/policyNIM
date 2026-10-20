# Private Auth SQLite Build Plan

## Summary

The production observation found the root-owned auth database at mode 0644. This follow-up starts from main `509fba9` and makes both account and operation writers open private auth state without changing quota APIs, schema, records or production state. The review unit is one auth-specific storage helper, its two callers, focused tests and operator guidance.

## Skills To Use

| Stage | Skill | Responsibility |
|---|---|---|
| Plan | qodo-get-rules | Apply retrieved Python docstring, English text and storage-layer rules. |
| Design | policynim-runtime-path-safety | Preserve the configured final path component for link rejection. |
| Verification | failure-path-testing | Reproduce creation, existing data, link and permission failures through public stores. |
| Storage | sqlite-concurrency-hardening | Keep short existing transactions and verify independent concurrent stores. |
| Lifecycle | policynim-service-lifecycle-cleanup | Close descriptors and failed connections on every path. |
| Audit | dx-audit | Check safe actionable errors and POSIX scope claims. |
| Review | qodo-review | Review the full final diff against the pushed main baseline, fix supported findings and re-review. |

Qodo Local Review is eligible: this Git worktree has a published base and authenticated repository access. No transaction redesign or new filesystem manager is planned.

## Scope

In scope: auth database and existing SQLite sidecar modes, safe regular owned files, final-component link rejection, preserving existing records, sanitized errors and focused runtime evidence.

Out of scope: production repair, deployment, Windows ACL provisioning, generic filesystem changes, new quota semantics and database encryption.

## Package Layout / File-to-Task Mapping

- `storage/auth_database.py`: auth-specific private SQLite connection preparation.
- `storage/auth_store.py`, `storage/operation_store.py`: use that connection seam for existing writes.
- `services/beta_auth.py`, `services/operation_admission.py`: retain the lexical final component until storage validation.
- `tests/test_auth_database_permissions.py`: actual creation, sidecar, preservation and rejection witnesses.
- `docs/hosted-beta-operations.md`: permission contract and recovery guidance.

## Dependencies And Settings

No dependencies, schema versions or environment variables change. Python 3.11/3.12 and SQLite remain pinned. POSIX modes are enforced; Windows ACL privacy remains an operator responsibility.

## Canonical Contracts

New auth files start at mode 0600 regardless of a permissive caller mask. Existing service-owned regular files are tightened in place without replacing their inode or records. Newly created SQLite journals inherit the main file's mode. Symlinks, multiply linked files, nonregular files and foreign-owned POSIX files are rejected before chmod or SQL work. Runtime code doesn't change the process-wide umask. The configured parent remains a trusted operator-managed directory.

## TDD And BDD Implementation Strategy

First reproduce creation under child-process umask 000, retained 0644 database/sidecars, and linked-file rejection through both public stores. Then implement preparation before SQLite opens and verify actual WAL, SHM and rollback journals. Follow with factory-path, failure cleanup and concurrent initialization tests.

## Component Design

The storage helper owns only descriptor validation and auth SQLite connection setup. It doesn't import services or HTTP. Existing stores retain SQL and transaction ownership. Database identity is checked around connection creation; writable parent directories require trusted deployment ownership. No new filesystem manager or protocol is introduced for the storage adapter.

## Failure And Recovery Rules

Unsafe files aren't chmodded, replaced or deleted. Permission preparation fails with a static `auth_storage_unsafe` or `auth_storage_unavailable` message, without private paths. Failed connections and descriptors close. Operators preserve the database, inspect ownership and path aliases, provision a private directory and retry as the service user. This change doesn't transfer ownership or repair production itself.

## Commit Plan

1. `fix(auth): protect SQLite files and preserve existing state` with public regression witnesses and operator guidance.
2. Follow-up supported review fixes, if any, with their failing-before evidence.

## Branch And PR Flow

Use isolated `fix/private-auth-sqlite` from published main `509fba9`. Run focused and strict offline tests, static and language checks, then full local Qodo review. Open a ready PR after supported findings are fixed; collect exact-head remote Qodo and CI. Don't merge or deploy until root coordinates release ordering.

## Test Plan

Run focused auth, operation admission and permission tests, then `uv run pytest -q -m 'not live and not docker_live'`, Ruff/format/Pyright and language verification. Use child-process umask changes only. Real filesystem tests retain target bytes and modes on rejection. No NVIDIA calls or production writes are needed.

## Definition Of Done

Both writers create private auth bytes, retain old data while fixing permissions, reject unsafe aliases, close failed resources, and preserve existing public quota behavior. Required tests and reviews identify the exact source. Production repair and Windows ACLs aren't claimed.

## Assumptions And Defaults

The service user owns the configured parent and may tighten its own files. Other processes running as that same user and hostile replacement of operator-controlled ancestors are outside this file-mode boundary. Existing broad directory trees aren't chmodded recursively.
