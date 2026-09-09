# Hosted NVIDIA provider recovery (#107)

This repair targets ingestion and the two existing guidance tools. It does not
implement the policy-generation publication protocol tracked in #93.

## Diagnosis and supported configuration

Railway preview `46923eec-0b7d-4286-9c7a-f866011b0b04`, built from Foundation
`c077a6bce4e5252e0fc28e677a88223c85c7231a`, failed during Docker ingestion at
`2026-09-09T19:02:27.636Z` with an NVIDIA embedding HTTP 410. It never launched
the server. NVIDIA's catalog marks the previous
[embedding](https://build.nvidia.com/nvidia/llama-nemotron-embed-1b-v2),
[reranking](https://build.nvidia.com/nvidia/llama-nemotron-rerank-1b-v2), and
[chat](https://build.nvidia.com/nvidia/llama-3_3-nemotron-super-49b-v1_5)
hosted endpoints deprecated. Endpoint retirement is the leading supported
explanation. Historical wire capture and replacement access have not been
verified; credentials and dependency behavior are not conclusively excluded.

| Operation | Default model | Official request contract |
| --- | --- | --- |
| Embedding | `nvidia/nemotron-3-embed-1b` | [Reference](https://docs.api.nvidia.com/nim/re/reference/nvidia-nemotron-3-embed-1b-infer): `/v1/embeddings`, `input_type=passage` or `query`, `encoding_format=float`, `truncate=NONE`. |
| Reranking | `nvidia/llama-nemotron-rerank-vl-1b-v2` | [Reference](https://docs.api.nvidia.com/nim/reference/nvidia-llama-nemotron-rerank-vl-1b-v2-infer): `/v1/retrieval/{model}/reranking`, text query and passages, `truncate=END`. |
| Chat | `nvidia/nemotron-3-super-120b-a12b` | [Reference](https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b/build): OpenAI-compatible chat, temperature `1`, top-p `0.95`, `chat_template_kwargs.enable_thinking=false`. |

Custom model overrides remain supported. Credentialed provider endpoints require
HTTPS; cleartext endpoints are rejected before constructing clients. The selected chat model alone uses
these sampling and reasoning options; unrelated models retain temperature `0`
and top-p `1`. Citation validation remains mandatory. No model fallback or silent
embedding truncation is applied. HTTP 410 is classified as `endpoint_unavailable`
and stops immediately, including when retry settings are nonzero. Public errors
identify the operation and configuration names without upstream response bodies.

## Build and runtime configuration

Both Dockerfiles declare these nonsecret arguments globally and in the builder
and runtime stages, and carry them into the runtime image's defaults:

| Variable / build argument | Default |
| --- | --- |
| `POLICYNIM_NVIDIA_EMBED_MODEL` | `nvidia/nemotron-3-embed-1b` |
| `POLICYNIM_NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` |
| `POLICYNIM_EMBED_BATCH_SIZE` | `32` |
| `POLICYNIM_NVIDIA_TIMEOUT_SECONDS` | `30.0` |
| `POLICYNIM_NVIDIA_MAX_RETRIES` | `2` |

The standard Dockerfile continues to use a BuildKit secret. Railway's Dockerfile
continues to use its existing key build argument because the platform rejects
secret mounts. Never print key values or pass keys in command-line arguments.
[Railway requires explicit ARG declarations](https://docs.railway.com/builds/dockerfiles)
for service variables used by a Docker build. Existing Railway model variables
must be changed explicitly: code defaults do not override them. Before this fix,
only the key was forwarded, so the ingestion build used code defaults even when
runtime model variables were present.

Reranking and chat run at runtime. Set `POLICYNIM_NVIDIA_RERANK_MODEL`,
`POLICYNIM_NVIDIA_CHAT_MODEL`, and `POLICYNIM_NVIDIA_RETRIEVAL_BASE_URL` there.
A runtime embedding override that differs from the baked identity fails readiness.
`uv sync --frozen` plus `uv run --no-sync` keeps ingestion on the locked environment.
The image includes both the database and `/app/data/runtime/runtime_rules.json`.
Build failures remain fatal; missing, empty, unknown, or incompatible indexes
cannot satisfy startup readiness.

## Rebuild into a separate candidate

Preserve the original corpus, index, runtime rules, and configuration. Do not edit
identity metadata or reuse either old output path. A schema-1 index remains
inspectable with `dump-index`, but cannot be queried or implicitly upgraded.
All schema-2 indexes record provider, model, credential-free endpoint, observed
vector dimension, build ID, and completion status. Equal dimensions do not make
different models compatible. Endpoint identities must use HTTPS without URL
credentials, query parameters, or fragments.

After approval for live provider usage, run this from a locked source checkout
with `NVIDIA_API_KEY` already supplied securely. Set `POLICYNIM_CORPUS_DIR` to the
preserved source corpus; do not regenerate sources from a partial index. This
example keeps configuration changes inside a subshell and leaves the candidate
inactive:

```bash
(
  mkdir -p data
  recovery_dir=$(mktemp -d "$PWD/data/provider-recovery.XXXXXX")
  export POLICYNIM_CORPUS_DIR="$PWD/policies"
  export POLICYNIM_INDEX_DB_PATH="$recovery_dir/index.sqlite3"
  export POLICYNIM_RUNTIME_RULES_ARTIFACT_PATH="$recovery_dir/runtime_rules.json"
  export POLICYNIM_NVIDIA_EMBED_MODEL=nvidia/nemotron-3-embed-1b
  export POLICYNIM_NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
  export POLICYNIM_NVIDIA_MAX_RETRIES=0
  uv run --no-sync policynim ingest &&
    uv run --no-sync policynim doctor --format json &&
    uv run --no-sync policynim dump-index --count-only
)
```

Inspect the JSON `index.identity` and `index.compatible` fields; `doctor` reports
action-required in its JSON even when the diagnostic command exits successfully.
Check source inventory against indexed documents/chunks and confirm runtime rules
exist. The shipped corpus at the Foundation baseline has 9 documents and 45
chunks; verify the inventory for the exact source revision being deployed.
Doctor and health never contact NVIDIA. Runtime decisions also reject incomplete
or incompatible indexes before they can authorize actions. On any failure, stop; retain the candidate
for inspection and choose fresh paths for another attempt. An incomplete database
must not be activated. Cleanup warnings identify owned staging-file cleanup;
a warning after successful completion does not mean publication was rolled back.
Corrupt or unreadable existing indexes produce the same sanitized, separate-path
recovery guidance; ingestion never replaces them implicitly.

For a local restart, an approved operator can select the validated candidate's
index and rules paths with the matching provider configuration.

For hosted containers, the candidate is a **new image** built from the complete
preserved corpus in its build context. Both Dockerfiles ingest that corpus inside
the isolated builder and package `/app/data/index.sqlite3` and
`/app/data/runtime/runtime_rules.json`. Inspect that image's identity, inventory,
and rules before approving its deployment with matching runtime provider settings.
Do not set Railway paths to the local shell's `recovery_dir`; those files are not
inside the image. This workflow rebuilds the hosted candidate in the image and
does not introduce external artifact publication or policy generations.

Retain compatible application/configuration/index combinations for rollback. Preserving
an old index whose provider endpoint is deprecated does not prove it remains an
operational rollback option.

## Transaction contract

For a model migration, canonical state is the original source corpus, index,
runtime rules, and operator configuration. The migration owns only a new,
explicitly selected candidate destination and its uniquely named staging files.
The old paths must not be selected as migration outputs.

| State | Transition | Durable result |
| --- | --- | --- |
| Observed | Validate settings and destinations | Existing incompatible/unknown indexes are rejected without provider calls. |
| Preparing | Embed all preserved source documents | Original installation is untouched. |
| Staged | Write vectors and identity into one temporary database | Production inspection must accept identity and dimensions. |
| Published, incomplete | Publish database with an incomplete marker | Retrieval and readiness reject the candidate. |
| Complete | Finalize runtime rules, then mark the matching database build complete | Candidate can be inspected and explicitly activated. |
| Aborted | Failure before database publication | Original installation remains unchanged; remove only owned staging files. |
| Recoverable | Failure after database publication | Incomplete candidate remains blocked; preserve it and retry into a new destination. |
| Cleanup warning | Failure removing owned temporary files after publication | Report the committed state accurately; do not delete published data. |

The database publication operation is an atomic no-clobber link for a new
destination and a staged-file replacement for a compatible existing index.
Migration requires a fresh destination. A concurrent creator of that destination
must win rather than be overwritten. Existing-index replacement is an offline,
single-publisher operation; concurrent activation/publication is reserved for #93.
No sequence of separate index/rules writes is claimed to be an atomic generation.

Invariants:

- Never infer a persisted model from current settings or accept equal vector
  dimensions as proof of model compatibility.
- Store identity, dimensions, build identifier, and completion status together
  with vectors. Validate identity again on the connection used for retrieval.
- Never query an incomplete candidate or complete a different build by mistake.
- Complete only the physical database published by the active ingestion. Copied
  build metadata cannot resume completion after path substitution or process restart.
- Preserve original sources and old index/rules during migration and every
  migration failure. Reject a pre-existing rules output for a fresh migration.
- Never rebuild an incompatible index as a side effect of health, doctor, or
  startup. Diagnostics use read-only database connections.
- Clean up only uniquely owned staging files. Do not erase a concurrent writer's
  destination or its sidecars.
- Reject destinations with existing WAL, shared-memory, or rollback-journal
  sidecars, including orphaned files or links. Preserve them and choose fresh paths.

Hosted server startup retains Foundation's automatic ingestion for a truly absent
index; that path can contact NVIDIA and belongs inside an approved deployment.
It refuses existing empty, corrupt, incomplete, or incompatible files. The health
inspector and `doctor` themselves never ingest or contact providers.

## Fault and lifecycle evidence required

| Operation / boundary | Injection | Required outcome |
| --- | --- | --- |
| Inspect destination / identity | Unreadable, legacy, malformed, different model | No provider call or destructive write; explicit rebuild guidance. |
| Create parent / temporary file | Permission or creation failure | Original untouched; no candidate reported ready. |
| Load sources / embed | Parse failure, HTTP 410, invalid vectors | Original untouched; no partial candidate activated. |
| Write database / commit | Insert, commit, close or checkpoint failure | No published candidate; owned temp cleanup only. |
| Inspect staged database | Invalid identity/dimensions | Publication refused. |
| Publish new destination | Concurrent create or link failure | Preserve winning destination and original installation. |
| Replace compatible destination | Identity changes during preparation | Refuse stale replacement. |
| Finalize rules | Rename failure or concurrent create | Candidate remains incomplete and unready. |
| Complete candidate | Mismatched build identifier / update failure | Fail closed; never complete a different build. |
| Cleanup | Unlink failure | Preserve original error or report post-commit cleanup warning. |
| Interruption | Before publication / before completion | Old installation preserved; incomplete candidate rejected after reopening. |
| Retry / inspect | Reopen through actual store, health, search, and doctor | Same compatibility and completion verdict across consumers. |

The tests use real temporary SQLite databases, mocked provider transports, and
injected filesystem failures. `test_index_identity.py`, `test_ingestion_recovery.py`,
`test_provider_recovery.py`, and the CLI/Docker contract tests cover these boundaries. OS power-loss durability and concurrent live
publication of existing indexes remain outside this issue; migrate offline into
new paths and activate only after validation.

## Verification and release gates

Default CI remains offline. Use the contributor guide's locked test commands for
both Python 3.11 and 3.12, Ruff, formatting, Pyright, and `uv lock --check`. Bind
results and Qodo review evidence to the exact revision. Local-review coverage
limitations and historical findings must be reported separately from PR review.
Live evaluation orchestration also isolates both its temporary database and runtime
rules; it must never read or overwrite an installation's rules artifact.

Live verification needs explicit approval, zero provider retries, and a stop on
the first failure. Record the commit, lock digest, configuration names/public
values, corpus counts, image identity, deployment ID, and each outcome without
credentials or private responses. The bounded sequence is:

1. One passage embedding, query embedding, rerank, and grounded-generation probe.
2. Complete isolated ingestion and image build from the preserved corpus.
3. One preview deployment with the new model settings and its actual public
   hostname in `POLICYNIM_MCP_PUBLIC_BASE_URL` (never copy a staging hostname).
4. Separately verify `/healthz`, operator-inspected identity/inventory, authenticated
   MCP discovery, and one successful `policy_search` and `policy_preflight` call.
   Validate returned citations against the preserved source paths and line spans.

A successful build or health response does not prove either tool works. If access
or provider availability blocks any step, record hosted verification as blocked.

Keep this PR stacked on Foundation #105 until an authorized operator merges #105.
Do not merge automatically. Assume production autodeploy is enabled until verified;
coordinate approved deployment controls to prevent an intermediate Foundation-only
rollout. Then rebase only #107's commits onto updated main, retarget the PR, refresh
both offline verification and Qodo review at the new head, and deploy the final
verified combination. This supplies evidence to #104; it does not complete that
broader hosted-release program or #88. Inventory expansion (#90), atomic generation
publication (#93), and provider pooling/lifecycle redesign (#99) remain separate.

The approved live recovery probe observed the replacement chat model citing a
policy ID in place of a full chunk ID. Generation and compilation requests now
supply the explicit allowed chunk IDs and distinguish them from policy metadata.
Citation validation remains strict: a policy ID is never silently expanded or
accepted as a chunk citation. Live acceptance must verify this contract separately
from endpoint access.

Railway still supplies the ingestion key through its supported build ARG. The
ingestion command reads that inherited environment value without inline expansion,
so the key is not embedded in the displayed RUN command. This does not turn build
ARGs into BuildKit secrets: restrict access to Railway build metadata and rotate
any key previously rendered in legacy build logs. The generic Dockerfile continues
to use a BuildKit secret.
