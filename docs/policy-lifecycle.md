# How PolicyNIM applies a team's policy decisions

PolicyNIM gives coding agents reviewed engineering policies with preserved sources
and versions. A team controls the effective policy set, reviews changes to that set,
and can inspect runtime decisions and execution evidence for supported actions.

## From source documents to a reviewed policy set

A policy pack's manifest declares its members and provenance. A pinned profile
combines selected packs, including local team policies. Exclusions and replacements
name the exact target digest and a review reason. A changed target invalidates the
old decision until someone reviews it again. Declared conflicts and contradictory
runtime effects block composition; filesystem order doesn't choose a winner.

Those rules let teams answer operational questions: Which baseline do we use?
What did we replace? Who needs to review a changed source? Which local decision
explains the effective rule? Reasons and revisions are inspectable today; a complete
organizational ownership/approval system would need a separate contract.

## From policy selection to source authority

Retrieval and reranking find candidates for an engineering task. The compiler and
preflight select sources and return complete policy bodies with canonical owner
metadata and citations. Required wording, recommendations, conditions, examples,
and exceptions travel together. Legacy fields named `required_steps` and
`plan_steps` contain these source bodies; advisory fields stay empty.

Services validate source ownership and exact quotation fidelity. Missing or
incompatible evidence produces a refusal. Developers still review whether the
selected policy applies and whether its source deserves trust. See
[output semantics](source-extractive-guidance.md).

## From source changes to a consistent generation

Explicit ingestion captures sources, builds vectors, compiles declared runtime
rules, and seals a complete generation. Its identity binds those artifacts to
one model/provider space and source snapshot. Reader leases keep an operation on
the same generation during updates. Activation changes one pointer after validation;
rollback selects a retained complete generation.

When external vector storage is selected, approved ingestion publishes and verifies
its derived replica before activation. Moving vector search between supported
backends preserves the policy sources and decision semantics. Database access and
recovery behavior are part of the workflow a user needs to understand.

## From a supported action to an inspectable outcome

The local runtime wrapper checks shell commands, file paths, and HTTP hosts against
explicit `runtime_rules` matchers. A matching block rule takes precedence over
confirmation. An action with no matching rule receives allow after index readiness
checks. Confirmation-required actions stop when confirmation is refused or
unavailable. Initial decision evidence is persisted before execution, followed by
the terminal result.

This enforcement covers actions submitted through the wrapper and the matcher
types it supports. Policy prose doesn't automatically become an executable check.
See [runtime and evidence workflows](workflows.md).

## Worked example: a migration policy changes

The maintained `DATA-MIGRATE-001` policy asks developers to declare compatibility,
interruption, retry/recovery behavior, and verify recovery through the real reader.
Its boundaries explain that engine behavior and workload-specific targets need
their own decisions.

1. Compose that baseline with a team-owned pack using `policynim profile init`.
2. Review any replacement against the target digest and record its reason.
3. Ingest the profile and inspect a migration task's complete selected sources.
4. Test a separately authored runtime matcher through the local wrapper and
   inspect its decision, confirmation behavior, and execution evidence.
5. Update the baseline source. The old replacement target digest now blocks the
   profile update until renewed review. Publish only after that review completes.

This example describes a supported workflow to exercise with your own fixtures.
The policy text defines the engineering question; the explicit matcher defines
what the runtime can check. Keep the demonstration's result tied to the exact
policy generation and code revision used.
