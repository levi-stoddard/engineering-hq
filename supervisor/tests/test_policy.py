import json
from datetime import timedelta

import pytest

from conftest import OWNER_TOKEN, REVIEWER_TOKEN
from policy import ActionClass, Policy, PolicyError
from registers import Registers, utc_now


@pytest.fixture
def setup(config):
    policy = Policy.from_config(config)
    regs = Registers(config["state_dir"])

    def request(request_id, action, requester="implementer", payload=None):
        regs.requests.append({
            "kind": "request", "id": request_id, "action": action, "requester": requester,
            "summary": "test", "payload": payload or {}, "at": "2026-01-01T00:00:00Z",
        })

    return policy, regs, request


def approve(policy, regs, request_id, who="owner", token=OWNER_TOKEN, usd=2.0, calls=5, hours=24, typed=None, now=None):
    return policy.record_approval(regs, request_id, who, token, typed or request_id, usd, calls, hours, now=now)


def test_classes_and_unknown_actions_default_to_protected(setup):
    policy, _, _ = setup
    assert policy.classify("run_check") is ActionClass.ROUTINE
    assert policy.classify("prepare_review") is ActionClass.PREPARE_ONLY
    assert policy.classify("release") is ActionClass.PROTECTED
    assert policy.classify("edit_evidence") is ActionClass.FORBIDDEN
    assert policy.classify("brand_new_capability") is ActionClass.PROTECTED


def test_overlapping_classes_are_rejected():
    with pytest.raises(PolicyError, match="more than one class"):
        Policy(routine={"release"}, protected={"release"})


def test_verdicts_per_class(setup):
    policy, regs, request = setup
    for request_id, action in [("REQ-1", "run_check"), ("REQ-2", "prepare_review"), ("REQ-3", "release"), ("REQ-4", "edit_evidence")]:
        request(request_id, action)
    assert policy.evaluate(regs, "REQ-1").outcome == "allowed"
    assert policy.evaluate(regs, "REQ-2").outcome == "draft_only"
    assert policy.evaluate(regs, "REQ-3").outcome == "waiting_for_approval"
    assert policy.evaluate(regs, "REQ-4").outcome == "refused"


def test_protected_action_runs_only_after_a_valid_approval(setup):
    policy, regs, request = setup
    request("REQ-1", "release")
    approval = approve(policy, regs, "REQ-1")
    verdict = policy.evaluate(regs, "REQ-1")
    assert verdict.outcome == "authorized" and verdict.approval_id == approval["id"]


@pytest.mark.parametrize("kwargs, message", [
    ({"token": "wrong"}, "not recognised"),
    ({"who": "stranger"}, "not recognised"),
    ({"typed": "REQ-other"}, "typed confirmation"),
    ({"usd": 0}, "positive budget"),
    ({"hours": 500}, "expires within"),
])
def test_bad_approvals_record_nothing(setup, kwargs, message):
    policy, regs, request = setup
    request("REQ-1", "release")
    with pytest.raises(PolicyError, match=message):
        approve(policy, regs, "REQ-1", **kwargs)
    assert regs.approvals.rows() == []
    assert policy.evaluate(regs, "REQ-1").outcome == "waiting_for_approval"


def test_requester_cannot_approve_own_request(setup):
    policy, regs, request = setup
    request("REQ-1", "release", requester="owner")
    with pytest.raises(PolicyError, match="own request"):
        approve(policy, regs, "REQ-1")


def test_only_protected_actions_take_approvals(setup):
    policy, regs, request = setup
    request("REQ-1", "edit_evidence")
    with pytest.raises(PolicyError, match="forbidden"):
        approve(policy, regs, "REQ-1")


def test_expired_and_withdrawn_approvals_stop_counting(setup):
    policy, regs, request = setup
    request("REQ-1", "release")
    now = utc_now()
    approve(policy, regs, "REQ-1", hours=1, now=now)
    assert policy.evaluate(regs, "REQ-1", now + timedelta(minutes=30)).outcome == "authorized"
    assert policy.evaluate(regs, "REQ-1", now + timedelta(hours=2)).outcome == "waiting_for_approval"
    policy.withdraw(regs, "REQ-1", "reviewer", REVIEWER_TOKEN, "field report")
    assert policy.evaluate(regs, "REQ-1").outcome == "refused"


def test_switches(setup):
    policy, regs, request = setup
    request("REQ-1", "release")
    policy.switches["execution_enabled"] = False
    approve(policy, regs, "REQ-1")
    assert policy.evaluate(regs, "REQ-1").outcome == "authorized_held"
    policy.switches["approvals_enabled"] = False
    request("REQ-2", "release")
    with pytest.raises(PolicyError, match="switched off"):
        approve(policy, regs, "REQ-2")


def test_switches_default_to_off():
    assert Policy().switch("execution_enabled") is False


def test_rolling_spend_limit(setup):
    policy, regs, request = setup
    for n in range(1, 4):
        request(f"REQ-{n}", "release")
    approve(policy, regs, "REQ-1", "reviewer", REVIEWER_TOKEN, usd=6)
    with pytest.raises(PolicyError, match="would exceed"):
        approve(policy, regs, "REQ-2", "reviewer", REVIEWER_TOKEN, usd=5)
    approve(policy, regs, "REQ-2", "reviewer", REVIEWER_TOKEN, usd=4)
    later = utc_now() + timedelta(hours=170)
    approve(policy, regs, "REQ-3", "reviewer", REVIEWER_TOKEN, usd=9, now=later)


def test_tampered_registers_refuse_everything(setup):
    policy, regs, request = setup
    request("REQ-1", "release")
    approve(policy, regs, "REQ-1")
    lines = regs.approvals.path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["budget_usd"] = 999
    lines[0] = json.dumps(row, sort_keys=True)
    regs.approvals.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    verdict = policy.evaluate(regs, "REQ-1")
    assert verdict.outcome == "refused" and "verification" in verdict.reason


def test_approval_is_bound_to_the_exact_request(setup):
    """If the requests register is rebuilt with a changed payload, the old approval no longer matches."""
    policy, regs, request = setup
    request("REQ-1", "release", payload={"version": "1.0"})
    approve(policy, regs, "REQ-1")
    regs.requests.path.unlink()
    request("REQ-1", "release", payload={"version": "9.9"})
    assert policy.evaluate(regs, "REQ-1").outcome == "waiting_for_approval"
