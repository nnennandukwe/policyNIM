# Grounding refusal trace codes

Preflight returns empty insufficient context when a generated draft fails source binding. With `preflight --trace`, a failed grounding check also appends a `grounding_validation` step with kind `grounding_refusal`. Its summary contains a fixed code and its citation list is empty. The normal response and acceptance checks stay unchanged.

| Code | Meaning |
| --- | --- |
| `model_insufficient_context` | The generated draft declares insufficient evidence. |
| `empty_source_context` | No retained source chunks were supplied to the shared validator. |
| `missing_citations` | The draft supplies no top-level or per-policy citations. |
| `unknown_citation` | At least one referenced chunk isn't in retained context. |
| `policy_id_mismatch` | A guidance item's policy ID doesn't own every cited chunk. |
| `policy_title_mismatch` | A guidance item's title doesn't exactly match its cited source owner. |
| `missing_policy_guidance` | No guidance item has a supported source reference. |

These codes identify structural refusal, not semantic correctness. They don't contain model-supplied values. Existing generation trace summaries remain advisory model output. Inspecting a code doesn't authorize weakening source checks or changing evaluation expectations. A later passing replay doesn't rewrite an earlier failed receipt.
