# Simulation vault

Engineering produces a lot of small simulations and prototypes: models, calculators, demo servers. The vault is where the ones worth keeping are preserved, so that the version someone approved is the version that runs, months later.

## The one rule

**A simulation that runs is not thereby validated.** Every staged candidate keeps three separate results:
- `runs`: did it start and do what it says;
- `tests`: do its own tests pass;
- `validated`: has it been checked against reality.

Each is `PASS`, `FAIL` or `NOT_TESTED`. Collapsing them into one green tick is how a demo becomes a "verified model".

## Layout

```
vault/
  snapshots/<ID>/<version>/   source/ + MANIFEST.json. Read-only. Never run from here, never edited.
  staging/                    candidates waiting for a decision, with their three results
  approvals/                  one record per approval, naming the exact snapshot hash approved
  active/                     the approved version of each entry
  archive/                    earlier approved versions, still restorable
  runtime/<ID>/<version>/app  restored working copies; the only place anything runs
  launcher/                   vault_cli.py and the launch allowlist
```

Entry ids look like `SIM-001`.

## Lifecycle

1. **Preserve.**
   ```bash
   python vault/launcher/vault_cli.py snapshot SIM-001 0.1 path/to/working-copy
   ```
   This copies the folder, writes a manifest, marks every file read-only, and checks the copy. An existing version is never overwritten.
2. **Stage.**
   ```bash
   python vault/launcher/vault_cli.py stage SIM-001 0.1 --runs PASS --tests PASS --validated NOT_TESTED
   ```
   This records the three results and the snapshot hash.
3. **Ask.** An agent files a `promote_snapshot` request whose payload names the entry, the version and the snapshot hash. It's a protected action.
4. **Approve.** An approver approves it at the supervisor console, exactly as for a release.
5. **Promote.** The supervisor re-verifies the snapshot. It checks that the approved hash is the preserved hash and the staged hash. Then it writes the approval record, archives the previously active version, and makes this one active.
6. **Run.**
   ```bash
   python vault/launcher/vault_cli.py restore SIM-001 0.1
   ```
   This verifies the snapshot and restores a fresh working copy under `runtime/`. The snapshot is never written to.

`vault_cli.py` deliberately has no `promote` command. Promotion only happens through an approved request.

## Launch allowlist

`launcher/launch_definitions.example.json` shows the format for running preserved entries as local services. Each entry lists only:
- id and version;
- entry script;
- port;
- health path and marker.

The command is built from those fields. Nothing in a request is executed, and the port must be free or already held by a healthy instance of the same entry.

## What is and isn't claimed

- **Accidents, not adversaries.** Read-only flags and manifests guard against accidental change. They aren't tamper-proof against someone with write access to the disk.
- **Same machine.** The vault on its own is not a backup. Copies on a second disk in the same machine protect against one disk failing, not against losing the machine.
