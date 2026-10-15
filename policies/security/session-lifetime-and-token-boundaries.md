---
policy_id: SEC-SESSION-001
title: Session Lifetime and Token Boundaries
doc_type: security-standard
domain: security
tags: [sec-session-001, security, review]
grounded_in:
  - https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x23-V14-Data-Protection.md
  - https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x16-V7-Session-Management.md
  - https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx
---

# Session Lifetime and Token Boundaries

## Intent

Keep login sessions, bearer credentials, and opaque application state separate. MCP 2026-07-28 has no protocol-level sessions; legacy transport session IDs and current state handles aren't identity.

## Applicability

Roles: application-server, mcp-server, mcp-proxy. Apply only to the behavior described below; a combined deployment can hold several roles.

## Required Rules

- **SESSION-1** Document risk-based idle and absolute login-session limits; enforce expiry and termination on the trusted backend, including refresh, logout, privilege changes, suspected compromise, and account disablement. Support: ASVS-SESSION:7.1.1; ASVS-SESSION:7.2.1; ASVS-SESSION:7.3.1; ASVS-SESSION:7.3.2; ASVS-SESSION:7.4.1; ASVS-SESSION:7.4.2; PolicyNIM decision.
- **SESSION-2** Replace the login session token after authentication or re-authentication and terminate its predecessor. Support: ASVS-SESSION:7.2.4.
- **SESSION-3** Authenticate every protected MCP request; never accept possession of a legacy session ID or application state handle as authentication. Support: MCP-SECURITY:State Handle Hijacking.
- **SESSION-4** Bind state handles to the verified principal and reject cross-principal reuse, including reconnects; don't trust a user ID supplied alongside the handle. Support: MCP-SECURITY:State Handle Hijacking; PolicyNIM decision.
- **SESSION-6** Keep token contents minimal; exclude credentials, raw personal data, and mutable business state from client-visible tokens. Support: ASVS-DATA:14.2.6; PolicyNIM decision.

## Recommended Guidance

- **SESSION-5** Use unpredictable, expiring state handles and safe generic credential errors when detailed states would expose account information. Support: MCP-SECURITY:State Handle Hijacking; PolicyNIM decision.

## Review and Test Expectations

Test expiration at the boundary, refresh after revocation, logout, account disablement, two-principal handle reuse, and reconnect with a valid handle but missing or expired credentials. These are review expectations for this PolicyNIM adaptation.

## Exceptions and Boundaries

A stateless public tool with no protected state doesn't need a login-session store. Stdio process trust belongs to MCP-TRUST-001; adding a session ID doesn't create execution isolation. Any deviation from a project requirement needs an owner, reason, compensating control, and review date (PolicyNIM decision); it can't waive an applicable upstream obligation.

## Examples

Applicable: After reconnect, a valid access token resolves the principal; a handle owned by another principal is rejected.

Violation: An attacker copies Mcp-Session-Id from a legacy client and the server treats it as the authenticated user.

## Sources and Change Notice

This policy is PolicyNIM guidance adapted from the pinned sources below. Required Rules are requirements of this project baseline; recommended guidance retains that lower strength. A PolicyNIM decision isn't an upstream mandate. The referenced ASVS controls are selected controls, with their original levels retained upstream; this policy doesn't establish an ASVS verification level or certification.

- ASVS-DATA: [OWASP ASVS 5.0.0 ASVS-DATA](https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x23-V14-Data-Protection.md); sections: 14.1.1, 14.1.2, 14.2.1, 14.2.3, 14.2.6.
- ASVS-SESSION: [OWASP ASVS 5.0.0 ASVS-SESSION](https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x16-V7-Session-Management.md); sections: 7.1.1, 7.2.1, 7.2.4, 7.3.1, 7.3.2, 7.4.1, 7.4.2.
- MCP-SECURITY: [MCP 2026-07-28 Security Best Practices](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx); sections: Confused Deputy Problem, Token Passthrough, Server-Side Request Forgery (SSRF), State Handle Hijacking, Local MCP Server Compromise, OAuth Authorization URL Validation, stdio Transport Security in Proxy Scenarios, Scope Minimization.

Attribution: Model Context Protocol contributors; OWASP ASVS contributors, version 5.0.0. Changes: selected guidance restated, role boundaries, project requirements, examples, and review scenarios added.

This adapted policy text is available under [CC BY-SA 4.0](../licenses/CC-BY-SA-4.0.txt). Upstream MCP licensing and retained notices appear in [MCP upstream notice](../licenses/MCP-UPSTREAM.txt); no per-contributor relicensing status is asserted. Repository software licensing is separate.
