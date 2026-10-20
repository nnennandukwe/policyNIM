# Evaluation report provenance

Evaluation reports currently retain model names and source identity, but don't retain the sampling values or group executed gold cases by pinned source family and profile. That makes a later result harder to interpret without the old process environment.

Add optional run metadata with resolved request settings and executed-case family coverage. Dispatch and reporting must share the same chat-options builder and conformance thresholds. Family membership comes from the held generation's gold policy/chunk owners, using declared pack IDs or an explicit source-domain fallback. Missing assignments stay visible. Counts can overlap when a gold case expects several families; authored scenarios and generated answers don't establish executed coverage.

Keep case-result fields and nested shapes unchanged, since retained jobs hash those models. Mode/run metadata defaults must allow historical report reads. `evaluate_case` must retain its no-report-side-effects contract. Verify actual outgoing request values, generation/profile coverage with controlled public evaluation paths, omitted conformance evidence, historical report parsing, and unchanged case schemas. No additional provider calls are needed for this reporting slice.

Review follow-up: family `conformance_case_count` must use the same final-result meaning as aggregate metrics. Add `judged_case_count` for cases with a returned judgment in their final result or retained regeneration attempts; historical reports without this evidence retain an unavailable value. Reproduce a judged first attempt followed by insufficient context through the public evaluator. Keep case-result schemas and hashes unchanged.
