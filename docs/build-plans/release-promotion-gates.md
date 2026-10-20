# Release promotion gates

The current release workflow prepares a source-bound artifact manifest and verifies attestations, but it doesn't invoke the reviewed notes checker or evidence collector. A draft can therefore omit those gates. This change wires the existing tools into that workflow.

1. Render the selected project version's authored notes before draft creation. Keep notes and helper tools checkout-scoped; package resource membership stays unchanged.
2. Use the pinned uv setup and locked runtime dependencies in the publishing job. Build an artifact/provenance-only selection from trusted CI source/tag/repository, the checkout lock, and its reviewed public bundle. Hash the manifest produced by the existing source-bound verifier; don't derive expected identities from downloaded receipts.
3. Run the existing collector after actual attestation verification. Store selection/report outside the release assets and always retain the report. Missing or mismatched evidence must stop draft creation.
4. Execute the selected workflow Python against isolated source and asset fixtures; use actual collector invocations to reject missing/mismatched provenance. Check workflow ordering and locked setup. Run focused integration tests, static checks, local Qodo, and exact-head PR gates before merge.

The artifact gate doesn't assert client acceptance, published-channel installation, production deployment, rollback, OAuth, provider quality, or service capacity. Those phases need their actual receipts and selected follow-up gates. No tag, release, publication, or deployment happens during this implementation.
