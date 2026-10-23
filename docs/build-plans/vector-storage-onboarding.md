# Vector storage and local onboarding

Authority: the October 22 product research and the user's October 23 instruction to implement it. The first delivery covers SQLite, Qdrant, pgvector, local/self-hosted setup UI, business-rule documentation, and an advisory Habit Hooks trial. Chroma remains a researched candidate.

## Storage transaction gate

Canonical state consists of the sealed local source/catalog/SQLite/runtime-rule generation and its existing `active.json` pointer. External indexes are derived replicas for vector retrieval. Their records contain identifiers, domains, and vectors; authoritative policy text stays in the sealed generation. External indexes use a namespace derived from the full generation digest and an operator-selected prefix. Existing generation formats and offline bundle operations stay compatible.

| State | Transition | Visible result |
| --- | --- | --- |
| Prior generation active | Build and seal candidate | Prior generation remains selected |
| Candidate sealed | Create a new external namespace and upload vectors | Candidate remains inactive |
| External namespace exists | Validate its complete records against canonical vectors | Matching complete namespace can be reused; other existing data is preserved and refused |
| Candidate and external index validated | Existing `os.replace` of `active.json` | New generation becomes active |
| Preparation interrupted | Existing local staging cleanup | Prior pointer is unchanged; an incomplete remote namespace remains for operator inspection |
| Activated | Cleanup or sync fails | Committed result with warning; inspect before retrying |

No supported operation overwrites or deletes an existing external namespace. A partial upload requires a fresh prefix or operator recovery. Independent roots must use different prefixes. Credentials and remote administrative writers are trusted; this contract detects observed mismatch and doesn't isolate against an administrator changing storage concurrently.

## Invariants

1. Activation happens only after complete source artifacts and the configured external index validate.
2. Retrieval always resolves returned identifiers to the held canonical generation; remote payloads never supply authoritative policy text.
3. Existing external collections/tables are never overwritten, adopted on counts alone, or automatically deleted.
4. A failure before pointer replacement preserves the prior pointer. Cleanup after replacement can't turn a committed result into an aborted result.
5. Readers reject incomplete, incompatible, or unavailable external indexes before embedding requests; choosing SQLite remains independent of external dependencies.

## Fault matrix and lifecycle coverage

| Boundary | Injected failure | Persistent state and recovery | Planned evidence |
| --- | --- | --- | --- |
| Configuration parse | Wrong backend, unsafe endpoint, invalid namespace, missing secret | No provider or database mutation; correct configuration | Settings and CLI rejection tests |
| Canonical read | Missing generation, changed artifact, incompatible model | Existing generation preserved; inspect/recover it | Existing generation tests plus external reader tests |
| Remote inspect | Authentication, transport, malformed response, wrong vectors/domain/IDs | No overwrite; correct access or choose unused prefix | Protocol fixtures and real database tests |
| Namespace create | Existing unowned name or concurrent creator | Existing namespace preserved; inspect or use another prefix | Collision and retry tests |
| Upload | Partial upload, timeout, process interruption | Prior pointer preserved; incomplete remote data retained | Build failpoint integration tests |
| Read-back verification | Missing/extra/tampered records | Candidate isn't activated | Shared backend contract tests |
| Pointer replacement | Existing publication fault boundaries | Prior or new complete generation according to commit point | Existing transaction suite plus external-before-activation test |
| Read/query | Wrong dimension, nonfinite vector, wrong-domain or unknown returned ID | Refuse output; preserve sources | Retrieval contract tests |
| Restart/rollback | New service and retained prior generation | Validate namespace for the selected generation | Real consumer/restart/rollback tests |
| Remote retention | Local prune removes a generation | Remote data retained; manual database retention | Documented limitation; no remote delete API |

## Local UI boundary

The setup server binds loopback only and uses a per-launch secret, exact Host/Origin checks, bounded JSON requests, and no third-party assets. The handler validates transport input and delegates to an onboarding service. The service previews policy selection, probes storage without writing, builds on explicit submission, verifies a task, and writes a new private configuration file without overwriting existing files. The shared hosted beta links to this flow; it doesn't accept private packs or per-user database credentials.

Visual thesis: the existing PolicyNIM palette and typography, with one calm setup workspace and an ordered progress rail. Content: deployment and privacy, policy sources, storage, build/validation, first result and saved configuration. Interaction: focused step transitions and a busy/status state; reduced-motion preferences are honored.

## Verification and review

Run focused settings, storage, generation, CLI, and UI tests, then Ruff, Pyright, the offline suite, and lockfile checks. Exercise real Qdrant and pgvector containers with synthetic vectors and no model calls. Inspect the UI in a browser. Run Habit Hooks as an advisory check in an isolated environment and record supported findings separately from code correctness. Use final local Qodo review and a ready-for-review PR with exact-head Qodo/CI evidence before merge. Review failures remain visible.
