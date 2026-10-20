# Policy Packs And The Source Catalog

Inspect available policies before making a model request:

```bash
policynim policies list
policynim policies show SEC-AUTH-001
policynim policies validate --format json
```

Each command supports `--format text|json`. In a source checkout, prefix commands
with `uv run`. These commands read source files without requiring `init`, model
credentials, or network access. Invalid sources return exit code 1; JSON failures
include `status: error` and a location or recovery message.

`list` reports authored/effective policy counts and a deterministic catalog digest.
`show` includes the policy text, source path, declared provenance, and a SHA-256
revision of the complete Markdown file. `validate` checks structure and declared
metadata; it doesn't verify upstream claims, certify compliance, or evaluate model
quality. Use [inventory](policy-inventory.md) for the actual local database counts.

## Explicit Pack Membership

A corpus with `pack.toml` loads only the declared Markdown members. Unlisted
README files, templates, and author notes aren't policies. List and validation
reports expose unselected files as `unlisted_markdown`; they aren't silently added.
When copying `TEMPLATE.md` to create a new policy, register the new ID and path in
the manifest before ingestion. The bundled
`policynim.baseline@0.2.0` manifest selects 15 policies, including nine maintained
security policies with pinned source metadata. Existing IDs remain stable. See
the [security coverage matrix](security-mcp-coverage.md) for changes and notices.
This source-pack version doesn't change the software/package version or establish
an indexed/deployed inventory.

A local pack can use this shape:

```toml
schema_version = 1
pack_id = "team.backend"
version = "1.0.0"

[[policies]]
policy_id = "TEAM-LOG-001"
path = "backend/logging.md"
requirement_level = "recommended"
applicability = ["backend", "background-jobs"]
grounding_kind = "adaptation"

[[policies.sources]]
title = "Example logging reference"
url = "https://example.com/logging"
revision = "reviewed-revision"
sections = ["2.1"]
retrieved_on = 2026-09-01
license = "CC-BY-4.0"
attribution = "Example Authors"
change_notice = "Adapted to require request identifiers."
```

The reference values above illustrate the schema. Replace them with reviewed
source facts and applicable notices; omit unknown optional values instead of
inventing them. The member's Markdown frontmatter must use the same `policy_id`.
Pack versions use three numeric components. `pack_id` uses lowercase letters,
numbers, dots, underscores, and hyphens.

`requirement_level` accepts `required`, `recommended`, `informational`, or
`unknown`. `grounding_kind` accepts `upstream_requirement`, `adaptation`,
`project_recommendation`, or `legacy_unknown`. These are declarations by the pack
author, not verified upstream authority. Missing revisions, retrieval dates,
licenses, and attribution remain explicit unknowns in JSON.

Set `POLICYNIM_CORPUS_DIR` to the pack directory and validate it before ingestion.
Manifest paths must stay inside that directory, including after symlink
resolution. Duplicate IDs/files, unknown settings, unsupported schemas, bad source
records, and missing members fail validation. Manifest-backed Markdown accepts
`policy_id`, `title`, `doc_type`, `domain`, `tags`, `grounded_in`, and
`runtime_rules` frontmatter. Unsupported keys such as `enabled` fail rather than
silently changing policy meaning.

## Revisions And Compatibility

Policy revisions hash the complete source bytes. Editing text changes the
revision even when the policy ID and headings stay the same. Identical pack
inputs resolve to the same catalog digest across directory locations. Declared
provenance changes also affect the catalog digest.

Directories without a manifest retain recursive Markdown loading, with
`TEMPLATE.md` excluded. Their policies receive content digests while pack identity
and undeclared upstream provenance stay unknown. Existing `grounded_in` references
remain available without being promoted to verified, versioned source records.

Ingestion compiles the catalog from the same loaded source snapshot before
embedding and prints its digest in the completion summary. A catalog compilation
failure blocks provider calls and artifact publication.

New indexes persist provenance in the existing SQLite metadata table. Dump and
search read it back without requiring a schema migration. Older indexes without
that metadata remain readable when their embedding identity is compatible;
missing provenance stays `null`. Malformed stored provenance fails identity
validation before retrieval can contact the embedding provider.

The catalog is source inspection. Its `indexed_count` stays `null` because reading
source files doesn't prove which generation a service serves. Current source
entries are active; reviewed additions, exclusions, and replacements belong to
profile composition work. Complete generation activation, reader pinning, and
rollback belong to publication work. This manifest format doesn't implement those
operations or fetch remote packs.

For baseline plus local additions, use a [pinned profile](policy-profiles.md).
`POLICYNIM_CORPUS_DIR` keeps its replacement-directory behavior.
