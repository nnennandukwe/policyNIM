Selected policy context: implementation contract

The current live cleanup-job case retrieved its required-rules chunk at dense rank 2, reranked it to rank 12, and retained only the policy's intent. The selected policy ID was correct, but downstream generation lacked its conditions, required rules, and exceptions. The frozen ten-case run passed 9/10; its expectations won't change.

Keep discovery ranking, candidate depth, selected effective policy IDs, and packet evidence unchanged. Read complete chunks for those IDs through the same pinned index. Validate each discovery chunk against indexed identity and append missing chunks with score=None. Excluded or superseded policies can't enter through hydration. Custom headings receive complete context too.

Refuse the entire context above 80 chunks or 256 KiB of serialized scored-chunk JSON. These are operational budgets, not provider limits. Return a stable safe error with guidance to narrow selection or split reviewed policy documents. Packet reasons report discovery and additional-context counts. top_k continues to control discovery hits.

Verification will combine the preserved live trace with public router regressions, actual generation/profile and EvalService acceptance boundaries, and exact-head review/CI. Offline doubles prove orchestration and structural rejection; they don't prove model adherence or universal prompt-injection resistance. Root owns the unchanged live ten-case rerun and release decision.
