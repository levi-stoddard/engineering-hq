"""Release manifests and rollback.

A manifest pins every file of a release by SHA-256. It holds no timestamps or machine-specific
paths, so building it twice from the same files gives the same manifest and the same
`manifest_sha256`. Time and outcome of an activation go into the evidence register instead.

Releases are staged into `<releases_dir>/<version>/` and never edited afterwards (a changed
file is a new version). `CURRENT` names the active version. Activation and rollback both verify
the target release against its manifest first, and both are recorded in `history.jsonl`, an
append-only hash-chained ledger.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from registers import Ledger, canonical, iso, sha256_bytes, sha256_file, utc_now

MANIFEST_NAME = "MANIFEST.json"
VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")
SKIP = {"__pycache__", ".git", MANIFEST_NAME}


class ManifestError(Exception):
    pass


def _files(root: Path) -> list[Path]:
    found = []
    # Sort on the exact relative path string: Path ordering ignores case on Windows, which would
    # make the same files hash differently on different machines.
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if any(part in SKIP for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            raise ManifestError(f"symbolic links are not allowed in a release: {path.relative_to(root)}")
        if path.is_file():
            found.append(path)
    return found


def build_manifest(source: Path, component: str, version: str, decision_id: str | None = None) -> dict[str, Any]:
    source = Path(source)
    if not VERSION.match(version):
        raise ManifestError(f"invalid version: {version!r}")
    files = [
        {"path": path.relative_to(source).as_posix(), "sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in _files(source)
    ]
    if not files:
        raise ManifestError("a release needs at least one file")
    tree = sha256_bytes("".join(f"{f['sha256']}  {f['path']}\n" for f in files).encode("utf-8"))
    manifest = {
        "schema": "engineering-hq.release-manifest.v1",
        "component": component,
        "version": version,
        "decision_id": decision_id,
        "files": files,
        "tree_sha256": tree,
    }
    manifest["manifest_sha256"] = sha256_bytes(canonical(manifest))
    return manifest


def verify_manifest(manifest: dict[str, Any], folder: Path) -> list[str]:
    folder = Path(folder)
    problems = []
    body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    if sha256_bytes(canonical(body)) != manifest.get("manifest_sha256"):
        problems.append("the manifest itself was changed")
    listed = {f["path"]: f for f in manifest.get("files", [])}
    present = {p.relative_to(folder).as_posix() for p in _files(folder)}
    for path in sorted(set(listed) - present):
        problems.append(f"missing: {path}")
    for path in sorted(present - set(listed)):
        problems.append(f"not in the manifest: {path}")
    for path in sorted(set(listed) & present):
        if sha256_file(folder / path) != listed[path]["sha256"]:
            problems.append(f"changed: {path}")
    return problems


class ReleaseStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.history = Ledger(self.root / "history.jsonl")

    def path(self, version: str) -> Path:
        if not VERSION.match(version):
            raise ManifestError(f"invalid version: {version!r}")
        return self.root / version

    def manifest(self, version: str) -> dict[str, Any]:
        path = self.path(version) / MANIFEST_NAME
        if not path.is_file():
            raise ManifestError(f"no staged release {version}")
        return json.loads(path.read_text(encoding="utf-8"))

    def stage(self, source: Path, component: str, version: str, decision_id: str | None = None) -> dict[str, Any]:
        target = self.path(version)
        if target.exists():
            raise ManifestError(f"release {version} already exists; a changed file is a new version")
        manifest = build_manifest(source, component, version, decision_id)
        temporary = self.root / f".staging-{version}"
        if temporary.exists():
            shutil.rmtree(temporary)
        shutil.copytree(source, temporary, ignore=shutil.ignore_patterns(*SKIP))
        (temporary / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        problems = verify_manifest(manifest, temporary)
        if problems:
            shutil.rmtree(temporary)
            raise ManifestError(f"staged copy does not match the source: {problems[0]}")
        os.replace(temporary, target)
        return manifest

    def current(self) -> str | None:
        pointer = self.root / "CURRENT"
        if not pointer.exists():
            return None
        return pointer.read_text(encoding="utf-8").strip() or None

    def _point(self, version: str) -> None:
        pointer = self.root / "CURRENT"
        temporary = pointer.with_suffix(".tmp")
        temporary.write_text(version + "\n", encoding="utf-8")
        os.replace(temporary, pointer)

    def verify(self, version: str) -> list[str]:
        try:
            return verify_manifest(self.manifest(version), self.path(version))
        except ManifestError as exc:
            return [str(exc)]

    def activate(self, version: str, actor: str, approval_id: str | None = None) -> dict[str, Any]:
        problems = self.verify(version)
        if problems:
            raise ManifestError(f"release {version} failed verification; not activated: {problems[0]}")
        previous = self.current()
        self._point(version)
        return self.history.append({
            "kind": "activate",
            "version": version,
            "manifest_sha256": self.manifest(version)["manifest_sha256"],
            "previous": previous,
            "actor": actor,
            "approval_id": approval_id,
            "at": iso(utc_now()),
        })

    def rollback(self, actor: str, approval_id: str | None = None) -> dict[str, Any]:
        """Return to the version that was active before the current one, after verifying it."""
        current = self.current()
        if current is None:
            raise ManifestError("nothing is active; nothing to roll back")
        target = None
        for row in reversed(self.history.rows()):
            if row.get("kind") == "activate" and row.get("version") == current and row.get("previous"):
                target = row["previous"]
                break
        if target is None:
            raise ManifestError(f"no earlier release recorded before {current}")
        problems = self.verify(target)
        if problems:
            raise ManifestError(f"release {target} failed verification; rollback refused: {problems[0]}")
        self._point(target)
        return self.history.append({
            "kind": "rollback",
            "version": target,
            "manifest_sha256": self.manifest(target)["manifest_sha256"],
            "previous": current,
            "actor": actor,
            "approval_id": approval_id,
            "at": iso(utc_now()),
        })
