import json
from datetime import timedelta

import pytest

from registers import Ledger, RegisterError, Registers, utc_now


def test_rows_chain_and_verify(tmp_path):
    ledger = Ledger(tmp_path / "r.jsonl")
    first = ledger.append({"id": "A", "value": 1})
    second = ledger.append({"id": "B", "value": 2})
    assert second["prev_sha256"] == first["row_sha256"]
    assert ledger.verify() == []


@pytest.mark.parametrize("tamper", ["edit", "delete", "reorder"])
def test_any_tampering_is_detected(tmp_path, tamper):
    ledger = Ledger(tmp_path / "r.jsonl")
    for name in "ABC":
        ledger.append({"id": name, "value": name.lower()})
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    if tamper == "edit":
        row = json.loads(lines[1])
        row["value"] = "changed"
        lines[1] = json.dumps(row, sort_keys=True)
    elif tamper == "delete":
        del lines[1]
    else:
        lines[0], lines[1] = lines[1], lines[0]
    ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert ledger.verify()
    with pytest.raises(RegisterError, match="failed verification"):
        ledger.append({"id": "D"})


def test_duplicate_ids_and_caller_chain_fields_are_refused(tmp_path):
    ledger = Ledger(tmp_path / "r.jsonl")
    ledger.append({"id": "A"})
    with pytest.raises(RegisterError, match="already has"):
        ledger.append({"id": "A"})
    with pytest.raises(RegisterError, match="assigned by the register"):
        ledger.append({"id": "B", "prev_sha256": "0" * 64})


def test_evidence_freshness(tmp_path):
    regs = Registers(tmp_path / "state")
    (tmp_path / "result.txt").write_text("42 passed\n", encoding="utf-8")
    regs.record_evidence("EV-1", "unit tests", "check_run", "PASS", "supervisor", tmp_path, ["result.txt"])
    assert regs.evidence_freshness("EV-1", tmp_path)[0] == "CURRENT"
    assert regs.evidence_freshness("EV-1", tmp_path, now=utc_now() + timedelta(days=30))[0] == "STALE"
    (tmp_path / "result.txt").write_text("41 passed\n", encoding="utf-8")
    level, reason = regs.evidence_freshness("EV-1", tmp_path)
    assert level == "INVALID" and "changed" in reason


def test_evidence_needs_existing_artifacts_and_a_known_result(tmp_path):
    regs = Registers(tmp_path / "state")
    with pytest.raises(RegisterError, match="not found"):
        regs.record_evidence("EV-1", "tests", "check_run", "PASS", "supervisor", tmp_path, ["missing.txt"])
    with pytest.raises(RegisterError, match="PASS, FAIL or NOT_TESTED"):
        regs.record_evidence("EV-1", "tests", "check_run", "LOOKS_GOOD", "supervisor", tmp_path)


def test_decisions_need_current_evidence_and_every_confirmation(tmp_path):
    regs = Registers(tmp_path / "state")
    (tmp_path / "doc.md").write_text("Ship it.\n", encoding="utf-8")
    (tmp_path / "log.txt").write_text("ok\n", encoding="utf-8")
    with pytest.raises(RegisterError, match="cite at least one"):
        regs.propose_decision("DEC-1", "Ship", "doc.md", ["owner"], [], tmp_path, "implementer")
    with pytest.raises(RegisterError, match="INVALID"):
        regs.propose_decision("DEC-1", "Ship", "doc.md", ["owner"], ["EV-missing"], tmp_path, "implementer")
    regs.record_evidence("EV-1", "tests", "check_run", "PASS", "supervisor", tmp_path, ["log.txt"])
    decision = regs.propose_decision("DEC-1", "Ship", "doc.md", ["owner", "reviewer"], ["EV-1"], tmp_path, "implementer")
    with pytest.raises(RegisterError, match="exact document"):
        regs.confirm_decision("DEC-1", "owner", "f" * 64)
    regs.confirm_decision("DEC-1", "owner", decision["document_sha256"])
    assert regs.decision_status("DEC-1") == "PROPOSED"
    with pytest.raises(RegisterError, match="already confirmed"):
        regs.confirm_decision("DEC-1", "owner", decision["document_sha256"])
    with pytest.raises(RegisterError, match="not a required approver"):
        regs.confirm_decision("DEC-1", "outsider", decision["document_sha256"])
    regs.confirm_decision("DEC-1", "reviewer", decision["document_sha256"])
    assert regs.decision_status("DEC-1") == "ADOPTED"
