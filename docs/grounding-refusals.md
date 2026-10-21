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
| `unsupported_source_text` | Retained source ownership can't be reproduced or doesn't match, or the draft changes selected source text, adds unsupported prose or breaks the source-extractive output contract. |

These codes identify structural refusal, not semantic correctness. They don't contain model-supplied values. Existing generation trace summaries remain advisory model output. Inspecting a code doesn't authorize weakening source checks or changing evaluation expectations. A later passing replay doesn't rewrite an earlier failed receipt.

The default NVIDIA compiler and generator select compact source IDs through JSON Schema. The service then materializes complete policy bodies from the same pinned source catalog, including their conditions and examples. The grammar doesn't generate policy statements or owner metadata. Server validation also applies to custom providers and rejects changed bodies, invented summaries and mismatched owners. See the [source-extractive guidance contract](source-extractive-guidance.md) for budgets and caller responsibilities.

[NVIDIA's structured-generation documentation](https://docs.nvidia.com/nim/large-language-models/1.15.0/structured-generation.html) describes schema-guided generation and notes backend limitations. Generic schema support alone doesn't establish support for the actual endpoint and selected schema. Exact source agreement doesn't independently establish policy applicability, source trust or runtime compliance. Earlier owner-bound grammar probes remain historical evidence; they don't verify the compact selector's deployed behavior.
