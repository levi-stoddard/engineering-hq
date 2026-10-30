"""Read-only health check for an Engineering HQ install. Changes nothing.

    python scripts/check.py --config examples/acme/config.yaml
    python scripts/check.py --config <config> --backup backups/20261030T120000Z

Checks: the config loads (no placeholders); every register chain verifies; every staged release and
vault snapshot matches its manifest; the state file is recent while the supervisor runs; and each
supervised status service answers on 127.0.0.1, refuses a foreign Host header and refuses writes.
With --backup it verifies the copied registers in a backup folder instead.
Exit code 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "supervisor"))

from config import ConfigError, load_config  # noqa: E402
from hq_supervisor import Supervisor, pid_alive  # noqa: E402
from registers import Ledger, parse_iso, sha256_file, utc_now  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(what: str, ok: bool, detail: str = "") -> None:
    results.append((what, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} {what}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def request(port: int, method: str, path: str, host: str | None = None) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, headers={"Host": host or f"127.0.0.1:{port}"})
        return connection.getresponse().status
    except OSError:
        return 0
    finally:
        connection.close()


def check_install(config_path: Path) -> None:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        check("config loads", False, str(exc))
        return
    check("config loads with no placeholders", True)
    sup = Supervisor(config)
    problems = sup.registers.verify()
    check("register chains verify", not problems, "; ".join(problems[:3]))
    for version in sorted(p.name for p in sup.releases.root.glob("*") if (p / "MANIFEST.json").is_file()):
        problems = sup.releases.verify(version)
        check(f"release {version} matches its manifest", not problems, "; ".join(problems[:3]))
    problems = sup.releases.history.verify()
    check("release history chain verifies", not problems, "; ".join(problems[:3]))
    snapshots = sup.vault.root / "snapshots"
    for entry in sorted(snapshots.glob("*/*")) if snapshots.exists() else []:
        problems = sup.vault.verify(entry.parent.name, entry.name)
        check(f"vault {entry.parent.name} {entry.name} matches its manifest", not problems, "; ".join(problems[:3]))

    lock = sup.state_dir / "supervisor.lock"
    running = lock.exists() and pid_alive(int(lock.read_text(encoding="ascii").strip() or 0))
    state_file = sup.state_dir / "state.json"
    if running:
        age = (utc_now() - parse_iso(json.loads(state_file.read_text(encoding="utf-8"))["updated_utc"])).total_seconds()
        check("supervisor state is fresh (under 60 s)", age < 60, f"{age:.0f} s old")
        for service in config.get("services") or []:
            port = int(service["port"])
            check(f"{service['name']} answers on 127.0.0.1:{port}", request(port, "GET", service["health"]) == 200)
            check(f"{service['name']} refuses a foreign Host header", request(port, "GET", service["health"], "evil.example") == 403)
            check(f"{service['name']} refuses writes", request(port, "POST", service["health"]) in (403, 404, 405))
    else:
        print("INFO supervisor not running; service checks skipped")


def check_backup(folder: Path) -> None:
    sums = folder / "SHA256SUMS"
    check("backup has SHA256SUMS", sums.is_file())
    if sums.is_file():
        for line in sums.read_text(encoding="utf-8").splitlines():
            digest, name = line.split("  ", 1)
            check(f"backup copy {name} matches its recorded hash", sha256_file(folder / name) == digest)
    for path in sorted(folder.glob("*.jsonl")):
        problems = Ledger(path).verify()
        check(f"backup {path.name} chain verifies", not problems, "; ".join(problems[:3]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True)
    parser.add_argument("--backup")
    args = parser.parse_args()
    started = time.monotonic()
    if args.backup:
        check_backup(Path(args.backup))
    else:
        check_install(Path(args.config))
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"PASS: {len(results) - failed}  FAIL: {failed}  ({time.monotonic() - started:.1f} s)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
