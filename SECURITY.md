# Security Policy

PolicyNIM handles local configuration, API keys, hosted MCP bearer tokens, and
runtime evidence artifacts. Please report security issues privately instead of
opening a public issue.

## Reporting A Vulnerability

Email the maintainer listed in [pyproject.toml](pyproject.toml) or open a
private GitHub security advisory if repository access allows it.

Include:

- affected version, commit, install channel, and operating system
- whether the issue affects CLI, MCP `stdio`, hosted `streamable-http`, the
  hosted beta portal, release installers, or runtime evidence
- minimal reproduction steps without secrets
- whether credentials, policy content, runtime evidence, or hosted beta tokens
  may have been exposed

Do not include real `NVIDIA_API_KEY` values, hosted bearer tokens, generated beta
tokens, or private policy documents. Redact secrets before attaching logs.

## Supported Versions

PolicyNIM is currently pre-1.0. Security fixes target `main` and the latest
GitHub release unless a release note says otherwise.

## Security-Relevant Local Checks

For local setup diagnostics, run:

```bash
policynim doctor --format json
```

`doctor` reports config and artifact paths without calling NVIDIA-hosted APIs or
printing configured secret values.

## Supply Chain Stewardship

PolicyNIM keeps sensitive path ownership in [.github/CODEOWNERS](.github/CODEOWNERS)
and bounded weekly dependency update checks in
[.github/dependabot.yml](.github/dependabot.yml). Dependency, installer,
release, and GitHub Actions updates still need the offline gates and maintainer
review before release; update bots do not replace security review.
