import os
import stat

import pytest

from vault import Vault, VaultError


@pytest.fixture
def vault(tmp_path):
    v = Vault(tmp_path / "vault")
    v.ensure_layout()
    return v


@pytest.fixture
def model(tmp_path):
    source = tmp_path / "heat-loss-model"
    source.mkdir()
    (source / "model.py").write_text("print('room cools 0.8 C per hour at 5 C outside')\n", encoding="utf-8")
    (source / "README.md").write_text("Heat loss model for the reference test room.\n", encoding="utf-8")
    return source


def test_snapshot_is_read_only_and_never_overwritten(vault, model):
    manifest = vault.snapshot("SIM-001", "0.1", model)
    preserved = vault.root / "snapshots" / "SIM-001" / "0.1" / "source" / "model.py"
    assert not os.stat(preserved).st_mode & stat.S_IWRITE
    assert vault.verify("SIM-001", "0.1") == []
    assert manifest["manifest_sha256"] == vault.manifest("SIM-001", "0.1")["manifest_sha256"]
    with pytest.raises(VaultError, match="new version"):
        vault.snapshot("SIM-001", "0.1", model)


def test_staging_keeps_three_separate_results(vault, model):
    vault.snapshot("SIM-001", "0.1", model)
    record = vault.stage("SIM-001", "0.1", runs="PASS", tests="PASS", validated="NOT_TESTED")
    assert record["results"] == {"runs": "PASS", "tests": "PASS", "validated": "NOT_TESTED"}
    with pytest.raises(VaultError, match="must be one of"):
        vault.stage("SIM-001", "0.1", runs="PASS", tests="PASS", validated="probably")


def test_promotion_needs_the_exact_snapshot_hash(vault, model):
    manifest = vault.snapshot("SIM-001", "0.1", model)
    vault.stage("SIM-001", "0.1", "PASS", "PASS", "PASS")
    with pytest.raises(VaultError, match="one exact hash"):
        vault.promote("SIM-001", "0.1", "0" * 64, "APR-1", "owner")
    vault.promote("SIM-001", "0.1", manifest["manifest_sha256"], "APR-1", "owner")
    assert vault.active_version("SIM-001") == "0.1"
    assert (vault.root / "approvals" / "SIM-001_0.1_APPROVAL.json").is_file()


def test_promoting_a_new_version_archives_the_old_one(vault, model):
    first = vault.snapshot("SIM-001", "0.1", model)
    vault.stage("SIM-001", "0.1", "PASS", "PASS", "PASS")
    vault.promote("SIM-001", "0.1", first["manifest_sha256"], "APR-1", "owner")
    (model / "model.py").write_text("print('room cools 0.7 C per hour at 5 C outside')\n", encoding="utf-8")
    second = vault.snapshot("SIM-001", "0.2", model)
    vault.stage("SIM-001", "0.2", "PASS", "PASS", "NOT_TESTED")
    vault.promote("SIM-001", "0.2", second["manifest_sha256"], "APR-2", "owner")
    assert vault.active_version("SIM-001") == "0.2"
    assert (vault.root / "archive" / "SIM-001_0.1.json").is_file()


def test_runs_happen_in_a_restored_copy_not_the_snapshot(vault, model):
    vault.snapshot("SIM-001", "0.1", model)
    working = vault.restore("SIM-001", "0.1")
    (working / "model.py").write_text("print('edited in the working copy')\n", encoding="utf-8")
    assert vault.verify("SIM-001", "0.1") == []
    working_again = vault.restore("SIM-001", "0.1")
    assert "0.8" in (working_again / "model.py").read_text(encoding="utf-8")


def test_entry_ids_are_checked(vault, model):
    with pytest.raises(VaultError, match="entry ids"):
        vault.snapshot("../escape", "0.1", model)
