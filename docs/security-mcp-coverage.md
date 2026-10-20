# Application Security and MCP Source Coverage

The maintained security revisions introduced in `policynim.baseline@0.2.0` remain in the expanded `0.3.0` aggregate and the selectable `policynim.security-mcp@0.3.0` pack. Nine
security policies were added or materially revised for #94; the six other
members retain their existing bytes. The package/software version remains
`0.1.0`. A source-pack version doesn't publish a package or activate an index.

This guidance helps reviewers identify applicable requirements and expected
failure evidence. It doesn't implement OAuth, grant execution authority, create
a sandbox, or certify an application. Roles apply to behavior: an HTTP proxy can
be both an MCP server to its caller and a client to a downstream API.

## Topic, Source, and Scenario Matrix

Each row names the effective policy and its authored scenario IDs. The policy's
numbered rules contain exact source-section support or a `PolicyNIM decision`
label. Full commit-pinned URLs, editions, retrieval dates, attribution, licenses,
and change notices are in `policies/pack.toml` and the policy's source section.

| Topic | Effective policy and requirements | Pinned source controls/sections | Scenarios |
| --- | --- | --- | --- |
| Auth-Sensitive Code Review Standard | [SEC-AUTH-001](../policies/security/auth-sensitive-code-review-standard.md); AUTH-1, AUTH-2, AUTH-3, AUTH-4, AUTH-5, AUTH-6 | ASVS-AUTHZ:8.2.1; ASVS-AUTHZ:8.2.2; ASVS-AUTHZ:8.2.3; ASVS-AUTHZ:8.3.1; ASVS-AUTHZ:8.4.1; ASVS-LOG:16.2.5; ASVS-LOG:16.3.1; ASVS-LOG:16.3.2; ASVS-LOG:16.5.3; CS-AUTHZ:Deny by Default; CS-AUTHZ:Validate the Permissions on Every Request | sec-auth-001-applicable, sec-auth-001-violating, sec-auth-001-out-of-scope |
| Session Lifetime and Token Boundaries | [SEC-SESSION-001](../policies/security/session-lifetime-and-token-boundaries.md); SESSION-1, SESSION-2, SESSION-3, SESSION-4, SESSION-6, SESSION-5 | ASVS-DATA:14.2.6; ASVS-SESSION:7.1.1; ASVS-SESSION:7.2.1; ASVS-SESSION:7.2.4; ASVS-SESSION:7.3.1; ASVS-SESSION:7.3.2; ASVS-SESSION:7.4.1; ASVS-SESSION:7.4.2; MCP-SECURITY:State Handle Hijacking | sec-session-001-applicable, sec-session-001-violating, sec-session-001-out-of-scope, sec-session-001-revoked-refresh |
| Secrets Redaction and Handling | [SEC-SECRET-001](../policies/security/secrets-redaction-and-handling.md); SECRET-1, SECRET-2, SECRET-3, SECRET-4, SECRET-5 | ASVS-DATA:14.1.1; ASVS-DATA:14.1.2; ASVS-DATA:14.2.1; ASVS-DATA:14.2.6; ASVS-LOG:16.2.5; ASVS-LOG:16.5.1; CS-LOG:Data to exclude; CS-SECRET:2 General Secrets Management | sec-secret-001-applicable, sec-secret-001-violating, sec-secret-001-out-of-scope |
| Input and Output Validation | [SEC-INPUT-001](../policies/security/input-output-validation.md); INPUT-1, INPUT-2, INPUT-3, INPUT-4 | ASVS-ENCODE:1.1.2; ASVS-ENCODE:1.2.1; ASVS-ENCODE:1.2.4; ASVS-ENCODE:1.2.5; ASVS-INPUT:2.1.1; ASVS-INPUT:2.2.1; ASVS-INPUT:2.2.2; MCP-TOOLS:Output Schema; MCP-TOOLS:Security Considerations | sec-input-001-applicable, sec-input-001-violating, sec-input-001-out-of-scope, sec-input-001-malformed-output |
| SSRF and Egress Boundaries | [SEC-EGRESS-001](../policies/security/ssrf-and-egress.md); EGRESS-1, EGRESS-2, EGRESS-3, EGRESS-4 | ASVS-ENCODE:1.3.6; CS-SSRF:Case 2 - Application can send requests to ANY external IP address or domain name; MCP-SECURITY:OAuth Authorization URL Validation; MCP-SECURITY:Server-Side Request Forgery (SSRF) | sec-egress-001-applicable, sec-egress-001-violating, sec-egress-001-out-of-scope, sec-egress-001-executable-auth-url |
| MCP Token Audience and Scopes | [MCP-AUTH-001](../policies/security/mcp-token-audience-and-scopes.md); TOKEN-1, TOKEN-2, TOKEN-3, TOKEN-4, TOKEN-5 | ASVS-TOKEN:9.1.1; ASVS-TOKEN:9.1.2; ASVS-TOKEN:9.1.3; ASVS-TOKEN:9.2.1; ASVS-TOKEN:9.2.2; ASVS-TOKEN:9.2.3; MCP-AUTH:Error Handling; MCP-AUTH:Scope Selection Strategy; MCP-AUTH:Step-Up Authorization Flow; MCP-AUTH:Token Handling; MCP-SECURITY:Token Passthrough | mcp-auth-001-applicable, mcp-auth-001-violating, mcp-auth-001-out-of-scope, mcp-auth-001-insufficient-scope |
| MCP Consent and Delegation | [MCP-CONSENT-001](../policies/security/mcp-consent-and-delegation.md); CONSENT-1, CONSENT-2, CONSENT-3, CONSENT-4, CONSENT-5 | MCP-SECURITY:Confused Deputy Problem; MCP-TOOLS:Security Considerations; MCP-TOOLS:User Interaction Model | mcp-consent-001-applicable, mcp-consent-001-violating, mcp-consent-001-out-of-scope, mcp-consent-001-cancelled-consent |
| MCP Server and Process Trust | [MCP-TRUST-001](../policies/security/mcp-server-and-process-trust.md); TRUST-1, TRUST-2, TRUST-3, TRUST-4, TRUST-5 | ASVS-DEPENDENCY:15.1.1; ASVS-DEPENDENCY:15.1.2; ASVS-DEPENDENCY:15.2.1; ASVS-DEPENDENCY:15.2.4; MCP-SECURITY:Local MCP Server Compromise; MCP-SECURITY:stdio Transport Security in Proxy Scenarios | mcp-trust-001-applicable, mcp-trust-001-violating, mcp-trust-001-out-of-scope, mcp-trust-001-cancelled-launch, mcp-trust-001-dependency-origin |
| Untrusted Tool and Resource Content | [MCP-CONTENT-001](../policies/security/untrusted-tool-and-resource-content.md); CONTENT-1, CONTENT-2, CONTENT-3, CONTENT-4, CONTENT-5 | MCP-TOOLS:Security Considerations; MCP-TOOLS:Tool | mcp-content-001-applicable, mcp-content-001-violating, mcp-content-001-out-of-scope |

