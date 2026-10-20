# Changelog

## [Unreleased]

## [0.2.0] - Candidate

These are candidate release notes. Package publication and production rollout still
need final release receipts. They don't establish coding-client acceptance, provider
quality or production capacity.

### Implemented foundations

- Immutable policy generations bind source, catalog, index and runtime-rule artifacts.
  Publication, activation, reader leases, retention and rollback are covered by the
  generation work in [#117](https://github.com/nnennandukwe/policyNIM/pull/117).
- Principal-aware operation admission separates accepted work units, active slots and
  caller outcomes. Weighted evaluation reservations retain their charge after accepted
  cancellation or failure; they don't represent provider tokens or monetary cost.
- Policy inventory and readiness diagnostics report source/catalog/index compatibility.
  Content-free operational reports keep unavailable provider measurements explicit.

### Candidate release additions

- Grounded MCP compilation, evidence, resource and prompt interfaces are being integrated
  through [#125](https://github.com/nnennandukwe/policyNIM/pull/125).
- Durable evaluation submission, ownership, cancellation, case results and retained
  reports are being integrated through [#130](https://github.com/nnennandukwe/policyNIM/pull/130).
- Fresh wheel and standalone receipts bind source, dependency lock, policy generation
  and artifact checksums through [#131](https://github.com/nnennandukwe/policyNIM/pull/131).
  Signed provenance and optional installer verification are tracked in
  [#145](https://github.com/nnennandukwe/policyNIM/pull/145).
- The package metadata prepares version 0.2.0, Beta status and the Python typing marker.
  Offline evidence collection keeps selected expectations, recomputed artifact facts,
  supplied execution receipts and operator declarations separate.

### Compatibility and limits

- Beta and operator bearer keys remain supported. OAuth issuer/client acceptance is
  pending; no external issuer has been selected or configured for production.
- SDK protocol checks don't establish actual Codex or Claude Code acceptance. Final
  client, deployment, rollback and optional-dependency receipts remain under
  [#104](https://github.com/nnennandukwe/policyNIM/issues/104).
- Measured token usage requires a provider response. Unknown usage isn't zero usage,
  and neither token cost nor service capacity is inferred from offline tests.
- Weighted-ledger data needs a compatible application version. Don't restore an older
  quota writer against migrated state; retain compatible application and policy backups.
