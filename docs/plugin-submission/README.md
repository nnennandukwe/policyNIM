# PolicyNIM plugin submission handoff

Start with the [package source](../../packaging/openai-plugin/README.md). It builds
a reproducible source archive for PolicyNIM, a service that retrieves engineering
policy sources and produces grounded guidance before an AI coding agent changes
code. Authenticated evaluation tools add bounded, durable jobs and retained reports.
The Model Context Protocol (MCP) lets AI clients discover and call those tools.

The implementation base is main `0956cca74a7d83091839a03b0d4d1838d54b825f`, runtime
version 0.2.1. The independent plugin package starts at 0.1.0. This handoff prepares
submission materials for owner review. The submission maintainer owns upload,
attestations and publication. Production OAuth provisioning remains tracked in
[issue #97](https://github.com/nnennandukwe/policyNIM/issues/97). Nothing in these
files records a live directory test or submission.

## Remaining release values

The local release-input JSON must contain all six values below. Credentials, client
secrets and reviewer instructions aren't accepted fields. The endpoint and public
policy URLs must describe the final deployment; use the actual recording URL only
after recording and access verification.

| Input key | Required value or proposed existing source |
| --- | --- |
| `mcp_endpoint` | Final stable public HTTPS MCP endpoint, reconciled with the OAuth resource audience. |
| `websiteURL` | Owner-approved product URL; the public [repository](https://github.com/nnennandukwe/policyNIM) is an existing option. |
| `supportURL` | Owner-approved support page; [repository support](https://github.com/nnennandukwe/policyNIM/blob/main/SUPPORT.md) is an existing option. |
| `privacyPolicyURL` | Published, owner-approved policy based on [the privacy source draft](privacy-policy-draft.md). |
| `termsOfServiceURL` | Published, owner-approved terms based on [the service terms source draft](terms-draft.md). |
| `demo_recording_url` | Reviewer-accessible walkthrough that demonstrates actual test outcomes. |

OAuth client ID, issuer URL, authorization/token endpoints, requested scopes,
registered callback URI and client-authentication method must also be finalized in
the issuer/dashboard integration. They aren't ZIP secrets or invented manifest
fields. Configure the dashboard's supported OAuth setup using the verified values
from the implementation tracked in #97. Never place a client secret or access token
in `mcp.json`.

## Owner and integration gates

1. Approve the privacy/terms text, service operator identity, private contact route,
   hosting/issuer/provider details, retention/deletion process and brand rights.
   The existing mark contains an NVIDIA logo. Confirm permission or provide an
   approved replacement before directory submission.
2. Complete production OAuth and native ChatGPT linking through the work in #97.
   Verify the deployed revision, public protected-resource metadata and all eight
   discovered tools, including cancellation's updated annotation. Record actual
   client ID/callback/scope evidence privately and omit secrets from public artifacts.
3. Prepare isolated reviewer accounts and sample data. Run all eight planned cases
   using the actual account, record observed outcomes, and capture the walkthrough.
   Use [the review procedure](review-procedure.md) and [editable script](walkthrough-script.md).
4. Build the candidate with the finalized release-input JSON. Open every public URL
   as an unauthenticated visitor, verify the same publisher, and test the recording
   from a reviewer-accessible session. Check ZIP contents and keep its checksum.
5. Hand the candidate, verified results and private access details to the submission
   maintainer. That maintainer owns Comet dashboard upload, imported-field readback, automated
   scans, directory submission, legal attestations and later publication.

The current package has no country-availability declaration. Before the first
submission, the owner must inspect the dashboard's actual targeting and choose
availability. Omission preserves existing targeting; it isn't a legal or service
availability decision. Directory guidelines require a complete, stable service;
the owner must assess that gate against the hosted beta's actual operating state.

## Product and support copy

The editable listing copy, two starter prompts and release notes live in
[plugin.json](../../packaging/openai-plugin/plugin.json). The subtitle is
"Policy-aware coding guidance". The description scopes results to the configured
corpus, generation and NVIDIA services, and identifies job persistence and provider
allowance. It makes no certification or measured-quality claim.

Support follows [SUPPORT.md](../../SUPPORT.md): reproducible issues go through the
public bug form after redaction; security reports use [SECURITY.md](../../SECURITY.md).
The current support policy offers no response-time or availability guarantee.
Private privacy/deletion requests need an owner-approved contact route before the
policy draft can become effective. Don't ask users to post personal data publicly.

## Release notes source draft

Plugin package 0.1.0 adds portable metadata and an authenticated remote MCP
connection for policy search, source compilation, preflight guidance, evidence
inspection and evaluation-job submission, progress, cancellation and reports.
The package includes eight planned review cases and an icon derived from existing
assets. Cancellation discovery now marks the operation as potentially destructive.
The package builder validates offline and reports remaining live acceptance gates.
These notes describe the package change; publication status follows the dashboard.

## Verification boundary

Automated package tests validate schema pins, root layout, reproducible bytes,
metadata limits, references, asset structure, secret-shaped text rejection, unknown
input rejection and preservation of existing output on validation failure. An exact
allowlist excludes local environments, private job state, legal drafts and reviewer
credentials. This narrows what can ship; human content review remains part of the
handoff. SDK tests cover authenticated discovery and job workflow behavior using
offline provider doubles. See the PR for exact-head CI and Qodo evidence.

All eight directory cases remain **planned** until actual runs are recorded through
the review procedure. Package validation doesn't execute provider work or establish
production OAuth, recording access, approved legal text or reviewer access.
