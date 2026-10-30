import json
import shutil

import pytest

from manifest import ManifestError, ReleaseStore, build_manifest, verify_manifest


@pytest.fixture
def builds(acme):
    return acme / "release-sources"


def test_manifests_are_reproducible(builds, tmp_path):
    first = build_manifest(builds / "thermostat-fw-1.4.0", "thermostat-firmware", "1.4.0")
    copy = tmp_path / "copy"
    shutil.copytree(builds / "thermostat-fw-1.4.0", copy)
    second = build_manifest(copy, "thermostat-firmware", "1.4.0")
    assert first == second
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert [f["path"] for f in first["files"]] == sorted(f["path"] for f in first["files"])


def test_verify_finds_changed_missing_and_extra_files(builds, tmp_path):
    folder = tmp_path / "release"
    shutil.copytree(builds / "thermostat-fw-1.4.0", folder)
    manifest = build_manifest(folder, "thermostat-firmware", "1.4.0")
    assert verify_manifest(manifest, folder) == []
    (folder / "CHANGELOG.md").write_text("edited\n", encoding="utf-8")
    (folder / "build-info.json").unlink()
    (folder / "extra.txt").write_text("surprise\n", encoding="utf-8")
    problems = verify_manifest(manifest, folder)
    assert "changed: CHANGELOG.md" in problems
    assert "missing: build-info.json" in problems
    assert "not in the manifest: extra.txt" in problems
    manifest["version"] = "9.9.9"
    assert "the manifest itself was changed" in verify_manifest(manifest, folder)


def test_stage_activate_and_rollback(builds, tmp_path):
    store = ReleaseStore(tmp_path / "releases")
    store.stage(builds / "thermostat-fw-1.4.0", "thermostat-firmware", "1.4.0")
    store.activate("1.4.0", actor="test")
    store.stage(builds / "thermostat-fw-1.4.1", "thermostat-firmware", "1.4.1")
    store.activate("1.4.1", actor="test")
    assert store.current() == "1.4.1"
    row = store.rollback(actor="test")
    assert (row["version"], row["previous"]) == ("1.4.0", "1.4.1")
    assert store.current() == "1.4.0"
    with pytest.raises(ManifestError, match="no earlier release"):
        store.rollback(actor="test")
    assert store.history.verify() == []


def test_a_release_version_is_never_overwritten(builds, tmp_path):
    store = ReleaseStore(tmp_path / "releases")
    store.stage(builds / "thermostat-fw-1.4.0", "thermostat-firmware", "1.4.0")
    with pytest.raises(ManifestError, match="already exists"):
        store.stage(builds / "thermostat-fw-1.4.1", "thermostat-firmware", "1.4.0")


def test_rollback_refuses_a_damaged_previous_release(builds, tmp_path):
    store = ReleaseStore(tmp_path / "releases")
    for version in ("1.4.0", "1.4.1"):
        store.stage(builds / f"thermostat-fw-{version}", "thermostat-firmware", version)
        store.activate(version, actor="test")
    (store.path("1.4.0") / "firmware.hex").write_text(":00000001FF\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="failed verification; rollback refused"):
        store.rollback(actor="test")
    assert store.current() == "1.4.1"


def test_activation_refuses_a_damaged_release(builds, tmp_path):
    store = ReleaseStore(tmp_path / "releases")
    store.stage(builds / "thermostat-fw-1.4.0", "thermostat-firmware", "1.4.0")
    manifest_path = store.path("1.4.0") / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManifestError, match="not activated"):
        store.activate("1.4.0", actor="test")
    assert store.current() is None


def test_invalid_versions_are_rejected(builds, tmp_path):
    with pytest.raises(ManifestError, match="invalid version"):
        ReleaseStore(tmp_path).stage(builds / "thermostat-fw-1.4.0", "x", "../escape")
