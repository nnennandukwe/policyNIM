# Bootstrap store seam

The public seam is `bootstrap_bundle(bundle, expected_digest, source_settings=None)`. It shares archive parsing, source agreement, staged preparation, inspection, and pointer commit with explicit import. Explicit import keeps its existing activation behavior.

A valid existing root takes the coordinated validation path without reading or matching the new image seed, changing its pointer, or pruning retained objects. An absent root validates the trusted seed and its optional packaged source agreement before creating state, then claims root creation with exclusive mkdir and retains its directory identity. Writer admission rechecks that identity before initializing ownership and again before the conditional operation. If another supported publisher has committed an active generation, validate and preserve its selection. If no pointer exists, only this invocation's fresh root claim permits publication. Existing unowned, incomplete, damaged, or incompatible roots fail with preserved recovery data.

| Witness | Boundary and required result |
| --- | --- |
| Fresh seed | Actual public bootstrap imports a checked ZIP without providers; pin reads sealed chunks/rules. |
| Invalid seed/source | Wrong checksum, malformed archive, or source mismatch fails before creating the root. |
| Restart | A different seed or moved image sources can't replace an existing valid selection. Pointer and retained object bytes stay unchanged; no prune. |
| Explicit alternate activation | Build another valid generation, activate the older retained ID, bootstrap, and retain that chosen pointer and both IDs. |
| Unowned/incomplete | Existing empty, unowned, owned/no-pointer, partial owner, symlink, or damaged roots reject rather than adopt, resume, or clean up. |
| Concurrent winner | Interpose another supported writer before bootstrap writer admission; preserve its valid pointer. Concurrent bootstrap may refuse an in-progress root, but it can't overwrite a winner. |
| Root identity change | Replace the claimed directory before initialization; reject before creating ownership in the replacement. |
| Precommit interruption | Inject an existing transaction failpoint before pointer replacement; no served partial state. A later bootstrap refuses the owned incomplete root; explicit recovery remains available. |
| Postcommit interruption | Existing publication warning boundary retains committed pointer. Restart validates and preserves it. |
| Atomic commit refusal | Mutation during final inspection or before commit still uses the existing activation token checks; bootstrap doesn't bypass them. |

Use real temporary filesystem/SQLite/public store paths. Fault callbacks model named boundary interruption and supported writer interleaving. Don't claim arbitrary external filesystem mutation is a serialized writer or that every concurrent startup succeeds. Run native Windows generation tests and the full offline suite. Hosted process/container/volume startup remains root's separate integration proof.


## Filesystem trust boundary

The root and its parent directories must stay under service-controlled ownership
and permissions. Directory identity checks prevent adopting a replacement observed
at initialization or writer admission. They aren't a filesystem sandbox. A
same-UID actor that independently renames roots after admission can redirect
path-based staging and object writes before later validation refuses activation.
A retained witness reproduces that boundary in a mode-0700 parent with the same
OS user: bootstrap refuses activation and preserves the other store's pointer,
but an orphan object reaches that other store. It doesn't establish a cross-UID
exploit. Supported publisher interleaving remains serialized and preserves the
winner; independent pathname mutation falls outside that protocol. Hosted volume
ownership and parent permissions need separate deployment readback.
