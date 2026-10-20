## Problem and behavior

Link the issue and explain the caller-visible result. Keep the PR scoped to one
reviewable change. Name affected CLI, MCP, storage, policy or release surfaces.

## Verification

State the exact tested commit, commands and results. Explain failed/skipped checks.

- [ ] Locked dependencies agree with the manifest.
- [ ] Ruff, Pyright and the offline suite pass.
- [ ] Changed public behavior has relevant success, rejection and recovery checks.
- [ ] Any package/client/live/deployment evidence names its own revision and scope.
- [ ] Final review findings and coverage limits are disclosed.

See [CONTRIBUTING.md](https://github.com/nnennandukwe/policyNIM/blob/main/CONTRIBUTING.md) and
[the release guide](https://github.com/nnennandukwe/policyNIM/blob/main/docs/release.md) for applicable commands. A source suite isn't
proof of a package install or production rollout.

## State and recovery

Describe compatibility, migration, interruption, cleanup or rollback when affected.
List external dependencies and remaining acceptance checks without marking them done.

## Sensitive data

- [ ] No credentials, private policy/task content or raw private artifacts are included.
- [ ] Diagnostic examples have been inspected after redaction.
- [ ] Live calls or external writes, if needed, were explicitly authorized and identified.
