# Runtime dependency audit

The retained [audit inputs](evidence/runtime-dependency-audit/inputs.json) bind the
project and universal lock bytes to two requirement exports. pip-audit 2.10.1
queried PyPI's advisory service on October 20, 2026, using Python 3.11 on macOS.
This is a known-advisory inventory, not an exploitation test or proof of safety.
Other platforms and Python versions weren't audited by these receipts.

The [standard runtime](evidence/runtime-dependency-audit/standard.json) contains
79 selected packages and returned no known advisories. The
[supported-extra runtime](evidence/runtime-dependency-audit/all-extras.json)
contains 162 packages and retains eight Pillow advisories in one package.
Re-export requirements and rerun the audit when the lock changes; an earlier
receipt can't establish a later lock's result. Default CI stays offline.

## Optional Pillow exposure

`nvidia-guardrails` brings FastEmbed, which requires Pillow below 13. Pillow 12.3
still has the eight image/PDF parsing and writing advisories listed in the raw
receipt. Upstream fixes them in 13.0.0; forcing that version would violate the
selected FastEmbed dependency contract. Representative upstream records cover
[TIFF pixel disclosure](https://github.com/python-pillow/Pillow/security/advisories/GHSA-q45j-58j2-45rg)
and [PDF parser recursion](https://github.com/python-pillow/Pillow/security/advisories/GHSA-p4wg-p73j-c528).

PolicyNIM's exposed requests carry text. Its Guardrails wrapper serializes a typed
preflight draft to JSON and checks that assistant text through output rails.
PolicyNIM doesn't import Pillow, open image files, decode pixels, append PDFs,
or select FastEmbed image models. The reviewed application paths therefore don't
expose the affected operations. This is a source-based reachability assessment;
it doesn't label Pillow patched or certify every upstream extension/configuration.
Don't add image processing or arbitrary Guardrails configuration without reviewing
these advisories again. The standard hosted install doesn't include this extra.

## External tools

Phoenix UI and the upstream NeMo launcher run outside the main environment as
described in [installation profiles](runtime-install-profiles.md). These runtime
audit receipts don't cover those separate environments. A separate audit of the
selected Phoenix 20.23.0 tool environment found no known advisories in its 151
installed packages on Python 3.11/macOS; that result doesn't cover other tool
versions or platforms. The launcher's LeptonAI
AnyIO ceiling remains an upstream constraint; isolation doesn't patch it.
