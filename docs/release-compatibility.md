# Release compatibility and evidence

This matrix records the candidate's tested interfaces. It doesn't declare a new
package release or production rollout. Final release evidence must name the exact
application commit, dependency-lock digest, policy generation, artifact checksums,
client version and observed deployment identity.

## Server and transport baseline

| Component | Pinned combination | Evidence and limit |
| --- | --- | --- |
| Python | 3.11 and 3.12 | Offline CI and fresh wheel checks run on both versions. |
| MCP server SDK | `mcp==2.2.0` | Resolved from `uv.lock`; the installed verifier records the actual version. |
| Modern protocol | `2026-07-28` | Actual installed SDK clients negotiate stdio and Streamable HTTP. |
| Legacy protocol | `2025-11-25` | Actual installed SDK clients negotiate the same two transports in legacy mode. |
| Linux wheel | Ubuntu 24.04, Python 3.11/3.12 | Require passing package jobs on the final revision; retain their per-commit CI receipts. |
| Windows wheel | Windows Server 2022, Python 3.11/3.12 | Require the same final-revision package jobs with explicit UTF-8 decoding. |
| Docker | `Dockerfile` and `Dockerfile.railway` | Require offline build/import/startup checks without provider credentials. |

SDK checks call `policy_search` with an invalid `top_k` and require the range
validation message without provider work. Installed stdio checks discover four
guidance tools: `policy_search`, `policy_preflight`, `policy_compile` and
`policy_preflight_evidence`. Authenticated HTTP checks discover those tools plus
`eval_submit`, `eval_inspect`, `eval_cancel` and `eval_result`; they also require
missing-token rejection. Resource templates, prompts and scope authorization
have separate integration tests. These SDK checks don't
prove that a coding client can complete a user workflow or that provider-backed
jobs succeed in production.

## Coding clients and authorization

| Client or mode | Version or configuration | Current acceptance state |
| --- | --- | --- |
| Codex CLI | `0.155.1` observed locally | Real tool/resource/error/cancellation acceptance is pending under #104. |
| Claude Code | `2.1.283` observed locally | Real tool/resource/error/cancellation acceptance is pending under #104. |
| Local stdio | Process launch, local provider credentials | SDK transport checks pass; no HTTP bearer authorization applies. |
| Authenticated HTTP | Configured bearer or beta keys | Package checks cover successful discovery and missing-token rejection. Broader operation and legacy-key transition acceptance remains separate. |
| OAuth HTTP | Configurable issuer/introspection foundation | #97 remains open. Production has no selected issuer or registered client flow; Keycloak remains a documented option. |
| Auth-disabled HTTP | Trusted local development | It has no authenticated principal. Production must require authentication; principal quota claims don't apply to this mode. |

Record the actual coding-client version used in each acceptance run. Don't
replace a failed or skipped client run with an SDK discovery result. OAuth
acceptance requires a real configured issuer and registered client; it can't be
inferred from bearer-key checks or mocked introspection.

## Optional evaluation dependencies

| Extra | Exact project pins | Verified scope |
| --- | --- | --- |
| `nvidia-guardrails` | `nemoguardrails[nvidia]==0.21.0` | Locked install and offline tests; internal adapter only. No live rail-quality claim. |
| `nvidia-eval` | `nemo-evaluator==0.2.5`, `nvidia-nat-eval==1.6.0`, `nvidia-simple-evals==26.3` | Locked install and offline tests. Package-gated conformance adapters still use the configured NVIDIA judge endpoint. |
| External NeMo launcher | Separate upstream tool environment | Not bundled, served, or verified as a PolicyNIM runtime backend. |

Verify the combined extra set with a fresh locked install, `uv pip check` and the
offline suite on the final revision. Retain the platform, Python version, lock
digest and package versions with that result. An earlier candidate's passing
result can't satisfy this gate. Cross-platform extra compatibility needs its own
receipts; installing the packages doesn't prove a live launcher workflow.
`httpx==0.27.2` and `httpx2==2.12.0` remain explicit separate distributions in the
reviewed lockfile. Don't silently replace either pin during release verification.

## Final release gate

Retain the CI `package-evidence-<os>-<python>` artifacts and separate real-client,
live-provider, hosted-state and rollback receipts. Repeat affected checks after
integration changes. A passing candidate matrix doesn't complete #104 while
client acceptance, production identity or rollback evidence is missing.
