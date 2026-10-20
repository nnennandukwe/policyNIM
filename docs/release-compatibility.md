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
| Linux wheel | Ubuntu 24.04, Python 3.11/3.12 | Candidate package jobs pass at `5436bf44ed284d97b79179a1797a2fd7f25e32e4`. |
| Windows wheel | Windows Server 2022, Python 3.11/3.12 | The same candidate passes package jobs after explicit UTF-8 decoding. |
| Docker | `Dockerfile` and `Dockerfile.railway` | Offline build/import/startup checks pass without provider credentials. |

SDK checks also call `policy_search` with an invalid argument and require rejection
without provider work. These checks don't prove that a coding client can complete a
user workflow. The package verifier's current two-tool checks cover
`policy_search` and `policy_preflight`; #100 and #101 own the additional tools,
versioned resources and durable-job operations. Those additions need updated
installed-client checks on the integrated release.

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
| `nvidia-eval-launcher` | `nemo-evaluator-launcher==0.2.4`, `nvidia-nat[eval]==1.6.0` | Locked install and offline tests; no independent launcher workflow or live benchmark claim. |

The complete extra set installs together on macOS with Python 3.11.13: 259
installed packages pass `uv pip check`, and 1,694 offline tests pass at the
engineering candidate `e443de6999671e59fade05cb7e525088b80b189d`, before
its subsequent policy-text review changes.
This doesn't establish extra compatibility on every operating system.
`httpx==0.27.2` and `httpx2==2.6.0` remain explicit separate distributions;
the combined locked install passes without replacing either pin.

## Final release gate

Retain the CI `package-evidence-<os>-<python>` artifacts and separate real-client,
live-provider, hosted-state and rollback receipts. Repeat affected checks after
integration changes. A passing candidate matrix doesn't complete #104 while
client acceptance, production identity or rollback evidence is missing.
