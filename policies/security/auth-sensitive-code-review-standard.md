---
policy_id: SEC-AUTH-001
title: Auth-Sensitive Code Review Standard
doc_type: security-standard
domain: security
tags: [sec-auth-001, security, review]
grounded_in:
  - https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x17-V8-Authorization.md
  - https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x25-V16-Security-Logging-and-Error-Handling.md
  - https://github.com/OWASP/CheatSheetSeries/blob/72865b1e3b5754246a101778dc00b37f9c233373/cheatsheets/Authorization_Cheat_Sheet.md
---

# Auth-Sensitive Code Review Standard

## Intent

Authentication establishes who a caller is. Authorization decides which action, object, field, and tenant that caller may access. Review both when changing a privilege boundary.

## Applicability

Roles: application-server, mcp-server, mcp-proxy, deployment-operator. Apply only to the behavior described below; a combined deployment can hold several roles.

## Required Rules

- **AUTH-1** Enforce function, object, field, and tenant permissions at a trusted service boundary on every request; a successful login alone grants no operation permission. Support: ASVS-AUTHZ:8.2.1; ASVS-AUTHZ:8.2.2; ASVS-AUTHZ:8.2.3; ASVS-AUTHZ:8.3.1; ASVS-AUTHZ:8.4.1; CS-AUTHZ:Validate the Permissions on Every Request.
- **AUTH-2** Deny access when permission evaluation fails or no permission rule matches. Support: CS-AUTHZ:Deny by Default; ASVS-LOG:16.5.3.
- **AUTH-3** Document the asset, attacker action, privilege transition, and token issuance, refresh, expiry, revocation, and rollback behavior for an auth-sensitive change. Support: PolicyNIM decision.
- **AUTH-4** Record safe authentication and denied-authorization events without credential bodies. Support: ASVS-LOG:16.3.1; ASVS-LOG:16.3.2; ASVS-LOG:16.2.5.
- **AUTH-5** Use a reviewer who didn't author the auth logic and require invalid, expired, revoked, and cross-tenant rejection tests where applicable. Support: PolicyNIM decision.

## Recommended Guidance

- **AUTH-6** Reuse a reviewed threat model when its assets, trust boundaries, and abuse paths still match the change. Support: PolicyNIM decision.

## Review and Test Expectations

Inspect permission decisions for two users and two tenants, including guessed object IDs and a permission-check outage. Review migration and rollback evidence for changed credential formats. These are review expectations for this PolicyNIM adaptation.

## Exceptions and Boundaries

A deliberately public operation can omit caller authentication only with documented public data and action boundaries; its input and output safeguards still apply. Any deviation from a project requirement needs an owner, reason, compensating control, and review date (PolicyNIM decision); it can't waive an applicable upstream obligation.

## Examples

Applicable: A signed-in reader requests another tenant's record; the service denies access before reading the protected object.

Violation: The handler treats any valid login token as permission to delete any user's object.

## Sources and Change Notice

This policy is PolicyNIM guidance adapted from the pinned sources below. Required Rules are requirements of this project baseline; recommended guidance retains that lower strength. A PolicyNIM decision isn't an upstream mandate. The referenced ASVS controls are selected controls, with their original levels retained upstream; this policy doesn't establish an ASVS verification level or certification.

- ASVS-AUTHZ: [OWASP ASVS 5.0.0 ASVS-AUTHZ](https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x17-V8-Authorization.md); sections: 8.1.1, 8.2.1, 8.2.2, 8.2.3, 8.3.1, 8.4.1.
- ASVS-LOG: [OWASP ASVS 5.0.0 ASVS-LOG](https://github.com/OWASP/ASVS/blob/5cf9b032440be53ce345ab3c130fda46ba1ce7a2/5.0/en/0x25-V16-Security-Logging-and-Error-Handling.md); sections: 16.2.5, 16.3.1, 16.3.2, 16.4.1, 16.5.1, 16.5.3.
- CS-AUTHZ: [OWASP Authorization Cheat Sheet](https://github.com/OWASP/CheatSheetSeries/blob/72865b1e3b5754246a101778dc00b37f9c233373/cheatsheets/Authorization_Cheat_Sheet.md); sections: Deny by Default, Validate the Permissions on Every Request.

Attribution: OWASP ASVS contributors, version 5.0.0; OWASP Cheat Sheet Series contributors. Changes: selected guidance restated, role boundaries, project requirements, examples, and review scenarios added.

This adapted policy text is available under [CC BY-SA 4.0](../licenses/CC-BY-SA-4.0.txt). Upstream MCP licensing and retained notices appear in [MCP upstream notice](../licenses/MCP-UPSTREAM.txt); no per-contributor relicensing status is asserted. Repository software licensing is separate.
