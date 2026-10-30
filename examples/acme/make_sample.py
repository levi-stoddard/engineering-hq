"""Regenerate examples/acme/sample/ by running the whole Acme scenario through the real code.

    python examples/acme/make_sample.py

Works in a temporary copy, so it never touches a state folder you are using. The output shows what
the registers, a release manifest and the supervisor state look like after: a routine check, a
prepare-only draft, a forbidden request, a decision adopted by two approvers on cited evidence,
two approved releases and an approved rollback.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "supervisor"))

from config import load_config  # noqa: E402
from hq_supervisor import Supervisor, write_state  # noqa: E402
from registers import sha256_file  # noqa: E402

OWNER, REVIEWER = ("owner", "acme-demo-owner"), ("reviewer", "acme-demo-reviewer")


def approve(sup: Supervisor, request_id: str, who: tuple[str, str], usd: float) -> None:
    sup.policy.record_approval(sup.registers, request_id, who[0], who[1], request_id, usd, 3, 24)


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp) / "examples" / "acme"
        shutil.copytree(HERE, work, ignore=shutil.ignore_patterns("state", "releases", "vault", "sample", "__pycache__"))
        shutil.copytree(REPO / "supervisor", Path(temp) / "supervisor", ignore=shutil.ignore_patterns("tests", "__pycache__"))
        sup = Supervisor(load_config(work / "config.yaml", env={}))
        inbox = sup.state_dir / "inbox"
        inbox.mkdir(parents=True)

        def drop(name: str) -> None:
            shutil.copy(work / "requests" / name, inbox / name)
            sup.process()

        for name in ("REQ-acme-001-run-tests.json", "REQ-acme-003-prepare-review.json", "REQ-acme-004-edit-evidence.json"):
            drop(name)

        test_run = sup.registers.evidence.rows()[0]["id"]
        decision = sup.registers.propose_decision(
            "DEC-001", "Ship thermostat firmware 1.4.0", "decisions/DEC-001-ship-1.4.0.md",
            ["owner", "reviewer"], [test_run], work, "implementer",
        )
        for who in (OWNER, REVIEWER):
            sup.registers.confirm_decision("DEC-001", who[0], sha256_file(work / decision["document"]))

        drop("REQ-acme-002-release-1.4.0.json")
        approve(sup, "REQ-acme-002", OWNER, 2)
        sup.process()
        drop("REQ-acme-005-release-1.4.1.json")
        approve(sup, "REQ-acme-005", REVIEWER, 2)
        sup.process()
        drop("REQ-acme-006-rollback.json")
        approve(sup, "REQ-acme-006", OWNER, 1)
        sup.process()
        write_state(sup, [])

        out = HERE / "sample"
        if out.exists():
            shutil.rmtree(out)
        (out / "registers").mkdir(parents=True)
        for ledger in sup.registers.all():
            shutil.copy(ledger.path, out / "registers" / ledger.path.name)
        shutil.copy(sup.releases.path("1.4.0") / "MANIFEST.json", out / "release-manifest-1.4.0.json")
        shutil.copy(sup.releases.history.path, out / "release-history.jsonl")
        shutil.copy(sup.state_dir / "state.json", out / "state.json")
        shutil.copy(sup.state_dir / "drafts" / "REQ-acme-003.md", out / "draft-REQ-acme-003.md")
        for request in sup.requests():
            print(f"{request['id']:<14} {request['action']:<15} {sup.status_of(request['id'])[0]}")
        print(f"DEC-001: {sup.registers.decision_status('DEC-001')}; current release: {sup.releases.current()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
