# Offline release evidence

A successful source test run doesn't identify a published download, a production
service or a coding-client session. These tools collect scoped evidence for one
selected release. They don't publish, deploy, execute client setup commands or make
network requests.

## Select the subject and gates

Copy [the selection example](examples/release-evidence-selection.example.json) and
replace every placeholder identity. The [selection schema](release-evidence-selection.schema.json)
records the structural input contract; runtime validation also enforces context and cross-field identity rules. Select the exact source revision, dependency-lock digest,
generation ID, catalog digest, public bundle digest and manifest checksum. SHA-256
fields use the prefixes shown in the example; the manifest checksum is bare hex.

Each required check has a unique ID and scope. Execution checks need nonempty named
cases and exact context. Client context includes client/version, server SDK, protocol,
transport and auth mode. Deployment context includes provider project, environment,
service, deployment and application source. Select every client/protocol/transport
combination that's required; one SDK receipt can't substitute for a coding-client run.

The artifact check recomputes local download sizes and checksums against the selected
manifest. The provenance check consumes the existing attestation verifier's receipt
and requires its repository, workflow, source and complete verified subject set to
match those bytes. Keep the report outside the release asset directory.

## Collect existing receipts

Use the existing #131 artifact verifiers and #145 attestation verifier first. This
collector doesn't add another build/install runner. Create the chosen output directory,
then run from the checkout with locked dependencies already synced:

```bash
uv run --no-sync python scripts/collect_release_evidence.py \
  --selection /path/to/selection.json \
  --artifact-dir /path/to/release-assets \
  --provenance-receipt /path/to/attestation-verification.json \
  --execution-receipt /path/to/client-receipt.json \
  --client-declaration /path/to/client-declaration.json \
  --output /path/to/release-evidence/report.json
```

Repeat `--execution-receipt` for selected checks. The
[execution example](examples/release-execution-receipt.example.json) starts with
`not_run`; it isn't a passing receipt. A failed receipt can retain partial observed
identity, named case outcomes, exit status and a bounded failure class. Missing
observed fields stay unavailable. Expected values never fill them in.

The [client declaration example](examples/client-declaration.example.json) is entered
operator information. Schema validation doesn't prove a client ran. Declarations are
always `declared_only` and can't satisfy execution gates. No command text is accepted
or executed. OAuth evidence remains missing until an actual issuer/client flow is
configured; beta-key evidence can't satisfy it.

## Read the result honestly

`evidence_consistent` means the selected checks passed consistency validation. Artifact
bytes are recomputed locally; supplied execution/provenance receipts retain their
trust labels and `producer_authenticated=false`. The collector doesn't authenticate a
remote producer, verify signatures itself or independently witness a client/deployment
run. Obtain and authenticate those receipts through the existing trusted CI/verifier
or actual acceptance process before using them for promotion. A self-authored receipt
isn't independent execution evidence.

The report retains the expected selection, observed identities, scoped check results,
receipt checksums, declarations and sanitized input failures. Required missing,
failed, skipped, timed-out, nonzero, mismatched or incomplete results hold the gate.
An all-skipped or empty execution set can't pass. Separate artifact consistency,
published-channel installation, actual client acceptance, deployment match and rollback.
This report alone doesn't complete #104 or authorize a production claim.

Exit codes are `0` for selected evidence consistency, `1` for a held gate and `2` for
invalid selection or failed output. Inputs/reports are limited to 1 MiB, with at most
32 receipt/declaration inputs and 64 cases per check. Artifact sets are limited to 64
entries, 2 GiB per artifact and 8 GiB total. Symlinks and special-file inputs are rejected.
References must use HTTPS without credentials, query strings or fragments. Receipts
contain identity/status fields rather than query text, policy sources or raw diagnostics.

## Report replacement and recovery

A report is written to an owned temporary sibling, flushed, synced and closed before
`os.replace` commits it. A failed write preserves the prior report. A failed evidence
gate still writes a complete current report with `decision=hold`. Cleanup removes only
the invocation's staged file. If cleanup also fails, inspect the selected directory's
`.policynim-evidence-*.tmp` files before retrying; don't remove another writer's files.

Use a trusted local directory and one coordinated writer. This doesn't serialize
unrelated concurrent writers or promise power-loss durability. If interrupted after
replacement, inspect the complete current report before retrying; interruption doesn't
mean the old report was restored.

## Versioned release notes and integration points

```bash
uv run --no-sync python scripts/check_release_notes.py \
  --repo-root . --output /path/to/release-notes.md
```

The checker reads the actual project version and requires one matching nonempty
CHANGELOG section plus one Unreleased section. Empty/comment-only/placeholder bodies
fail before rendering or replacing earlier notes. Candidate feature status remains
in the authored body; the checker doesn't turn pending integration into a shipped claim.

For later workflow integration, root can call the notes checker after resolving the
release version and before draft creation. Call the collector after existing manifest
and attestation verification, storing its output outside `release-assets`. Hosted and
actual-client jobs should retain their own normalized receipts, then use an explicitly
selected promotion gate. No existing workflow is changed by these tools.
