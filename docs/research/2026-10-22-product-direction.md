# PolicyNIM: business rules, extensibility, and guided onboarding

Start with the policy lifecycle and one supported external database adapter. Evaluate Habit Hooks alongside that work as an optional development tool.

Research date: October 22, 2026. This brief records findings and proposed product work. Application code, agent configuration, hosted services, and the GitHub roadmap weren't changed.

## The product argument

PolicyNIM helps developers and coding agents work from a team's reviewed engineering policies. Its strongest product story is how those policies become a controlled, inspectable input to engineering work: which sources apply, which local decisions replace a baseline, which version a result uses, and what happens when an action matches a runtime rule.

Suggested positioning:

> Bring your team's engineering policies into the coding workflow. PolicyNIM combines reviewed policy packs, preserves their sources and versions, and gives agents the complete policies selected for a task. Its runtime wrapper checks supported actions against declared rules and records the result.

The case for investing in that behavior is stronger than a feature list centered on embeddings, reranking, and model calls. Those services help locate sources; the product's value depends on the policy ownership, change control, and decision behavior around them. Whether users value that enough to adopt PolicyNIM still needs direct observation.

## What already exists

Repository findings use GitHub `main` at `0956cca74a7d83091839a03b0d4d1838d54b825f`, checked against the live GitHub API. The working checkout was at `223e596a09154a0e2be34a115e148cd4214aca77`; it was preserved. Qodo's read-only source lookup at the reviewed main SHA also confirmed that the index factory still constructs SQLite stores.

| Business behavior | Existing mechanism | Why a user would care |
| --- | --- | --- |
| Decide which policies govern the installation | Explicit pack membership, pinned profiles, reviewed additions, exclusions, replacements, and declared conflicts | A team can maintain its own decisions alongside a public baseline. Stale override targets block an update. |
| Preserve policy authority through retrieval | Task-aware routing, complete selected source bodies, owner-bound citations, and output validation | The result keeps conditions and exceptions attached to the selected policy. |
| Keep versions consistent | Complete generations bind source snapshots, catalogs, vectors, and runtime rules; readers retain a generation | A policy update can be activated or rolled back without mixing artifacts from different versions. |
| Apply declared rules to supported actions | Deterministic shell-command, file-path, and HTTP-host matching; block takes precedence over confirmation | A declared rule can stop an action or require a person's confirmation through the runtime wrapper. |
| Explain execution outcomes | Initial decision evidence, confirmation outcomes, terminal execution records, and generation IDs | A developer can inspect what was checked and what actually happened. |