## Role Boundaries

| Role | Applicable behavior | Limits |
| --- | --- | --- |
| Application server | Authentication, object/field/tenant authorization, sessions, validation, redaction, outbound requests | A public endpoint can omit identity by explicit design while retaining data protections. |
| MCP server | Validate per-request credentials and tool inputs; return schema-conforming outputs; bind state to verified principals | No protocol-level sessions exist in MCP 2026-07-28. A legacy session ID or current state handle isn't identity. |
| MCP client/host | Resource-bound credentials, result validation, consent, untrusted-content handling, local launch review | Direct stdio process credentials don't require the hosted HTTP OAuth flow. |
| MCP proxy | Both client/server boundaries; separate downstream credentials, per-client consent where using a shared upstream client ID; authorized process spawning if provided | Inbound tokens aren't passed through to downstream APIs. One client's consent doesn't cover another. |
| Deployment operator | Secret delivery, network policy, reviewed dependencies, actual process/filesystem/network restrictions | Stdio, authentication, containers, and subprocess creation alone don't prove execution isolation. |

## Pinned Editions and Notices

- OWASP ASVS **5.0.0**, release tag `v5.0.0_release`, commit
  `5cf9b032440be53ce345ab3c130fda46ba1ce7a2`. Selected control IDs retain
  their upstream meaning and levels; this subset doesn't establish an ASVS level.
