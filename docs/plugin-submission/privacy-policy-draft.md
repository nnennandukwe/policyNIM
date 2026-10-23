# PolicyNIM privacy policy source draft

**Owner review required. This draft isn't approved, published or effective.**

This source draft describes the inspected repository behavior. Before publication,
the owner must reconcile it with the final hosting, OAuth issuer, provider contracts,
operational logging, backups and a private contact process. The owner must approve
the effective date and any legally required disclosures. Don't use this file's
repository URL as the listing's effective privacy-policy URL.

## Proposed public text

PolicyNIM retrieves engineering policies and produces source-linked guidance for
coding tasks. When you connect it to ChatGPT or another MCP client, the client sends
the tool arguments you authorize to the hosted PolicyNIM service. Send only task
details and sample evaluation data that you're authorized to share with that
service and its model providers.

### Information the service handles

Search and guidance requests can contain your policy question, task text, selected
domain and retrieval settings. PolicyNIM processes selected policy text, source
metadata, citations, compiled guidance and model responses to answer the request.
Policy-source attribution, revision and license information travel with the source
catalog and relevant results.

OAuth access verification uses the configured issuer and resource/client identity.
Authenticated requests carry identity, scopes and expiry claims. If the optional
GitHub beta portal is enabled, its account records include GitHub identity/profile
information, available email information, quota/usage records, API-key hashes and
revocation/audit records. Portal sessions use a signed cookie. The owner must state
which authentication paths are enabled on the published service.

Submitting an evaluation creates persistent job records. These include the submitted
suite and task inputs, captured policy generation, model/provider settings, accepted
authorization claims, scope/owner identity, idempotency key, limits, progress, case
artifacts, usage and report data. Job snapshots retain authorization claims rather
than the bearer credential or NVIDIA API key. Treat idempotency keys as identifiers
and keep private text out of them.

Application operation logs use correlation IDs, operation/tool names, authentication
outcome, timing and failure classes. The MCP logging path avoids request content,
tokens and raw exception payloads. Hosting, proxy, issuer and provider logs have
their own configuration and policies, which the published operator must disclose.

### Model providers and other services

The configured NVIDIA endpoints process query/task text and selected policy text
for embeddings, reranking and generated guidance or evaluation, depending on the
operation. The hosted service uses its own provider credentials. PolicyNIM MCP
credentials aren't forwarded as model-provider credentials.

The default vector index stores embeddings locally with SQLite. Operators can
configure Qdrant or PostgreSQL with pgvector to store policy embeddings and their
retrieval identifiers outside that local database. The owner must identify the
deployed backend, hosting location, access controls and retention process before
approving this policy. Optional backend support doesn't establish that the hosted
service uses an external vector store.

The MCP client receives the tool results and handles conversation data under its
own terms and privacy policy. The OAuth issuer handles sign-in and token lifecycle.
GitHub handles beta portal sign-in where enabled. The hosting operator stores the
application databases and runs the service. The final public policy must identify
the actual enabled providers and their applicable notices; repository defaults
alone don't establish their contractual retention, training or regional terms.

### Retention and deletion controls

The evaluation implementation defaults to a seven-day report retention window.
Local operator configuration can select supported limits from one minute to thirty
days. Hosted MCP submissions currently use the default retention setting. Normal
terminal jobs calculate expiry when they finish; interrupted/invalid artifact
recovery has its documented handling. Active jobs retain accepted inputs while
execution is pending. The published operator must verify the deployed settings.

Expired reports become unavailable with `result_expired`. Worker maintenance or
the operator's `eval-jobs prune` command removes expired input/report payloads and
unreferenced captured archives. Job summaries, owner identifiers, usage, hashes and
idempotency records remain. An active case identifier can remain in the summary,
so case IDs must also avoid personal or confidential text. Quota accounting can
retain permanent compact reservation tombstones to
prevent an old key from charging again. Report expiry isn't full account or identity
erasure, and cancellation follows its own execution lifecycle.

The beta portal supports key rotation/revocation and logout. Those actions affect
access. The current plugin exposes no account-deletion or immediate job-erasure
tool. The operator must provide a private process for access, correction and
deletion requests, including retained summaries, audit/accounting records, issuer
records, backups and provider-held data. The implementation doesn't establish a
universal deletion deadline across those systems.

### Contact and safeguards

Use the approved private contact route linked from the published support/privacy
page for personal-data requests. Public bug reports must omit task text, personal
details, tokens and raw reports. Security reports follow the repository's private
security-reporting route. The owner must publish a reachable private privacy contact
before this draft can become effective.

## Owner decisions before approval

| Decision | Evidence needed |
| --- | --- |
| Operator identity and effective date | Owner/legal approval of public identity and applicable disclosures. |
| Private privacy contact | Tested route with a person responsible for handling requests. |
| Hosting, issuer, NVIDIA and vector storage | Actual deployment/provider configuration and applicable contracts, regions and notices. |
| Retention and erasure | Deployed settings, worker/prune behavior, summary/accounting handling, backups and operator procedure. |
| Additional collection or uses | Review analytics, telemetry, subprocessors, training uses and required legal disclosures before making claims. |

Source evidence: [job contracts](../../src/policynim/job_types.py),
[job retention storage](../../src/policynim/storage/evaluation_jobs.py),
[evaluation lifecycle](../evaluation-jobs.md),
[hosted authorization](../hosted-authorization.md),
[beta auth](../../src/policynim/services/beta_auth.py),
[MCP logging](../../src/policynim/interfaces/mcp.py),
[vector storage](../vector-storage.md),
[NVIDIA adapters](../../src/policynim/providers/nvidia.py) and
[source grounding](../public-source-grounding.md).
