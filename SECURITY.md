# Security policy

PolicyNIM handles API credentials, hosted identities, policy sources and evaluation
artifacts. Report suspected disclosure, authorization bypass, unsafe filesystem
access or release-integrity issues privately.

## Report a vulnerability

Use GitHub's [private vulnerability report form](https://github.com/nnennandukwe/policyNIM/security/advisories/new).
Private vulnerability reporting is enabled for this repository. Don't put exploit
details or secrets in public issues. If you can't access the private form, open a
public request for a private contact route without vulnerability details.

Include the affected tag/commit, install channel, operating system, transport and
smallest safe reproduction. Describe possible impact without attaching real tokens,
private policy content, task text or raw evaluation artifacts. Redact credentials
and personal paths before sharing logs. Preserve original evidence privately.

## Fix and release scope

Fixes target the maintained `main` branch. Applicable release work is tracked in
[#104](https://github.com/nnennandukwe/policyNIM/issues/104); identify the artifact
and tag you used so maintainers can assess affected versions. This policy doesn't
promise response deadlines or long-term support for historical branches.

Security reports, review findings, fixes, published artifacts and deployment are
separate evidence. A merged fix alone doesn't show that production or an installed
artifact includes it.

## Maintainer checks

Review authentication, principal scope, accepted-operation accounting, content
redaction, persistence and cleanup at their actual boundaries. Keep secrets out of
public diagnostics. Run the offline checks in [CONTRIBUTING.md](CONTRIBUTING.md)
and use explicit opt-in verification when live services are required. Preserve
pinned dependencies and GitHub Actions, and examine update PRs before merging.