Sources: [profile composition](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/docs/policy-profiles.md), [source-extractive output](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/docs/source-extractive-guidance.md), [generation lifecycle](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/docs/policy-generations.md), [runtime decisions](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/src/policynim/services/runtime_decision.py#L101), [runtime execution](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/src/policynim/services/runtime_execution.py).

The current default compiler and preflight return complete policy quotations. Fields named `plan_steps` and `required_steps` carry source bodies; advisory fields stay empty. Product copy should describe this output accurately. Reintroducing synthesized implementation advice would require a separately reviewed response contract. Policy applicability still requires judgment. Runtime enforcement covers actions submitted through PolicyNIM's wrapper; its matcher coverage defines what it can check.

### A stronger demonstration of the business logic

Use a data migration as the worked example. The existing `DATA-MIGRATE-001` policy asks teams to declare interruption/recovery behavior and verify recovery through the real reader. Its policy body also preserves engine-specific limits and examples. [Migration policy](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/policies/data/migration-recovery.md).

Proposed demonstration:

1. Select a public baseline plus a team-owned migration policy pack.
2. Preview a reviewed replacement, including the target digest and reason.
3. Publish a complete generation and retrieve the full selected policy for a migration task.
4. Submit a separately declared matching runtime action through the wrapper and inspect its decision and evidence.
5. Change the baseline source and show that an old replacement pin needs renewed review before publication.

The migration prose itself doesn't automatically become an executable rule. The demonstration needs an explicitly authored supported matcher for step 4. Run the demonstration before presenting its outcome as observed evidence.

## Habit Hooks: recommendation and fit

[Habit Hooks](https://github.com/habit-hooks/habit-hooks) runs detectors and maps normalized findings to coaching guides. Its Python plugin adds structural checks around Ruff and other detectors. Its documented plugin interfaces make a future PolicyNIM adapter plausible. The inspected product was MIT licensed, with PyPI version `1.5.0`. [Versioned package metadata](https://pypi.org/project/habit-hooks/1.5.0/), [architecture](https://github.com/habit-hooks/habit-hooks/blob/5603331f8c31b1537761e6639f49b876acf0a4e4/docs/architecture.md).

Two possible uses have different scopes:

| Use | Recommendation |
| --- | --- |
| Help an agent improve PolicyNIM's source code | Pilot Python/generic checks in an isolated checkout. Compare useful findings and repair outcomes against the existing Ruff, Pyright, tests, and review workflow. Start advisory. |
| Turn PolicyNIM failures into agent feedback | Explore one optional adapter for a typed, verifiable failure. Include the policy version, evidence, failure category, and bounded correction; rerun the original check after revision. |

Don't copy its README instruction that promotes tool output to the highest-priority user instruction. Findings and coaching remain tool-provided data within the user's authorized task. The linked evaluation is an author-reported prompt-framing experiment on small Python examples; a PolicyNIM trial would need behavior checks, false-positive review, and separate token/time measurements. [Agent instructions](https://github.com/habit-hooks/habit-hooks/blob/5603331f8c31b1537761e6639f49b876acf0a4e4/README.md#agent-integration), [evaluation](https://github.com/LiinaSuoniemi/prompt-vs-metric-eval).

Read the [Habit Hooks source notes](2026-10-22-habit-hooks-source-notes.md) for detector coverage, extension points, evaluation limits, and pilot measurements. Habit Hooks hasn't been installed or tested here.

## Bring-your-own vector database

Make database ownership a supported product capability. Keep the first-run default simple and publish a bounded adapter contract that another developer can implement.

| Proposed option | Role | Recommendation |
| --- | --- | --- |
| Local SQLite + sqlite-vec | Embedded, no separate database service | Keep the current default. |
| Qdrant | Dedicated self-hosted vector database | Build the first external adapter against its server mode. |
| PostgreSQL + pgvector | Vector search in an existing relational database | Add next for teams already operating Postgres. |
| Chroma | Existing local or server-based Chroma installations | Retain as a later candidate after validating a supported server/auth version. |

These projects have open-source options: sqlite-vec uses MIT/Apache-2.0 licensing, Qdrant and Chroma use Apache-2.0, and pgvector uses a PostgreSQL-style license. Infrastructure and model-provider costs remain separate. [sqlite-vec](https://alexgarcia.xyz/sqlite-vec/), [Qdrant](https://github.com/qdrant/qdrant), [pgvector](https://github.com/pgvector/pgvector), [Chroma](https://github.com/chroma-core/chroma).

This ordering's a scope recommendation, not a measured performance ranking. The [database source notes](2026-10-22-vector-database-source-notes.md) cover deployment modes, filtering, licensing, compatibility limits, and acceptance checks.

### What extensibility has to preserve

There's already an `IndexStore` protocol, but default composition, generation artifacts, ingest paths, and some resource reads still assume SQLite. Existing generations explicitly contain `index.sqlite3`. A provider dropdown alone can't deliver interchangeable storage. [Index factory](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/src/policynim/services/index_store.py), [store contract](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/src/policynim/contracts.py), [generation implementation](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/src/policynim/services/generations.py).

Proposed design requirements:

1. Keep source snapshots, policy composition, source authority, and runtime semantics owned by PolicyNIM. Treat the vector index as a derived artifact bound to that source generation.
2. Give adapters responsibility for connection lifecycle, query translation, metadata filters, owned namespaces, capability checks, and staged publication. Establish the shared contract with SQLite and one external implementation before generalizing it.
3. Bind each index to the embedding provider/model, dimensions, metric, schema, and policy generation. Importing an existing arbitrary collection needs an explicit mapping and validation path. Equal dimensions alone don't establish compatible embeddings.
4. Preserve activation, in-flight readers, retry, rollback, and retention across remote namespaces. Validate a candidate before switching the active reference; failed publication must preserve the usable prior generation.
5. Test citation ownership, whole-policy reads, filters, refusal paths, and recovery through each real backend. Approximate search can return different rankings; measure policy relevance separately from business-rule parity.

Vector storage, source storage, runtime evidence, job state, and authentication storage serve different purposes. Offering BYO vectors should say exactly which data moves. Local vectors still use the currently configured NVIDIA embedding/retrieval paths. Private-policy onboarding needs an explicit data-flow explanation. [Provider/privacy boundary](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/docs/policy-profiles.md#provider-and-privacy-boundary).

## Proposed UI onboarding

The current beta dashboard centers account access, API-key rotation, client commands, and example workflows. It has no storage or policy-profile selection controls in the reviewed templates. [Dashboard](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/src/policynim/templates/beta/dashboard.html.j2).

The first useful outcome should be: **My selected policies are active in my chosen storage, and I can inspect a result with its source and version.**

| Step | User decision | What the UI verifies or shows |
| --- | --- | --- |
| 1. Choose deployment | Shared hosted baseline, local installation, or self-hosted server | Where policy text and credentials travel. The shared hosted route keeps its public profile; custom policies/storage initially belong to local or self-hosted installations. |
| 2. Choose policy packs | Baseline topics plus local team packs | Policy inventory, declared authority, provenance, conflicts, and a preview of exclusions/replacements. |
| 3. Choose storage | SQLite default or a supported external adapter | Backend-specific connection fields, secret references, namespace ownership, model/dimension compatibility, and permission checks. |
| 4. Build and validate | Approve the displayed configuration and provider work | Isolated candidate generation, progress, actionable failures, complete source binding, and explicit activation after validation. |
| 5. Connect the agent | Select a supported client and try a task | A policy-backed result with full selected sources, citations, generation ID, saved configuration, and recovery guidance. |

Deployment location comes before connection setup: `localhost` on a hosted server points at that server. A laptop database needs a local PolicyNIM process or deliberately configured network reachability. The shared hosted product currently accepts one operator-selected public profile; per-user private indexes would add a separate tenancy and credential-management scope. [Accepted product boundaries](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/docs/roadmap.md).

Show separate statuses for connection success, compatibility, candidate validation, and active generation. Advanced metric/index controls can stay collapsed. A connection probe mustn't overwrite an existing collection. Endpoint validation, secret handling, namespace ownership, and isolation checks belong in the server-side configuration contract shared by UI and CLI.

## Recommended work order

1. **Define the product contract and worked demonstration.** Lead with policy composition, source ownership, decisions, and evidence. Review wording against the current source-extractive behavior.
2. **Timebox a storage design spike to one engineering day.** Trace SQLite assumptions, choose the external generation/namespace lifecycle, and draft the shared acceptance fixture. The timebox produces a design decision, not a promised completed adapter.
3. **Implement and verify Qdrant support.** Exercise ingest interruption, failed activation, concurrent readers, incompatible embeddings, unauthorized namespaces, restart, rollback, and restore before calling it supported.
4. **Build the five-step onboarding flow.** Expose only backends whose contract passes. Keep local/self-hosted custom policy setup separate from the shared hosted baseline.
5. **Run the advisory Habit Hooks pilot.** Measure supported repairs, preserved behavior, false positives, time, and tokens. Decide whether its feedback earns a place in the development workflow before making it required.

Success measures should follow completed user work: first verified policy result, successful custom-pack composition, successful chosen-backend setup, recovery after restart, and useful policy decisions during a later task. Establish baselines through observed trials before setting numerical targets.

## Verification and remaining limits

This research checked current repository source and docs, live GitHub revision/issue/PR metadata, Habit Hooks sources and package metadata, and official database documentation. Candidate databases and Habit Hooks weren't executed. No adapter, UI flow, comparative benchmark, deployment, or adoption outcome was verified. Automated language checks cover the authored research files; factual and semantic review used the linked source material.
