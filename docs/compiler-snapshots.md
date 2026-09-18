# Compiler snapshot integrity

Automatic compiler drafts are unreviewed artifacts. Content integrity does not
establish human approval or task correctness; existing execution admission rules
remain in place.

New format 0.2 snapshots retain a local copy of the source trace and any previous
snapshot marker outside the active TaskPack tree. The manifest records the source
hash and a deterministic relative-path, size and SHA-256 tree digest. Validation
rejects changed, added or removed files, source mismatches, and symlink or special
file entries. Freezing checks for observed changes before and after copying.

The web console reuses an existing marker only when the frozen artifact, current
source tree, source trace and compiler settings agree. A stale marker requires a
fresh compilation/version; it is not silently refreshed. Existing snapshots are
preserved. Failed partial snapshots remain without a valid completion manifest;
they are not usable execution inputs and may require manual storage cleanup.

Format 0.1 snapshots retain their historical digest algorithm and require their
original source file. Legacy markers without the new source-tree binding cannot
be reused automatically. These snapshots contain absolute task paths: the local
source copy does not make the entire package relocatable.

This is consistency checking for a local workspace, not a signed authenticity
scheme or a defense against a hostile concurrent filesystem writer. No new
experiment conditions or automatic approvals are introduced.
