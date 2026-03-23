---
policy_id: SEC-SESSION-001
title: Session Lifetime and Token Boundaries
doc_type: security-standard
domain: security
tags:
  - session
  - tokens
  - expiration
---

# Session Lifetime and Token Boundaries

## Intent

Session and token behavior should be predictable, bounded, and easy to revoke so
credential exposure has a limited blast radius.

## Required Rules

- Session and token lifetimes must be explicit in product behavior, not implied by
  implementation defaults.
- Refresh or renewal paths must preserve revocation and expiry checks.
- Privilege changes, logout, and suspected compromise must have a clear invalidation
  path.
- Token contents must stay minimal and must not include secrets, raw PII, or mutable
  business state.
- Client-facing errors should distinguish invalid, expired, and revoked states when
  that distinction is safe and useful.

## Review Expectations

- Verify token issuance, refresh, and revocation behavior are covered together.
- Verify expiration and clock-skew behavior is tested at the boundary.
- Verify session storage and transport choices do not leak credentials into logs,
  analytics, or support artifacts.

## Public Grounding

- OWASP authentication and session management guidance informed the lifetime,
  revocation, and boundary rules.
