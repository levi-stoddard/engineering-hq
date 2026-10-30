"""Vault tools: preserve, verify, stage and restore. Promotion into the active library is not here.

    python vault/launcher/vault_cli.py --vault vault snapshot SIM-001 0.1 path/to/working-copy
    python vault/launcher/vault_cli.py --vault vault verify   SIM-001 0.1
    python vault/launcher/vault_cli.py --vault vault stage    SIM-001 0.1 --runs PASS --tests PASS --validated NOT_TESTED
    python vault/launcher/vault_cli.py --vault vault restore  SIM-001 0.1

Promoting a staged version is a protected action: an agent files a `promote_snapshot` request naming
the exact snapshot hash, an approver approves it at the supervisor console, and the supervisor
promotes it. That is why this tool has no promote command.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "supervisor"))

from manifest import ManifestError  # noqa: E402
from vault import RESULTS, Vault, VaultError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", default="vault")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "verify", "stage", "restore"):
        sub = commands.add_parser(name)
        sub.add_argument("entry_id")
        sub.add_argument("version")
        if name == "snapshot":
            sub.add_argument("source")
        if name == "stage":
            for result in ("--runs", "--tests", "--validated"):
                sub.add_argument(result, choices=sorted(RESULTS), required=True)
            sub.add_argument("--notes", default="")
    args = parser.parse_args()

    vault = Vault(Path(args.vault))
    vault.ensure_layout()
    try:
        if args.command == "snapshot":
            manifest = vault.snapshot(args.entry_id, args.version, Path(args.source))
            print(f"Preserved {args.entry_id} {args.version}: {len(manifest['files'])} files, snapshot sha256 {manifest['manifest_sha256']}")
        elif args.command == "verify":
            problems = vault.verify(args.entry_id, args.version)
            for problem in problems:
                print(f"FAIL {problem}")
            print("Snapshot matches its manifest." if not problems else f"{len(problems)} problem(s).")
            return 1 if problems else 0
        elif args.command == "stage":
            record = vault.stage(args.entry_id, args.version, args.runs, args.tests, args.validated, args.notes)
            print(f"Staged {args.entry_id} {args.version} (snapshot sha256 {record['snapshot_sha256']}). "
                  "File a promote_snapshot request naming that hash to ask for approval.")
        elif args.command == "restore":
            print(f"Working copy: {vault.restore(args.entry_id, args.version)}")
    except (VaultError, ManifestError) as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
