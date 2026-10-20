# Hosted generation continuity

The container currently keeps generations in its image layer. Redeploying that
image loses retained generation IDs, so archived jobs and resource links can't
rely on them. The hosted volume must hold the complete managed generation root.

## Ownership and commit model

The canonical state is the owned root, immutable objects, and its active.json
pointer. GenerationStore owns all writes, locks, leases, validation, and recovery.
The container owns a read-only seed ZIP and trusted checksum. The hosted startup
coordinator owns no database or filesystem transaction of its own.

| Starting state | Action | Ending state |
|---|---|---|
| Target absent | Import verified seed through conditional store operation | Owned root with validated active pointer |
| Valid owned root | Validate active and configured selection | Same selection and retained objects |
| Another supported writer wins | Validate winner under store coordination | Winner preserved |
| Existing unowned, damaged, or interrupted root | Refuse startup | Bytes preserved for operator inspection |
| Import fails before pointer replacement | Refuse startup | Prior state preserved or owned incomplete root retained |
| Pointer replacement succeeds, cleanup warns | Validate committed state | Start with warning |

The commit operation remains GenerationStore's os.replace of active.json. Startup
must never activate the image seed over an existing selection. Restart never
performs automatic source matching against changed image policies: historical
selection is intentional. Initial seed import requires packaged source agreement.
Explicit import/activate/rollback stays an operator action.

## Invariants

Persistent deployments set `POLICYNIM_HOSTED_STATE_MOUNT` to their actual volume
root. Before reading or creating generation state, hosted startup requires that
root to be a mount point and requires generation, authentication, evaluation, and
runtime-evidence paths to resolve beneath it. Missing mounts and escaped paths
fail without writes. The production example and persistent Docker command set
this contract explicitly; the short image test remains ephemeral. Mount-point
detection doesn't replace the required cross-redeploy durability observation.

1. Startup only seeds an absent root and never rewrites a valid active pointer.
2. The conditional first import rechecks state while holding the store writer lock.
3. Existing unowned, corrupt, incompatible, or incomplete state blocks startup.
4. All served objects pass the real store inspector with configured embedding identity.
5. No startup path creates providers, calls NVIDIA, or removes recovery artifacts.
6. Process replacement preserves signals; startup failure exits nonzero with bounded recovery text.

## Fault matrix

| Phase | Boundary | Failure | Required result and recovery |
|---|---|---|---|
| Observe | Root lstat | Permission denied, dangling symlink, non-directory | Refuse; preserve and fix mount/permissions |
| Observe | Owned root/pointer/objects inspection | Missing owner, malformed pointer, altered artifact, embedding mismatch | Refuse; inspect and explicitly restore/import verified state |
| Prepare | Seed/checksum read and parse | Missing, oversized, malformed, wrong checksum | Refuse without creating target |
| Prepare | Source match | Different packaged sources | Refuse without target mutation |
| Coordinate | Root observation to writer admission | Concurrent publisher selects another generation | Preserve winner; validate its active state |
| Import | mkdir, owner writes/link, directories, artifact writes/fsync, object install | Existing store failpoints | Existing GenerationStore transaction proofs apply; startup doesn't retry or clean up |
| Commit | Immediately before active.json replacement | Interruption | No served partial state; restart blocks incomplete root |
| Commit | Immediately after active.json replacement | Interruption | Restart validates committed root and preserves selection |
| Cleanup | Retention or fsync warning | Committed result with warning | Validate and start; warning remains observable |
| Serve | Process exec | Failure | Nonzero startup result; committed generation preserved |

No durability claim extends beyond the volume/filesystem guarantees. Unsupported
actors that mutate owned files outside the store protocol remain corruption,
never a reason to overwrite them. No automatic removal/uninstall is provided.

## Lifecycle evidence required

Focused tests must cover first seed, unchanged restart, explicit alternate
activation, damaged/unowned/incomplete/symlink refusal, wrong seed digest,
concurrent supported initialization, pre/post-commit interruption, compatible
selection, and process arguments/failure. Reuse store failpoint tests rather than
duplicating the import engine. Actual containers must mount a named volume,
initialize once, restart on it, keep an alternate valid selection across restart,
and reject a damaged pointer with networking disabled. Later Railway readback must
verify the mounted path, generation identity, historical resource, and retained
job behavior across redeploy. Until then, hosted durability isn't established.
