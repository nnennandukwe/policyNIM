# Runtime and external evaluation tools

PolicyNIM's main environment includes the NVIDIA adapters, MCP server, policy
tools, evaluation runner, and Phoenix publication client. The optional Phoenix UI
and upstream NeMo launcher run in separate environments. NVIDIA Guardrails and
evaluation packages use the main universal lock.

## Install PolicyNIM

For source development:

```bash
uv sync --locked --group test --group dev
```

The supported NVIDIA extras can share the same locked environment:

```bash
uv sync --locked --all-extras --group test --group dev
```

These extras are `nvidia-guardrails` and `nvidia-eval`. They don't enable model
calls in offline CI or turn an internal integration into an advertised MCP tool.
The normal NVIDIA embedding, reranking and chat adapters remain part of the main
install.

The fresh installed-package verifier also uses the locked `verification` group
for its `packaging` parser. CI explicitly includes that group in its requirements
export; it isn't an application runtime dependency.

## Run evaluations with or without Phoenix

Install the optional Phoenix UI command independently:

```bash
uv tool install --python 3.11 arize-phoenix==20.23.0
uv tool dir --bin
```

Put the reported bin directory on PATH. `policynim eval` launches `phoenix serve`,
waits up to 30 seconds for local startup, and publishes its completed result
through the Phoenix client. Annotation writes wait up to five seconds for queued
spans to appear; other endpoint errors still propagate. Republishing a completed
report reads back duplicate spans from the selected project and checks their
identifiers and report content before skipping them. Missing spans are submitted;
conflicting or uncertain duplicates fail. Existing publication timestamps stay intact. Its existing workspace and port settings still apply. Back up saved UI
data before upgrading the upstream server.

For JSON and HTML reports without that server, use:

```bash
policynim eval --headless
```

This command doesn't start Phoenix or contact NVIDIA in its default offline mode.
The synthetic fixture score isn't live retrieval or semantic-quality evidence.

## Migrate the former launcher extra

Version 0.2 removes `nvidia-eval-launcher` from PolicyNIM's extras. Its upstream
launcher installs LeptonAI, whose AnyIO ceiling conflicts with the patched main
runtime. PolicyNIM doesn't invoke that launcher as an application backend.
Remove `--extra nvidia-eval-launcher` from PolicyNIM setup commands.

If a separate research workflow needs the upstream launcher, keep it in a separate
tool environment and review its dependency advisories there. Isolation doesn't
patch that tool's dependencies. It isn't bundled or served by PolicyNIM, and its
installation doesn't establish a verified live benchmark.
