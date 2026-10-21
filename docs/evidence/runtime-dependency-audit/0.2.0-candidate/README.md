# 0.2.0 candidate dependency audit

This snapshot records an actual pip-audit 2.10.1/PyPI run on Python 3.11.13 and macOS. The [inputs](inputs.json) retain the producing source revision, lock/project hashes, exported requirements hashes and raw receipt hashes. The lock and project bytes also match prepared metadata source `0e455469`; adopting these files didn't rerun or relabel the audit.

The [standard profile](standard.json) contains 79 packages and no reported affected package. The [supported-extra profile](all-extras.json) contains 162 packages and eight reported advisories for Pillow 12.3.0. Standard exited 0; extras exited 1. Neither receipt has skipped-package reasons. These are advisory-service observations, not exploit tests or evidence for every platform.

Both raw JSON hashes match the earlier audit because the dependency identities and service results are unchanged. The retained requirements exports have new hashes because their command headers changed. The 0.2.0 metadata lock was regenerated without a dependency upgrade; this snapshot doesn't change any pin, waive an advisory or claim the optional Pillow paths are patched.

The earlier [input record](../inputs.json), [standard receipt](../standard.json), [extra receipt](../all-extras.json) and [separate Phoenix tool receipt](../phoenix-tool.json) remain unchanged. The separate tool environment and production reachability need their own evidence. The current snapshot covers the selected candidate requirements only.
