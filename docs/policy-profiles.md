# Compose pinned local policy packs

A profile combines explicitly selected, manifest-owned local packs. Use it to
keep a maintained public baseline while adding project policies or reviewing
specific exclusions and replacements. Profile commands run without credentials,
model requests, Git commits, or pushes.

The shared hosted service uses one operator-selected public profile. Private
custom packs belong in local or self-hosted installations. There's no hosted
private upload endpoint or mutation tool.

## Create and inspect a profile

1. Obtain local copies of the intended public pack release and your custom pack.
   Each needs a [pack.toml manifest](policy-packs.md). Packs aren't downloaded or
   refreshed automatically.
2. Pin only the directories you intend to select:

   ```bash
   policynim profile init team-profile.toml --id team \
     --pack ./public-baseline --pack ./team-policies
   policynim profile validate team-profile.toml
   policynim profile preview team-profile.toml --format json
   ```

   Without `--pack`, initialization pins the currently bundled baseline.
3. Select the profile for this local/self-hosted process:

   ```bash
   export POLICYNIM_PROFILE="$PWD/team-profile.toml"
   policynim policies list --format json
   policynim inventory --format json
   ```

   Leave `POLICYNIM_CORPUS_DIR` unset when selecting a profile. Without a profile,
   that setting keeps its existing behavior: the directory replaces the bundled
   corpus. A relative profile path resolves from the process working directory;
   relative pack paths resolve from the profile's directory.

Commit the profile and selected pack sources through your normal review workflow.
Keep private packs out of public Git repositories. Only manifest members from
selected directories are read as policies; neighboring packs aren't discovered.

## Add, replace, or exclude policies

All selected policies start active. An addition needs a unique policy ID across
all selected packs. There's no filesystem-order or model-selected precedence.

Edit the generated profile to add decisions. `profile preview` shows each policy's
`target_digest`, binding text, path, and declared provenance; copy that reviewed digest to
`expected_digest`. The following decision fields are required:

| Decision | Fields |
| --- | --- |
| `[[exclusions]]` | `target_id`, `expected_digest`, `reason` |
| `[[replacements]]` | `target_id`, `expected_digest`, `replacement_id`, `reason` |
| `[[conflicts]]` | `left`, `right`, `reason` |

For a replacement, the replacement policy must already belong to a selected pack
and have a different ID. Its target becomes `replaced`. An excluded target becomes
`excluded`. Both remain visible with their source text, reason, and provenance in
preview and `policies show`; neither reaches retrieval, compilation, or derived
runtime rules after ingestion. `inventory` lists the decisions separately from
effective policy counts.

Validation rejects unknown or stale targets, repeated IDs, multiple replacements,
exclusion/replacement contradictions, and replacement chains or cycles. A
replacement must remain active. At least one effective policy must remain.

A declared conflict names two known policies that mustn't both stay effective.
Validation also rejects different runtime effects for identical normalized matcher
lists; host patterns use the runtime's case-insensitive comparison.
These are structural checks: it doesn't infer arbitrary prose compatibility,
regex overlap, or semantic equivalence. Operators still review policy meaning.
Unsupported Markdown frontmatter such as `enabled` doesn't disable a policy.

## Review a local release update

1. Replace the explicitly selected local pack copy with the intended release.
   Old pins will reject the changed source until it's reviewed.
2. Inspect the offline diff:

   ```bash
   policynim profile diff team-profile.toml --format json
   ```

   The result lists `added`, `changed`, `removed`, `changed_packs`,
   `invalidated_decisions`, and any blocking `validation_error`. The JSON
   `current_policies` snapshot includes the new text, provenance, and `target_digest`
   for reviewing affected decisions even while old pins block preview. Changes include
   declared pack/provenance metadata. Missing or invalid selected packs fail
   instead of producing an applicable diff.
3. Review affected overrides. Edit their target digest only after reviewing the
   new target text and provenance, or remove/change the decision. Rerun validation through
   `profile diff`; stale target digests block updates.
4. Copy the current `review_digest` to the explicit update command:

   ```text
   policynim profile update team-profile.toml --expect-digest <review_digest>
   ```

   The token binds the profile text and proposed source snapshot. Changed inputs
   require a new diff. Update changes source pins only; it doesn't activate or
   rebuild an index. Operator notes and decision formatting are preserved. Keep
   your decisions and comments in valid TOML; the updater edits parsed pin fields.
5. Preview again, then build into new index/rules destinations through the normal
   ingestion workflow. The existing ingestion destination guards still apply.
   Atomic whole-generation publication and rollback are tracked in #93.

Pack paths can point outside the profile directory when explicitly selected, but
manifest member paths must stay inside their pack. Symlink escape, remote schemes,
and duplicate directory aliases are rejected. Initialization won't overwrite an
existing profile; failed or interrupted updates preserve the old profile and
clean temporary files. Supported CLI writers share an advisory lock; external
editors must finish their edits before an update because they don't honor that lock.
Updates use a private staging directory and preserve POSIX file permissions.
POSIX publication syncs the file and parent directory; a post-publication sync
failure returns a warning because the pins have already changed. Windows uses
atomic replacement without claiming power-loss durability. Keep profile directories
trusted: these commands don't isolate files from other processes running as the
same OS user, which can also edit the profile directly.
Source-only preview doesn't claim an indexed generation.

## Provider and privacy boundary

Inspection, validation, diffs, and pin updates don't send policy text to models.
Ingestion sends effective policy chunks to the configured NVIDIA embedding
service. Search/reranking and guidance/preflight operations can send selected
policy excerpts and request context to the configured NVIDIA services. Optional
evaluation/observability integrations have their own export behavior. Selecting a
private custom policy doesn't keep that selected text local during those model
operations. Unselected pack text isn't loaded into the effective profile.

Repinning doesn't certify upstream licensing, policy correctness, or regulatory
compliance. Declared provenance and unknown values remain inspectable.
