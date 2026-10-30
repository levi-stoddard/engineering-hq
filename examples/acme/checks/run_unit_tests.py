"""Stand-in test suite for the fictional thermostat firmware: checks each build folder is complete."""

import json
import sys
from pathlib import Path

REQUIRED = ("firmware.hex", "build-info.json", "CHANGELOG.md")

root = Path(sys.argv[1] if len(sys.argv) > 1 else "release-sources")
failures = 0
builds = sorted(p for p in root.iterdir() if p.is_dir())
for build in builds:
    missing = [name for name in REQUIRED if not (build / name).is_file()]
    info = json.loads((build / "build-info.json").read_text(encoding="utf-8")) if not missing else {}
    ok = not missing and info.get("target") == "thermostat" and info.get("tests_passed") == info.get("tests_total")
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'} {build.name}" + (f" missing {missing}" if missing else ""))
print(f"{len(builds) - failures}/{len(builds)} builds pass")
sys.exit(1 if failures else 0)