- OWASP Cheat Sheet Series commit
  `72865b1e3b5754246a101778dc00b37f9c233373`, retrieved October 15, 2026.
  These are selected published cheat sheets, not the `cheatsheets_draft` tree.
- MCP **2026-07-28**, repository revision
  `5f5440bb26a62e2cf3440b92da5a667efa03b267`: Authorization, Tools, and
  that edition's Security Best Practices. Links to `latest`, draft OAuth texts,
  or optional extensions inside upstream material aren't independently promoted
  to stable PolicyNIM requirements.

Attribution belongs to the OWASP ASVS contributors, OWASP Cheat Sheet Series
contributors, and Model Context Protocol contributors. PolicyNIM changes include
selected control restatements, role limits, review requirements, project decisions,
and examples. The adapted nine policy files are available under CC BY-SA 4.0;
repository software keeps its separate MIT license. This doesn't relicense
upstream MCP material or assert endorsement.

Retained source notices travel in the policy pack and wheel:

- [ASVS CC BY-SA 4.0 license](../policies/licenses/CC-BY-SA-4.0.txt).
- [OWASP Cheat Sheet Series notice](../policies/licenses/OWASP-CHEATSHEETS-UPSTREAM.txt).
- [MCP upstream notice and license texts](../policies/licenses/MCP-UPSTREAM.txt).
  The pinned project describes an Apache-2.0 transition with retained MIT
  contributions; non-specification documentation is CC-BY-4.0. Per-contribution
  relicensing consent isn't known here and isn't asserted in the catalog.

## Scenario Evidence and Verification

`evals/security_mcp_scenarios.json` contains 34 authored source scenarios:
applicable and violating cases for each changed policy, role-specific out-of-scope
cases, and extra refusal/revocation cases. Each expected policy ID, revision, rule,
and exact evidence quote refers to the selected policy bytes. Cases are scoped to
their `policy_id`; expected IDs aren't exhaustive predictions of every policy
a future retrieval run should return. Wrong-audience
tokens, legacy session-ID-as-identity, token passthrough, malicious destinations,
and instructions in retrieved text are explicit cases.

Run the deterministic content checks without model credentials:

```bash
uv sync --locked --group test --group dev
uv run --no-sync policynim policies validate --source --format json
uv run --no-sync pytest -q tests/test_security_content.py
```

The tests check selected membership, source support declarations, paired cases,
role labels, revision freshness, evidence after real Markdown chunking, and a full
ingest/SQLite round trip with deterministic embedding doubles. They don't execute
an OAuth implementation, sandbox, agent, or malicious network request. They don't
classify scenario behavior independently of the authored expectations.

The existing `evals/default_cases.json` and evaluation runner remain unchanged.
Their offline doubles use synthetic IDs and measure their original workflow;
passing those cases doesn't establish retrieval quality for the expanded corpus.
The new scenarios aren't silently fed into those synthetic candidates. #96 owns
runner integration and generation/profile-specific evaluation once #92/#93/#94/#95
contracts exist. No manual paid provider calls were made for these fixtures.

## Explicit Omissions and Unknowns

The other six baseline members retain legacy provenance declarations where no
reviewed source revision was recorded. This change doesn't invent their history.
Existing public-endpoint safety remains `SEC-PUBLIC-ENDPOINT-002`; redaction in
`SEC-SECRET-001` complements it rather than creating a competing readiness policy.

This subset doesn't cover every ASVS control, a complete OAuth/client conformance
suite, organization-specific identity policy, every dependency vulnerability,
or universal prompt-injection resistance. Broader engineering packs belong to
#95. Composition, exclusions, and activation belong to #92/#93.

The catalog's `indexed_count` stays `null`. To inspect a local index, run
`uv run --no-sync policynim inventory --format json`; confirm its source/index
identity separately. Neither source tests nor a merged PR establish hosted
inventory, live quality, deployment readiness, or package publication.
