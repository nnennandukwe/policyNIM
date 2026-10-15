---
policy_id: MCP-AUTH-001
title: MCP Token Audience and Scopes
doc_type: security-standard
domain: security
tags: [mcp-auth-001, security, review]
grounded_in:
  - https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x18-V9-Self-contained-Tokens.md
  - https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/specification/2026-07-28/basic/authorization/index.mdx
  - https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx
---

# MCP Token Audience and Scopes

## Intent

Hosted MCP authorization uses credentials for a particular protected resource and permission set. It doesn't confer arbitrary downstream or local process authority.

## Applicability

Roles: mcp-client, mcp-server, mcp-proxy. Apply only to the behavior described below; a combined deployment can hold several roles.

## Required Rules

- **TOKEN-1** MCP servers must validate token integrity, issuer trust, validity, intended audience, and operation permissions before using claims; reject wrong-audience and expired credentials. Support: ASVS-TOKEN:9.1.1; ASVS-TOKEN:9.1.2; ASVS-TOKEN:9.1.3; ASVS-TOKEN:9.2.1; ASVS-TOKEN:9.2.3; MCP-AUTH:Token Handling.
- **TOKEN-2** MCP clients must request resource-bound access tokens and send only credentials intended for that MCP resource; servers and proxies must never pass the inbound token through to a downstream API. Support: MCP-AUTH:Token Handling; MCP-SECURITY:Token Passthrough.
- **TOKEN-3** Use separate downstream credentials authorized for the downstream resource and verify the originating user's permitted action at the proxy boundary. Support: MCP-SECURITY:Token Passthrough; PolicyNIM decision.
- **TOKEN-4** Return 401 for invalid or expired access tokens and 403 for insufficient permission; don't treat an ID token as an API access token. Support: MCP-AUTH:Error Handling; ASVS-TOKEN:9.2.2.

## Recommended Guidance

- **TOKEN-5** Request task-appropriate scopes, include previously requested scopes during step-up, and bound retries after denied authorization. Support: MCP-AUTH:Scope Selection Strategy; MCP-AUTH:Step-Up Authorization Flow.

## Review and Test Expectations

Test a correctly signed token for another audience, wrong issuer/type, expired token, missing scope, and token passthrough. Verify rejection before tool execution and use only mock credentials. These are review expectations for this PolicyNIM adaptation.

## Exceptions and Boundaries

The MCP HTTP authorization flow doesn't apply to direct stdio process credentials. A public endpoint can be unauthenticated by explicit design; protected operations still need their own authorization. Any deviation from a project requirement needs an owner, reason, compensating control, and review date (PolicyNIM decision); it can't waive an applicable upstream obligation.

## Examples

Applicable: The resource rejects a validly signed token whose aud names a different API before running the requested tool.

Violation: A proxy forwards its incoming MCP bearer token to the third-party API as proof of downstream permission.

## Sources and Change Notice

This policy is PolicyNIM guidance adapted from the pinned sources below. Required Rules are requirements of this project baseline; recommended guidance retains that lower strength. A PolicyNIM decision isn't an upstream mandate. The referenced ASVS controls are selected controls, with their original levels retained upstream; this policy doesn't establish an ASVS verification level or certification.

- ASVS-TOKEN: [OWASP ASVS 5.0.0 ASVS-TOKEN](https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x18-V9-Self-contained-Tokens.md); sections: 9.1.1, 9.1.2, 9.1.3, 9.2.1, 9.2.2, 9.2.3.
- MCP-AUTH: [MCP 2026-07-28 Authorization](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/specification/2026-07-28/basic/authorization/index.mdx); sections: Authorization Flow Steps, Scope Selection Strategy, Access Token Usage, Token Handling, Error Handling, Step-Up Authorization Flow.
- MCP-SECURITY: [MCP 2026-07-28 Security Best Practices](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/5f5440bb26a62e2cf3440b92da5a667efa03b267/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx); sections: Confused Deputy Problem, Token Passthrough, Server-Side Request Forgery (SSRF), State Handle Hijacking, Local MCP Server Compromise, OAuth Authorization URL Validation, stdio Transport Security in Proxy Scenarios, Scope Minimization.

Attribution: Model Context Protocol contributors; OWASP ASVS contributors, version 5.0.0. Changes: selected guidance restated, role boundaries, project requirements, examples, and review scenarios added.

This adapted policy text is available under [CC BY-SA 4.0](../licenses/CC-BY-SA-4.0.txt). Upstream MCP licensing and retained notices appear in [MCP upstream notice](../licenses/MCP-UPSTREAM.txt); no per-contributor relicensing status is asserted. Repository software licensing is separate.
