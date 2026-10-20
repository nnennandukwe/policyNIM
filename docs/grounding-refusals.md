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

For the Lightning model, request-side JSON Schema also binds each guidance item to one retained policy's exact ID/title pair and permits only that owner's chunk IDs in its citation list. Top-level citations can use the complete retained set, including compiler evidence from other policies. This narrows the generation grammar; it doesn't change the public response model or replace server validation. Explicit other-model overrides retain their request behavior and remain subject to the same strict source checks.

[NVIDIA's structured-generation documentation](https://docs.nvidia.com/nim/large-language-models/1.15.0/structured-generation.html) describes schema-guided generation and notes backend limitations. [JSON Schema's composition reference](https://json-schema.org/understanding-json-schema/reference/combining#anyof) defines `anyOf`. Hosted support for this particular owner-bound schema needs a separate actual endpoint probe; generic schema support alone doesn't establish it. Metadata constraints don't independently verify the recommendations' meaning.
