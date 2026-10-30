"""Shared fixtures: every test works in a temporary copy of the Acme example, offline."""

import shutil
import sys
from pathlib import Path

import pytest

SUPERVISOR = Path(__file__).resolve().parents[1]
REPO = SUPERVISOR.parent
sys.path.insert(0, str(SUPERVISOR))

OWNER_TOKEN = "acme-demo-owner"
REVIEWER_TOKEN = "acme-demo-reviewer"


@pytest.fixture
def acme(tmp_path: Path) -> Path:
    """A copy of examples/acme laid out like the repo, so the config's relative paths resolve."""
    target = tmp_path / "examples" / "acme"
    shutil.copytree(REPO / "examples" / "acme", target, ignore=shutil.ignore_patterns("state", "releases", "vault", "__pycache__"))
    shutil.copytree(SUPERVISOR, tmp_path / "supervisor", ignore=shutil.ignore_patterns("tests", "__pycache__"))
    return target


@pytest.fixture
def config(acme: Path) -> dict:
    from config import load_config

    return load_config(acme / "config.yaml", env={})
