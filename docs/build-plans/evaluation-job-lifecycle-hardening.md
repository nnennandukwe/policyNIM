# Evaluation job cancellation and recovery audit

Baseline: `77ecfe93c89586981e7055b06197328f2d45b090`. The finite audit covers queued cancellation and same-key retry, running cancellation/deadline cleanup, and interrupted worker recovery through CLI/MCP. Local evaluators and disposable SQLite replace paid provider execution.

The first baseline group passed 178 tests. Four added CLI regressions failed: a cleanup exception replaces cancellation, deadline or Ctrl-C with `failed`; foreground Ctrl-C without a cleanup error leaves the job running and records quota failure. These are reproduced outcomes, while other possible races remain audit questions.

## State and commit points

Reuse the state and storage model in [the original job plan](durable-evaluation-jobs.md). The canonical inputs, generation archive, case records, summary, report bytes and report-part digests remain in the job database. Quota acceptance and completion remain separate commits in the operation database. This change introduces no schema, settings, protocol or provider architecture change.

| Starting state | Operation and commit point | Visible state and recovery |
|---|---|---|
| Absent or prepaid | Quota COMMIT followed by job submission COMMIT | Same owner/key and inputs recover one accepted job without another charge. |
| Queued | Guarded claim COMMIT | Running; a new lock owner recovers interrupted claims without replay. |
| Queued | Cancellation or expired-deadline report COMMIT | Terminal report with zero provider work. |
| Running | Case intent and attempt reservation COMMIT before dispatch | Active work may be uncertain; never automatically replay it. |
| Running case | Case result COMMIT | Saved case survives restart before another case starts. |
| Running stop/failure | Drain evaluator and all pooled clients; quota completion COMMIT; terminal job/report COMMIT | Original cancellation/deadline reason remains primary through a close failure. Ctrl-C records interruption before returning from the foreground CLI. |
| Terminal | Owner-authorized result/inspect or same-key retry | Original identity and report hash; no new evaluator execution. |

## Invariants

1. Exactly one accepted job exists for the owner, key and request digest; captured input and generation hashes never change on retry.
2. Only the kernel-lock owner mutates running work, and every mutation rechecks the worker token. Restart never replays started or terminal work.
3. Cancellation remains a request until owned work and cleanup drain. Admission capacity releases only after all owned close hooks have been attempted.
4. Cleanup failures never replace an existing work exception. Ctrl-C sets the worker stop signal before quota completion, and job/quota outcomes both record interruption.
5. Terminal state, report bytes and report-part identities commit together; failed persistence retains the established inspect/restart recovery path. Owner denial preserves the other owner's state and evidence.

## Fault matrix and lifecycle coverage

| Boundary | Injected event | Expected durable state and recovery | Evidence |
|---|---|---|---|
| Submit quota/job COMMIT | Before/after transaction interruption | Same-key recovery without extra units | Existing acceptance fault tests; repeat in audit group. |
| Claim/intent/attempt | Process exit before or after persistence | Queued work can start; claimed work becomes interrupted; persisted attempt remains uncertain | Existing process-kill tests plus public CLI restart checks. |
| Evaluation | Cancel, expired budget, Ctrl-C | Retain original stop reason and uncertain attempt | New CLI regressions and running MCP tests. |
| Evaluator close | Runtime error while unwinding each stop reason | Pool still drains; original reason remains primary | New cleanup regression matrix. |
| Pooled client close | One or several close failures | Every owned close is attempted; primary work error survives | New cleanup matrix and existing resource-stack tests. |
| Case result COMMIT | Failure before/after COMMIT | Only committed results appear; no replay | Existing case fault tests plus public restart inspection. |
| Report/state/part COMMIT | Failure before/after COMMIT | Prior running state or complete terminal evidence | Existing report fault tests plus public restart inspection. |
| Quota finalization | Missing record or write failure | Worker pauses before another claim; preserve storage for repair | Existing accounting tests. |
| Owner check and repeated requests | Other principal, changed input, repeated cancel/result | Denial or unchanged accepted identity and terminal report | CLI/MCP audit paths and existing concurrent submission tests. |

No new file replacement, deletion, synchronization or filesystem rollback protocol is introduced. Existing archive capture/import/retention tests cover those unchanged paths. Physical power loss and volume durability remain outside local fault injection. Process-kill recovery can't close resources in the dead process; kernel ownership and restarted durable-state inspection provide its recovery evidence.

## Verification and delivery

Use locked Python 3.11 and 3.12, Ruff, Pyright, focused lifecycle/provider/admission tests and the full offline suite. Retain baseline and failure logs outside the repository. Serialize expensive runs and remove only successful task-owned fixture directories. Run final local Qodo review, then exact-head hosted Qodo and required CI before permitted merge. Report the known host PowerShell assembly failures separately if they recur. Installed-wheel checks must identify the tested revision and run outside the source tree.
