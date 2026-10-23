# PolicyNIM reviewer setup and execution procedure

Prepare dedicated sample accounts before executing the eight planned cases in
[plugin.json](../../packaging/openai-plugin/plugin.json). That manifest's
`extensions.com.openai.review.test_cases` is the canonical editable case list:
five positive cases and three negative cases. Expected behavior describes the
target observation. Every live directory case is currently **planned**.

## Exposed service contract

Refresh discovery against the final deployed revision using an authenticated client.
The plugin connection discovers the entire endpoint; it doesn't select four tools.
The expected current contract is:

| Tool | Purpose | Read-only / destructive / open-world | Required authority |
| --- | --- | --- | --- |
| `policy_search` | Source chunks and citations | true / false / true | `policies:read` |
| `policy_compile` | Complete selected policy packet | true / false / true | read + evaluate |
| `policy_preflight` | Grounded guidance | true / false / true | read + evaluate |
| `policy_preflight_evidence` | One preflight result with evidence trace | true / false / true | read + evaluate |
| `eval_submit` | Bounded durable job | false / false / true | read + evaluate + ownership |
| `eval_inspect` | Accepted job state and progress | true / false / false | read + evaluate + ownership |
| `eval_cancel` | Cancellation request for owned job | false / true / true | read + evaluate + ownership |
| `eval_result` | Final report manifest | true / false / false | read + evaluate + ownership |

Here, read + evaluate means both `policies:read` and `policies:evaluate`.
The four guidance tools report `idempotentHint=false`; submission/cancellation
report true. Inspect/result currently omit that optional hint. Record raw discovery
privately and check serialized `annotations` and per-tool OAuth `securitySchemes`,
including their `_meta` mirror. A live endpoint mismatch blocks submission and
must be reconciled with the owning implementation chat.

Policy resources include `policynim://catalog`, retained-generation catalogs, policy
documents and chunks. Use their returned immutable URIs rather than constructing
filesystem paths. The user-invoked `policy_guided_review` prompt helps review an
engineering task against those sources. Owned evaluation reports and report parts
are available through returned `policynim://evaluation-jobs/...` resources. These
are application jobs; the package makes no MCP Tasks capability claim.

## Safe reviewer setup

1. Have the OAuth owner create two dedicated review identities in the final issuer,
   separate from personal/admin accounts. Give each only the scopes needed above,
   a sample-only workspace and a bounded allowance. Record login URL, tenant,
   sign-in steps, client ID and callback configuration privately. Keep any MFA
   process usable for review without publishing recovery codes or shared secrets.
2. Use the public policy corpus at the tested revision, retaining its pack manifests,
   attribution and license notices. Inspect the catalog and sources to confirm
   that the API-change prompts in P1-P3 have relevant coverage. Record the actual
   generation ID and catalog digest. Confirm `review-absent-domain` is absent for
   N3. Don't upload private policy documents, production tasks or customer data.
3. Prepare a one-case `EvalSuite` JSON from the sample task below, using the actual
   catalog digest as `expected_catalog_digest`. Validate it with the runtime's
   `EvalSuite` model and the service's snapshot requirements. Supply that finalized
   sample suite to the reviewer privately. The sample text alone isn't a pinned
   suite and shouldn't be sent as a completed `eval_submit` request.
4. Get the required provider-work budget approval before execution. P4 and P5 each
   request a 120-second deadline and at most eight provider calls. Inspect actual
   allowance and generation readiness before sending either request. Create the
   second account's sample job for N2 only within that approved budget.
5. Enter reviewer credentials and access instructions only in the dashboard's
   private Review details form. Keep accounts/sample data available for review
   and subsequent checks. Rotate or revoke access afterward using the agreed
   process, and update review access privately when sign-in details change.

Suggested sample case fields:

```json
{
  "case_id": "review-api-search",
  "kind": "search",
  "input": "Which engineering policies apply to adding a public API endpoint?",
  "top_k": 3,
  "expected_insufficient_context": false,
  "expected_chunk_ids": [],
  "expected_policy_ids": [],
  "forbidden_policy_ids": []
}
```

This sample checks workflow behavior. Before evaluating retrieval quality, author
and inspect expected policy/chunk IDs against the pinned catalog. Empty expectation
lists don't establish that the right policy was found. Use a new idempotency key
for an intentionally new run and retain the original key for uncertain retries.

## Planned cases and execution record

| Case | Workflow | Live execution state |
| --- | --- | --- |
| P1 | Policy source search | Planned |
| P2 | Compilation and preflight | Planned |
| P3 | Single-run evidence trace and source readback | Planned |
| P4 | Submit, inspect, reconnect, report and same-key retry | Planned |
| P5 | Owned-job cancellation and terminal-state inspection | Planned |
| N1 | Unrelated request, no tool invocation | Planned |
| N2 | Cross-owner job/report access denied | Planned |
| N3 | Absent policy domain, insufficient-context fallback | Planned |

For P5, a fast job can finish before cancellation reaches it. Record the real state.
If that happens, prepare another sample run within the authorized budget and
capture cancellation while queued or running. Offline test doubles can exercise
this race deterministically; they aren't a substitute for the live observation.

Record each actual run in a private editable table with case ID, timestamp,
deployed SHA, plugin ZIP SHA256, client/version, sample generation/catalog digest,
actual tools, safe job handle, observed outcome, pass/fail/blocked, evidence link
and follow-up. Keep account identifiers and any task/report data outside public PRs
and the ZIP. Record failed or blocked runs without replacing them with expected
text. The package's planned-case data stays separate from those observations.

Separately verify invalid/expired tokens, insufficient scopes and refresh/reconnect
through the OAuth owner's acceptance procedure. Check public URLs, publisher
identity, retention settings and actual provider behavior before submission.

## Recording checklist

1. Open a clean ChatGPT review session with only the dedicated account and sample
   data. Hide credentials, browser account menus, notifications, local paths and
   private operator dashboards. Show package version and the tested service revision.
2. Use the [script](walkthrough-script.md) while demonstrating P1-P3, including an
   actual source readback. Keep spoken claims aligned with visible output.
3. Demonstrate P4/P5 with approved bounds, actual job states, reconnect, report links
   and the cancellation confirmation where the client requires one. Capture N1-N3
   with the correct sample account context and expected safe handling.
4. Watch and listen to the recording. Check every case is visible, joins preserve
   the actual sequence, text can be read, and no credentials or personal data appear.
   Keep failures visible or explain the actual repeat without inventing a success.
5. Publish the recording only through the authorized owner workflow. Verify its
   reviewer access, retain the real URL and update the release-input file. No
   recording has been created or uploaded by this package implementation.
