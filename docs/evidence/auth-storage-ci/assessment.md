# PR156 capacity failure assessment

CI at `9346404f` recorded three successful baseline requests and nine `admission_busy` results. Its original scenario JSON and server log weren't retained in the supplied evidence, so the actual writer and lock duration remain unknown. No runtime fix is justified by this assessment.

The public service intentionally rejects admission after its bounded SQLite busy retry window. The unchanged baseline still requires 12 successful requests. Don't relax that assertion or increase timeouts.

Observed checks:

- The unchanged macOS baseline passed all 12 requests.
- A controlled 600 ms sampler read passed all 12 requests. The real warmup had already enabled WAL; changing journal mode wouldn't address this observation.
- Linux runs at two CPUs and half a CPU passed all 12 requests. The half-CPU trace measured 29 writer BEGIN calls, with a maximum wait of 58.7 ms.
- A controlled Linux fault held each acquired auth writer transaction for 80 ms. It produced eight successes and four `admission_busy` results. All 12 outcomes were accounted for; nine provider stages included the warmup, and no active operation remained afterward. This establishes the intended fail-closed path under writer pressure. It doesn't reproduce the unknown CI cause.

These Linux checks used the existing `policynim-107:bb916253e42e-9e95a985` image plus the locked tomlkit 0.14.0 package, with current source mounted read-only. That image isn't exact CI dependency parity. Git metadata was supplied from the host readback because the image lacks Git; runtime application code wasn't replaced. The source was `51b116af`, which differs from failed `9346404f` only in the separately owned recovery wording and its assertion. The synthetic fixture never contacted NVIDIA or production.

The existing locked-database scenario already verifies deterministic reservation rejection: all 12 requests report busy, with only the warmup ledger row and provider call. A further duplicate negative case wouldn't identify this incident.

Recommended next change: retain the sanitized baseline scenario in the failed assertion output and add aggregate storage-failure phase/code counts to receipts. Keep every existing outcome assertion. A fresh exact CI run can then identify whether a reservation, finalization or initialization writer is responsible. This is an evidence improvement, not a runtime fix or capacity claim. No repository files were changed for the assessment.
