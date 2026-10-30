# Release and rollback

## The manifest

A release is a folder of files. Its manifest lists every file with its SHA-256 and size, plus a tree hash, the component, the version and the decision it ships under:

```json
{
  "schema": "engineering-hq.release-manifest.v1",
  "component": "thermostat-firmware",
  "version": "1.4.0",
  "decision_id": "DEC-001",
  "files": [
    { "path": "CHANGELOG.md", "sha256": "44bd9e75...7225", "bytes": 151 },
    { "path": "build-info.json", "sha256": "43f785e8...7e91", "bytes": 168 },
    { "path": "firmware.hex", "sha256": "6b5d34d1...0ea9", "bytes": 716 }
  ],
  "tree_sha256": "0f95f22d...b956",
  "manifest_sha256": "72aa6d85...a442"
}
```

**Reproducible.** There are no timestamps and no machine paths in it, and files are sorted by their exact relative path. Building the manifest twice from the same files, on any machine, gives the same `manifest_sha256`. I sort on the path string rather than the platform's path ordering because Windows compares paths case-insensitively, and that once gave me a different order there than on Linux. A test covers it now.

**When and who are recorded elsewhere.** The time of an activation and the approver behind it go into the evidence register and `history.jsonl`, not into the manifest.

## Staging and activation

A `release` request is protected. Once it's approved and execution is on, the supervisor runs these steps:
1. **Locate the source.** It resolves the payload's `source` folder, and refuses anything outside the configured `releases.sources` folder (no `../`).
2. **Build and copy.** It builds the manifest, copies the files to `releases/<version>/`, writes `MANIFEST.json`, and verifies the copy before moving it into place. A version folder that already exists is refused: a changed file is a new version.
3. **Activate.** It verifies the release against its manifest and points `releases/CURRENT` at it. Then it appends an `activate` row to `releases/history.jsonl` (hash-chained, like the registers) with the previous version, the actor and the approval id.
4. **Record evidence.** It adds an evidence row with the manifest as its artifact.

## Rollback

A `rollback` request is protected too. The supervisor:
1. **Finds the target.** It looks up the activation of the current version and reads its `previous`.
2. **Verifies the target.** It checks that previous release against its manifest. If anything changed, the rollback is refused, and the current release stays as it is. Restoring a release that no longer matches what was approved would be worse than staying put.
3. **Switches and records.** It points `CURRENT` back, appends a `rollback` row, and records evidence.

Rolling back past the first release is refused ("no earlier release recorded").

## What the tests cover

`supervisor/tests/test_manifest.py` and `test_supervisor.py`:
- the manifest is the same from a copy of the same files;
- changed, missing and extra files are detected, and so is a changed manifest;
- stage, activate, a second release, then rollback;
- rollback past the first release is refused;
- a damaged previous release blocks the rollback, and `CURRENT` doesn't move;
- a damaged release isn't activated;
- a version folder is never overwritten;
- a release source outside the allowed folder fails the request.

## Backups

`scripts/backup.ps1` copies the registers and `history.jsonl` into a timestamped folder, writes `SHA256SUMS`, and runs `check.py --backup`, which verifies both the hashes and every chain in the copy. A copy that doesn't verify fails the backup. Run it daily and before changing the config. A matching name and size isn't a verified backup.
