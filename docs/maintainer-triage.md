# Maintainer triage

Use [#88](https://github.com/nnennandukwe/policyNIM/issues/88) for program scope and
linked issues for acceptance criteria. Route bugs by actual failing workflow and
version, with the safe evidence described in [SUPPORT.md](../SUPPORT.md).

## Labels and topics

[labels.yml](../.github/labels.yml) and [topics.yml](../.github/topics.yml) describe
the desired repository metadata. Checking them in doesn't apply them to GitHub.
Both scripts default to the explicit `nnennandukwe/policyNIM` target; use `--repo
OWNER/REPO` to select a different repository. The target appears in text/JSON output
and doesn't depend on the current directory or ambient GitHub repository selection.

Preview offline without GitHub I/O:

```bash
uv run python scripts/sync_github_labels.py --format json
uv run python scripts/sync_github_topics.py --format json
```

Compare with current GitHub state without writing:

```bash
uv run python scripts/sync_github_labels.py --live --format json
uv run python scripts/sync_github_topics.py --live --format json
```

Read the target and planned changes before applying. An authenticated maintainer
with metadata-write access may explicitly apply the reviewed target:

```bash
uv run python scripts/sync_github_labels.py --repo nnennandukwe/policyNIM --apply --format json
uv run python scripts/sync_github_topics.py --repo nnennandukwe/policyNIM --apply --format json
```

Label sync creates or updates desired labels and preserves unrelated labels. Topic
sync adds desired topics and removes topics outside the file; inspect removals
before applying. GitHub changes aren't transactional. If a later write fails,
earlier changes may remain: rerun `--live`, inspect actual state and review the new
plan before retrying. Don't claim the original operation rolled back.

Missing authentication, invalid JSON and CLI failures produce nonzero exits.
Subprocess calls have a 30-second timeout. Check `gh auth status`, access to the
named repository and network availability, then rerun the live preview. An offline
preview can't determine which labels already exist. No script logs configured tokens.

## Route issues

Use one `type/*`, relevant `surface/*` labels and `status/needs-triage` until evidence
supports a route. Add priority after understanding impact:

- `priority/p0`: credential/private-data exposure, release integrity or broken installs.
- `priority/p1`: blocked setup, core CLI/MCP behavior or release verification.
- `priority/p2`: docs, recovery guidance and other nonblocking improvements.

Use `status/needs-repro` for missing reproducible evidence and
`status/blocked-external` when an identified provider, client, Docker, Railway,
GitHub or packaging dependency prevents acceptance. `needs/live-check` and
`needs/launch-evidence` indicate missing proof; they don't establish a release state.

Security details belong in the [private reporting route](../SECURITY.md), never a
public issue body. Don't ask reporters to post credentials or raw private artifacts.
A sanitized support bundle still needs human inspection before sharing.

## Ownership and update policy

[CODEOWNERS](../.github/CODEOWNERS) names the maintainer; it doesn't configure branch
protection. Dependabot opens bounded weekly uv and GitHub Actions update PRs using
[the checked-in configuration](../.github/dependabot.yml). GitHub documents these
[supported ecosystems](https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories).
Require applicable offline gates and review before merge. Neither the bot nor a
metadata label proves code-owner approval or package/production readiness.

Keep rollout proof with [#104](https://github.com/nnennandukwe/policyNIM/issues/104):
artifact checksums, installed behavior, supported clients and observed deployment
must identify their actual versions. Don't cite unmerged scripts or generated
files as if they were available in the repository.
