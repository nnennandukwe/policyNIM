# PolicyNIM Maintainer Triage

Use this guide to route public issues and pull requests without losing the
evidence trail that makes PolicyNIM trustworthy as an MCP and CLI verification
tool.

## Label Source Of Truth

The repository label taxonomy lives in [../.github/labels.yml](../.github/labels.yml).
Preview the GitHub label sync plan before opening broad public triage:

```bash
uv run python scripts/sync_github_labels.py --format json
```

Compare the checked-in taxonomy against live GitHub labels without changing
anything:

```bash
uv run python scripts/sync_github_labels.py --live --format json
```

Apply the taxonomy only from an authenticated maintainer session with label
write access:

```bash
gh auth status
uv run python scripts/sync_github_labels.py --apply --format json
```

If `gh` is missing or not authenticated, the sync command fails without a
traceback and tells the maintainer to install GitHub CLI, run `gh auth status`,
rerun `--live` to inspect the GitHub delta, and use `--apply` only when ready
to mutate labels.

Do not invent one-off labels when a listed label already fits.

Start every new public issue with:

- one `type/*` label
- one or more `surface/*` labels
- one `priority/*` label after the maintainer understands blast radius
- `status/needs-triage` until the report has enough evidence to route

Use `needs/launch-evidence` when an issue or PR claims public launch readiness
but still needs strict public launch evidence, such as GitHub artifact proof,
artifact attestation verification, PyPI trusted-publishing evidence, hosted MCP
domain proof, Hosted Beta Smoke output, applied labels/topics, or a real MCP
client-session record.

Use `type/launch` with
[../.github/ISSUE_TEMPLATE/public_launch_evidence.yml](../.github/ISSUE_TEMPLATE/public_launch_evidence.yml)
for public launch evidence reviews. The Public launch evidence form asks for the
strict public readiness result, the generated launch issue, attached evidence
records, and any remaining external proof so launch claims stay reviewable
without mixing them into bug or feature reports.

## Topic Source Of Truth

The repository discoverability topics live in
[../.github/topics.yml](../.github/topics.yml). Preview the topic sync plan
before broad public promotion:

```bash
uv run python scripts/sync_github_topics.py --format json
```

Compare the checked-in taxonomy against live GitHub topics without changing
anything:

```bash
uv run python scripts/sync_github_topics.py --live --format json
```

Apply topics only from an authenticated maintainer session with repository
metadata write access:

```bash
gh auth status
uv run python scripts/sync_github_topics.py --apply --format json
```

If `gh` is missing or not authenticated, the sync command fails without a
traceback and tells the maintainer to install GitHub CLI, run `gh auth status`,
rerun `--live` to inspect the GitHub delta, and use `--apply` only when ready
to mutate topics.

Keep topics aligned with README positioning and PyPI package keywords. Do not
add hosted-health or public-ready topics until strict public launch evidence
proves those claims.

## Ownership And Dependency Updates

The review ownership map lives in [../.github/CODEOWNERS](../.github/CODEOWNERS).
Use `needs/codeowner-review` when a PR touches release automation, installers,
security reporting, public MCP surfaces, or dependency-update configuration and
the listed owner has not reviewed it yet.

The dependency update policy lives in
[../.github/dependabot.yml](../.github/dependabot.yml). Dependabot is allowed to
open bounded weekly PRs for the `uv` dependency graph and GitHub Actions. Treat
those PRs as normal code changes: require offline CI, code-owner review when
the changed path is owned, and `uv run python scripts/release_check.py` before
merging updates that affect install, release, or workflow behavior.

## Evidence First

For bugs, ask for:

- `policynim support-bundle`
- the bundle's `first_run` target summary when the report involves hosted MCP,
  local CLI, or local MCP setup
- exact install channel or source checkout command
- exact CLI command, MCP client command, hosted URL shape, or release artifact
- whether live NVIDIA, hosted MCP, Docker, Railway, or PyPI was involved

For local MCP stdio issues, prefer:

```bash
policynim support-bundle --include-mcp-smoke
policynim mcp-config --client codex --format json
```

Support bundles redact local path prefixes by default. Ask for exact paths only
in private maintainer triage, using:

```bash
policynim support-bundle --include-local-paths
```

For source checkouts, ask reporters to use `uv run` for both commands and add
`--repo-root /ABS/PATH/TO/policyNIM` when the checkout is not auto-detected.

For hosted MCP client setup issues, ask for redacted generated config:

```bash
policynim mcp-config --target hosted-http --client codex --hosted-url 'https://<host>/mcp' --format json
```

If `hosted_url_placeholder` is `true`, route the report to setup guidance first:
the client is still configured with an example or placeholder URL, not the
deployed `/mcp` endpoint.

## Priority Rules

Use `priority/p0` for:

- private data, API key, bearer token, hosted beta token, or policy-content leaks
- release artifact, checksum, installer, or `RELEASE_MANIFEST.json` integrity
  failures
- install paths that fail before `policynim --help`, `doctor`, or
  `support-bundle` can run

Use `priority/p1` for:

- first-run setup regressions in `init`, `doctor`, `support-bundle`,
  `mcp-smoke`, or `mcp-config`
- local or hosted MCP regressions that block `policy_preflight` or
  `policy_search`
- CI or release gate drift that could ship unverified artifacts

Use `priority/p2` for:

- docs drift, unclear recovery guidance, and non-blocking UX issues
- feature requests that improve verification but do not block current workflows

## Surface Routing

- CLI command, flag, help, JSON, or exit-code issues: `surface/cli`
- Local MCP launch, generated config, or stdio client behavior:
  `surface/mcp-stdio`
- Hosted MCP, beta portal, auth, `/healthz`, or Railway deployment:
  `surface/hosted-mcp`
- Install scripts, PyPI, release assets, checksums, or manifests:
  `surface/install-release`
- Runtime decision, execution, evidence, or audit logs:
  `surface/runtime-evidence`
- GitHub Actions, release automation, markers, Ruff, Pyright, or tests:
  `surface/ci`
- README, examples, support docs, roadmap, or maintainer policy:
  `surface/docs`

## Response Expectations

Within maintainer capacity:

- P0: acknowledge quickly, move security-sensitive details to private advisory
  handling, and avoid asking reporters to paste secrets publicly
- P1: request missing deterministic evidence or reproduce from the supplied
  commands before accepting
- P2: keep scope narrow and ask for verification evidence before implementation

When evidence is incomplete, use `status/needs-repro` and ask for the smallest
command sequence that reproduces the behavior. When progress depends on PyPI,
Railway, Docker, NVIDIA, GitHub Actions state, or another external service, use
`status/blocked-external` and name the external dependency in the issue.
For public launch claims, also use `needs/launch-evidence` until
`scripts/release_check.py --strict-public --external-evidence-file
docs/launch-evidence.json` returns `public_ready`.
