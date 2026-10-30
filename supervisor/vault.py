"""Simulation vault: preserved, hash-manifested snapshots and an approved library.

Layout under the vault directory:
  snapshots/<id>/<version>/   preserved source + MANIFEST.json, read-only, never run from here
  staging/                    candidates waiting for a decision
  approvals/                  one record per approval, naming the exact snapshot hash approved
  active/                     the approved version of each entry
  archive/                    earlier approved versions
  runtime/<id>/<version>/     restored working copies; the only place anything runs

A snapshot that runs is not thereby validated. Each candidate keeps three separate results:
whether it runs, whether its own tests pass, and whether it was validated.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
from pathlib import Path
from typing import Any

from manifest import MANIFEST_NAME, ManifestError, build_manifest, verify_manifest
from registers import iso, utc_now

ENTRY_ID = re.compile(r"^[A-Z][A-Z0-9]{1,15}-[0-9]{3}$")
RESULTS = {"PASS", "FAIL", "NOT_TESTED"}
LAYOUT = ("snapshots", "staging", "approvals", "active", "archive", "runtime")


class VaultError(Exception):
    pass


class Vault:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def ensure_layout(self) -> None:
        for name in LAYOUT:
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def _snapshot_dir(self, entry_id: str, version: str) -> Path:
        if not ENTRY_ID.match(entry_id):
            raise VaultError(f"entry ids look like SIM-001: {entry_id!r}")
        return self.root / "snapshots" / entry_id / version

    def snapshot(self, entry_id: str, version: str, source: Path) -> dict[str, Any]:
        """Copy a working folder into a read-only snapshot. Refuses to overwrite: a change is a new version."""
        target = self._snapshot_dir(entry_id, version)
        if target.exists():
            raise VaultError(f"{entry_id} {version} is already preserved; a changed file is a new version")
        manifest = build_manifest(source, entry_id, version)
        shutil.copytree(source, target / "source", ignore=shutil.ignore_patterns("__pycache__", ".git", MANIFEST_NAME))
        (target / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        problems = verify_manifest(manifest, target / "source")
        if problems:
            raise VaultError(f"preserved copy does not match the source: {problems[0]}")
        for path in target.rglob("*"):
            if path.is_file():
                os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        return manifest

    def manifest(self, entry_id: str, version: str) -> dict[str, Any]:
        path = self._snapshot_dir(entry_id, version) / MANIFEST_NAME
        if not path.is_file():
            raise VaultError(f"{entry_id} {version} is not preserved")
        return json.loads(path.read_text(encoding="utf-8"))

    def verify(self, entry_id: str, version: str) -> list[str]:
        try:
            return verify_manifest(self.manifest(entry_id, version), self._snapshot_dir(entry_id, version) / "source")
        except (VaultError, ManifestError) as exc:
            return [str(exc)]

    def stage(self, entry_id: str, version: str, runs: str, tests: str, validated: str, notes: str = "") -> dict[str, Any]:
        for label, value in (("runs", runs), ("tests", tests), ("validated", validated)):
            if value not in RESULTS:
                raise VaultError(f"{label} must be one of {sorted(RESULTS)}")
        problems = self.verify(entry_id, version)
        if problems:
            raise VaultError(f"{entry_id} {version} failed verification: {problems[0]}")
        record = {
            "id": entry_id,
            "version": version,
            "snapshot_sha256": self.manifest(entry_id, version)["manifest_sha256"],
            "results": {"runs": runs, "tests": tests, "validated": validated},
            "notes": notes,
            "staged_at": iso(utc_now()),
        }
        self._write(self.root / "staging" / f"{entry_id}_{version}.json", record)
        return record

    def promote(self, entry_id: str, version: str, snapshot_sha256: str, approval_id: str, approver: str) -> dict[str, Any]:
        """Move a staged candidate into the active library. Called only for an authorized request."""
        staged_path = self.root / "staging" / f"{entry_id}_{version}.json"
        if not staged_path.is_file():
            raise VaultError(f"{entry_id} {version} is not staged")
        staged = json.loads(staged_path.read_text(encoding="utf-8"))
        current_hash = self.manifest(entry_id, version)["manifest_sha256"]
        if snapshot_sha256 != current_hash or staged["snapshot_sha256"] != current_hash:
            raise VaultError("the approval names a different snapshot than the one preserved; an approval covers one exact hash")
        problems = self.verify(entry_id, version)
        if problems:
            raise VaultError(f"{entry_id} {version} failed verification: {problems[0]}")
        approval = {
            "id": entry_id,
            "version": version,
            "snapshot_sha256": current_hash,
            "approval_id": approval_id,
            "approver": approver,
            "at": iso(utc_now()),
        }
        self._write(self.root / "approvals" / f"{entry_id}_{version}_APPROVAL.json", approval)
        active_path = self.root / "active" / f"{entry_id}.json"
        if active_path.is_file():
            previous = json.loads(active_path.read_text(encoding="utf-8"))
            self._write(self.root / "archive" / f"{entry_id}_{previous['version']}.json", previous)
        self._write(active_path, {**staged, "approval_id": approval_id, "activated_at": approval["at"]})
        staged_path.unlink()
        return approval

    def restore(self, entry_id: str, version: str) -> Path:
        """Fresh writable working copy under runtime/. The snapshot itself is never run or edited."""
        problems = self.verify(entry_id, version)
        if problems:
            raise VaultError(f"{entry_id} {version} failed verification; not restored: {problems[0]}")
        target = self.root / "runtime" / entry_id / version / "app"
        if target.exists():
            shutil.rmtree(target, onexc=_make_writable)
        shutil.copytree(self._snapshot_dir(entry_id, version) / "source", target)
        for path in target.rglob("*"):
            if path.is_file():
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        return target

    def active_version(self, entry_id: str) -> str | None:
        path = self.root / "active" / f"{entry_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))["version"] if path.is_file() else None

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)


def _make_writable(function: Any, path: str, _info: Any) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)
