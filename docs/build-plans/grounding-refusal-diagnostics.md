# Grounding refusal diagnostics

The final 030ec74/38727482 live evaluation passed nine of ten cases. The cleanup-job case had complete retained source context and successful compilation, then returned empty insufficient context. Its trace didn't identify which grounding guard refused the generated draft. One unchanged diagnostic replay passed, so the original cause remains unknown.

Add a fixed-code refusal step to the existing optional trace. Keep the shared preflight/regeneration materialization contract, strict policy ownership/title/citation checks, response schemas, normal outputs, model settings, and gold expectations unchanged. The new step mustn't include model text, invalid IDs, titles, or citations.

Public regressions will exercise each reachable refusal; lower-level checks cover empty source and citation input. Existing valid trace and regeneration cases must retain their behavior. After tests and source freeze, one diagnostic sample may execute the cleanup-job case at most five times, retaining every attempt and stopping on the first failure. This is diagnosis, not retrying until a pass. No full live suite will run before the cause is supported.

The bounded sample stopped after two executions: one passed, then the same case failed with `policy_id_mismatch`. Its typed draft used full chunk IDs as policy IDs and appended section names to policy titles. The strict guard refused it correctly. Request-side generation grammar will now use per-owner `anyOf` variants with exact ID/title pairs and owner-specific citation enums. Global citations will retain the complete context's allowed set. An actual endpoint support probe will precede unchanged-case acceptance; no response coercion, automatic schema fallback, or changed gold is planned.
