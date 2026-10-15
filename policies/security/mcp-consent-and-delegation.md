---
policy_id: MCP-CONSENT-001
title: MCP Consent and Delegation
doc_type: security-standard
domain: security
tags: [mcp-consent-001, security, review]
grounded_in:
  - https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx
  - https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/specification/2026-07-28/server/tools.mdx
---

# MCP Consent and Delegation

## Intent

A proxy connecting an MCP client to a third-party API must preserve the user's approval for that client and requested capability.

## Applicability

Roles: mcp-client, mcp-proxy. Apply only to the behavior described below; a combined deployment can hold several roles.

## Required Rules

- **CONSENT-1** A proxy using a shared upstream client ID must obtain and check per-user, per-client consent before the third-party authorization flow; a prior upstream consent cookie can't substitute for it. Support: MCP-SECURITY:Confused Deputy Problem.
- **CONSENT-2** Show the requesting client, requested downstream scopes, and registered redirect URI; protect consent against CSRF and framing, and bind any consent cookie to that client. Support: MCP-SECURITY:Confused Deputy Problem.
- **CONSENT-3** Match registered redirect URIs exactly; create state only after consent, bind it to the callback session, and reject missing, mismatched, replayed, or expired state. Support: MCP-SECURITY:Confused Deputy Problem.
- **CONSENT-4** When consent is denied or cancelled, stop the authorization flow and discard its pending state without triggering the downstream action. Support: PolicyNIM decision.

## Recommended Guidance

- **CONSENT-5** Show inputs and seek user confirmation for sensitive tool actions under the host's approval policy; treat expanded scope or a changed client as a new consent decision. Support: MCP-TOOLS:User Interaction Model; MCP-TOOLS:Security Considerations; PolicyNIM decision.

## Review and Test Expectations

Test two clients sharing one upstream OAuth registration, consent cancellation, callback replay, changed redirect URI, and scope expansion. Verify no code exchange or tool action follows refusal. These are review expectations for this PolicyNIM adaptation.

## Exceptions and Boundaries

Direct stdio launch consent belongs to MCP-TRUST-001. A service principal without an interactive user needs its documented grant policy; don't invent a human consent screen for it. Any deviation from a project requirement needs an owner, reason, compensating control, and review date (PolicyNIM decision); it can't waive an applicable upstream obligation.

## Examples

Applicable: A new MCP client encounters its own consent screen even though the browser already approved the proxy's upstream client ID.

Violation: The proxy reuses another MCP client's consent cookie and completes the authorization redirect without checking the requesting client.

## Sources and Change Notice

This policy is PolicyNIM guidance adapted from the pinned sources below. Required Rules are requirements of this project baseline; recommended guidance retains that lower strength. A PolicyNIM decision isn't an upstream mandate. The referenced ASVS controls are selected controls, with their original levels retained upstream; this policy doesn't establish an ASVS verification level or certification.

- MCP-SECURITY: [MCP 2026-07-28 Security Best Practices](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx); sections: Confused Deputy Problem, Token Passthrough, Server-Side Request Forgery (SSRF), State Handle Hijacking, Local MCP Server Compromise, OAuth Authorization URL Validation, stdio Transport Security in Proxy Scenarios, Scope Minimization.
- MCP-TOOLS: [MCP 2026-07-28 Tools](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/specification/2026-07-28/server/tools.mdx); sections: User Interaction Model, Tool, Output Schema, Security Considerations.

Attribution: Model Context Protocol contributors. Changes: selected guidance restated, role boundaries, project requirements, examples, and review scenarios added.

This adapted policy text is available under [CC BY-SA 4.0](../licenses/CC-BY-SA-4.0.txt). Upstream MCP licensing and retained notices appear in [MCP upstream notice](../licenses/MCP-UPSTREAM.txt); no per-contributor relicensing status is asserted. Repository software licensing is separate.
