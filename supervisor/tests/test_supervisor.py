"""End-to-end: the example config, the example requests, one loop pass at a time."""

import json
import shutil

import pytest

from conftest import OWNER_TOKEN
from hq_supervisor import Supervisor, main, validate_request


@pytest.fixture
def sup(config):
    return Supervisor(config)


def drop(sup, acme, *names):
    inbox = sup.state_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copy(acme / "requests" / name, inbox / name)


def status(sup, request_id):
    return sup.status_of(request_id)[0]


def test_each_class_ends_where_it_should(sup, acme):
    drop(sup, acme, "REQ-acme-001-run-tests.json", "REQ-acme-002-release-1.4.0.json",
         "REQ-acme-003-prepare-review.json", "REQ-acme-004-edit-evidence.json")
    sup.process()
    assert status(sup, "REQ-acme-001") == "done"
    assert status(sup, "REQ-acme-002") == "waiting_for_approval"
    assert status(sup, "REQ-acme-003") == "draft_written"
    assert status(sup, "REQ-acme-004") == "refused"
    assert sup.releases.current() is None
    evidence = sup.registers.evidence.rows()
    assert evidence[0]["result"] == "PASS" and evidence[0]["subject"] == "unit-tests"
    assert (sup.state_dir / "drafts" / "REQ-acme-003.md").read_text(encoding="utf-8").count("DRAFT, NOT SUBMITTED") == 1
    assert not list((sup.state_dir / "inbox").glob("*.json"))


def test_release_and_rollback_after_approval(sup, acme):
    drop(sup, acme, "REQ-acme-002-release-1.4.0.json")
    sup.process()
    sup.policy.record_approval(sup.registers, "REQ-acme-002", "owner", OWNER_TOKEN, "REQ-acme-002", 1, 1, 24)
    sup.process()
    assert status(sup, "REQ-acme-002") == "done"
    assert sup.releases.current() == "1.4.0"

    drop(sup, acme, "REQ-acme-005-release-1.4.1.json")
    sup.process()
    sup.policy.record_approval(sup.registers, "REQ-acme-005", "owner", OWNER_TOKEN, "REQ-acme-005", 1, 1, 24)
    sup.process()
    assert sup.releases.current() == "1.4.1"

    drop(sup, acme, "REQ-acme-006-rollback.json")
    sup.process()
    assert status(sup, "REQ-acme-006") == "waiting_for_approval"
    sup.policy.record_approval(sup.registers, "REQ-acme-006", "owner", OWNER_TOKEN, "REQ-acme-006", 1, 1, 24)
    sup.process()
    assert status(sup, "REQ-acme-006") == "done"
    assert sup.releases.current() == "1.4.0"
    kinds = [row["evidence_type"] for row in sup.registers.evidence.rows()]
    assert kinds == ["release_activated", "release_activated", "rollback"]
    assert sup.registers.verify() == []


def test_execution_switch_off_holds_approved_work(config, acme):
    config["switches"]["execution_enabled"] = False
    sup = Supervisor(config)
    drop(sup, acme, "REQ-acme-002-release-1.4.0.json")
    sup.process()
    sup.policy.record_approval(sup.registers, "REQ-acme-002", "owner", OWNER_TOKEN, "REQ-acme-002", 1, 1, 24)
    sup.process()
    assert status(sup, "REQ-acme-002") == "authorized_held"
    assert sup.releases.current() is None


def test_release_source_must_stay_inside_the_sources_folder(sup):
    sup.propose({"id": "REQ-escape", "action": "release", "requester": "implementer", "summary": "escape",
                 "payload": {"component": "x", "version": "1.0", "source": "../../supervisor"}})
    sup.process()
    sup.policy.record_approval(sup.registers, "REQ-escape", "owner", OWNER_TOKEN, "REQ-escape", 1, 1, 24)
    sup.process()
    assert status(sup, "REQ-escape") == "failed"
    assert "outside the allowed folder" in sup.status_of("REQ-escape")[1]


def test_a_request_names_a_check_and_never_supplies_a_command(sup):
    sup.propose({"id": "REQ-cmd", "action": "run_check", "requester": "implementer", "summary": "try a command",
                 "payload": {"check": "rm -rf /"}})
    sup.process()
    assert status(sup, "REQ-cmd") == "refused"


def test_malformed_inbox_files_are_rejected_and_kept(sup):
    inbox = sup.state_dir / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "bad.json").write_text(json.dumps({"id": "nope", "action": "Release!"}), encoding="utf-8")
    notes = sup.intake()
    assert notes and notes[0].startswith("rejected bad.json")
    assert (inbox / "processed" / "bad.rejected.json").is_file()


@pytest.mark.parametrize("request_body, fragment", [
    ({"id": "REQ-1", "action": "release", "requester": "Implementer Bot", "summary": "x"}, "requester"),
    ({"id": "REQ-1", "action": "release", "requester": "implementer", "summary": ""}, "summary"),
    ({"id": "REQ-1", "action": "release", "requester": "implementer", "summary": "x", "approved": True}, "unknown fields"),
])
def test_request_validation(request_body, fragment):
    assert any(fragment in error for error in validate_request(request_body))


def test_cli_run_once_status_and_verify(acme, capsys):
    shutil.copy(acme / "requests" / "REQ-acme-001-run-tests.json", acme / "request.json")
    config_path = str(acme / "config.yaml")
    assert main(["--config", config_path, "propose", str(acme / "request.json")]) == 0
    assert main(["--config", config_path, "run", "--once"]) == 0
    assert main(["--config", config_path, "status"]) == 0
    assert "REQ-acme-001" in capsys.readouterr().out
    assert main(["--config", config_path, "verify"]) == 0
    state = json.loads((acme / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["requests"] == {"done": 1} and state["register_problems"] == []


def test_cli_approve_refuses_without_a_terminal(acme, monkeypatch):
    monkeypatch.setattr("sys.stdin", open(acme / "config.yaml"))
    with pytest.raises(SystemExit, match="real terminal"):
        main(["--config", str(acme / "config.yaml"), "approve", "REQ-acme-002", "--as", "owner", "--usd", "1", "--calls", "1"])
