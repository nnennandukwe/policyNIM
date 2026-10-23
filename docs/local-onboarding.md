# Set up your policies and storage in a browser

Run `policynim setup` on the machine running your local or self-hosted
PolicyNIM process. It opens a private loopback URL. Keep that launch URL private
and stop the server with Ctrl+C after saving configuration.

```bash
policynim setup
policynim setup --no-open --port 8765 --config ./my-policynim.env
```

The server binds `127.0.0.1` only. For a self-hosted server, use an SSH tunnel to
its chosen port; the database connection originates from that server.
`localhost` always refers to the machine running PolicyNIM. An arbitrary public
page can't use the setup API: Host/Origin checks and a per-launch authorization
token protect requests, and external scripts/assets aren't loaded.

## Complete the workflow

1. **Choose deployment.** Local/self-hosted installations can select private
   sources and external storage. The shared hosted service keeps its maintained
   public profile; connect through `policynim quickstart --target hosted-mcp`.
2. **Preview sources.** Select the baseline, topic packs plus team packs, an
   existing pinned profile, or a custom directory. Inspect policy text, IDs,
   effective states, revisions, and replacement reasons. Structural conflicts
   and stale pins block advancement.
3. **Select storage.** Keep SQLite or connect Qdrant/pgvector. Read-only connectivity
   checks don't create a collection/table. A successful connection has its own
   status; generation validation happens during build.
4. **Build after consent.** Review the data flow and approve provider work.
   Effective policy text and task context can reach configured NVIDIA services.
   External storage receives identifiers, domains, generation bindings, and vectors.
   Complete sources, runtime rules, and the external replica are verified before
   activation. A failed preparation keeps the prior active generation.
5. **Verify and save.** Enter a task, inspect complete selected policy bodies and
   citations, and save only after a source-backed result. Insufficient evidence
   blocks completion and gives a recovery action. The UI prints the exact config
   path and commands to inspect storage and connect an MCP client.

Saving creates a private config file without overwriting an existing file. If the
usual config path already exists, the default destination adds `.setup`; choose
`--config NEW-PATH` when needed. Topic composition also creates a new pinned profile
beside that config. Other files and manual edits stay unchanged. On POSIX, new
configuration/profile files have owner-only permissions. Postcommit directory-sync
or cleanup failures are returned as warnings.

Setup config files start with `# policynim-literal-values-v1`. Keep that header:
PolicyNIM reads those values literally, including `${...}` in passwords and paths.
Existing unmarked env files retain their normal variable expansion behavior.

Use the printed `POLICYNIM_CONFIG_FILE` command for later CLI/MCP processes.
Process environment variables still override env-file settings. The config follows
the generation root's active pointer, so reviewed future ingestion can publish an
update normally. Source authorship, applicability, and regulatory compliance need
their own review.

The setup session runs in memory. A browser disconnect doesn't cancel a completed
build; reopen its launch URL and re-enter the same choices to continue. Source or
credential changes invalidate the preview digest. After the setup process stops,
saved config and generation state support normal restart; an unsaved session must
be reviewed and verified again.

See [backend settings and recovery](vector-storage.md) and
[the policy lifecycle](policy-lifecycle.md).

Setup preserves the effective provider timeouts, retry limits, embedding batch size, and default retrieval count in its new config. Reloading that config keeps the request limits used during verification.

Enter remote database credentials for the selected backend. Setup drops hidden or inherited database credentials when you change storage; only the configured NVIDIA key can be reused by leaving its field blank.
