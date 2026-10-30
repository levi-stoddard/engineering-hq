"""Approval policy: which actions an agent may take, and what a protected action needs.

Four action classes:
  routine       runs without an approval (local work, tests, reading status)
  prepare_only  may produce a draft, never executes anything
  protected     needs a recorded approval from a named approver, bound to the exact request
  forbidden     refused for any agent, approval or not

An action nobody classified is treated as protected. That is the conservative default:
a new capability waits for a person until someone decides otherwise.

Approvals are rows in the approvals register. Each is bound to the SHA-256 of the request it
approves, states a budget, a call limit and an expiry, and is only accepted from an approver
who proves who they are with their own token at a terminal and types the request id back.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from registers import RegisterError, Registers, content_sha256, iso, parse_iso, utc_now


class ActionClass(str, Enum):
    ROUTINE = "routine"
    PREPARE_ONLY = "prepare_only"
    PROTECTED = "protected"
    FORBIDDEN = "forbidden"


class PolicyError(Exception):
    """An approval or request the policy refuses. Nothing is recorded when this is raised."""


@dataclass(frozen=True)
class Approver:
    name: str
    token_sha256: str
    spend_limit_usd: float | None = None  # None: no aggregate limit, every approval still states a budget


@dataclass(frozen=True)
class Verdict:
    outcome: str  # refused | draft_only | allowed | waiting_for_approval | authorized_held | authorized
    reason: str
    approval_id: str | None = None


@dataclass
class Policy:
    routine: set[str] = field(default_factory=set)
    prepare_only: set[str] = field(default_factory=set)
    protected: set[str] = field(default_factory=set)
    forbidden: set[str] = field(default_factory=set)
    approvers: dict[str, Approver] = field(default_factory=dict)
    switches: dict[str, bool] = field(default_factory=dict)
    max_approval_hours: float = 168
    spend_window_hours: float = 168

    def __post_init__(self) -> None:
        classes = [self.routine, self.prepare_only, self.protected, self.forbidden]
        seen: set[str] = set()
        for actions in classes:
            overlap = seen & actions
            if overlap:
                raise PolicyError(f"actions listed in more than one class: {sorted(overlap)}")
            seen |= actions

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "Policy":
        policy = config.get("policy", {})
        approvers = {
            name: Approver(name, str(spec["token_sha256"]).lower(), spec.get("spend_limit_usd"))
            for name, spec in (config.get("approvers") or {}).items()
        }
        return cls(
            routine=set(policy.get("routine", [])),
            prepare_only=set(policy.get("prepare_only", [])),
            protected=set(policy.get("protected", [])),
            forbidden=set(policy.get("forbidden", [])),
            approvers=approvers,
            switches={k: bool(v) for k, v in (config.get("switches") or {}).items()},
            max_approval_hours=float(policy.get("max_approval_hours", 168)),
            spend_window_hours=float(policy.get("spend_window_hours", 168)),
        )

    def switch(self, name: str) -> bool:
        """Every switch is off unless the config turns it on."""
        return self.switches.get(name, False)

    def classify(self, action: str) -> ActionClass:
        if action in self.forbidden:
            return ActionClass.FORBIDDEN
        if action in self.protected:
            return ActionClass.PROTECTED
        if action in self.prepare_only:
            return ActionClass.PREPARE_ONLY
        if action in self.routine:
            return ActionClass.ROUTINE
        return ActionClass.PROTECTED

    # -- evaluation ----------------------------------------------------------------------

    def evaluate(self, registers: Registers, request_id: str, now: datetime | None = None) -> Verdict:
        """Decide what may happen to a request right now. Called again at execution time."""
        now = now or utc_now()
        problems = registers.verify()
        if problems:
            return Verdict("refused", f"registers failed verification: {problems[0]}")
        request = _request(registers, request_id)
        if request is None:
            return Verdict("refused", f"no request {request_id}")
        if registers.approvals.find(kind="withdrawal", request_id=request_id):
            return Verdict("refused", "withdrawn by an approver")

        action_class = self.classify(request["action"])
        if action_class is ActionClass.FORBIDDEN:
            return Verdict("refused", f"{request['action']} is forbidden for agents")
        if action_class is ActionClass.PREPARE_ONLY:
            return Verdict("draft_only", "prepare-only action: a draft may be written, nothing runs")
        if action_class is ActionClass.ROUTINE:
            return Verdict("allowed", "routine action")

        approval = self._valid_approval(registers, request, now)
        if approval is None:
            return Verdict("waiting_for_approval", "protected action: needs a recorded approval")
        if not self.switch("execution_enabled"):
            return Verdict("authorized_held", "approved, but execution is switched off", approval["id"])
        return Verdict("authorized", f"approved by {approval['approver']}", approval["id"])

    def _valid_approval(self, registers: Registers, request: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        for approval in reversed(registers.approvals.find(kind="approval", request_id=request["id"])):
            if approval["request_sha256"] != content_sha256(request):
                continue  # approved a different version of the request
            if parse_iso(approval["expires"]) <= now:
                continue
            if approval["approver"] not in self.approvers:
                continue  # approver was removed from the config since
            return approval
        return None

    # -- approver actions (console only) -------------------------------------------------

    def verify_approver(self, name: str, token: str) -> Approver:
        approver = self.approvers.get(name)
        given = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if approver is None or not hmac.compare_digest(given, approver.token_sha256):
            raise PolicyError("approver name or token not recognised; nothing recorded")
        return approver

    def record_approval(
        self,
        registers: Registers,
        request_id: str,
        approver_name: str,
        token: str,
        typed_confirmation: str,
        budget_usd: float,
        max_calls: int,
        hours: float,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or utc_now()
        approver = self.verify_approver(approver_name, token)
        if not self.switch("approvals_enabled"):
            raise PolicyError("approvals are switched off")
        if typed_confirmation != request_id:
            raise PolicyError("the typed confirmation does not match the request id")
        problems = registers.verify()
        if problems:
            raise PolicyError(f"registers failed verification: {problems[0]}")
        request = _request(registers, request_id)
        if request is None:
            raise PolicyError(f"no request {request_id}")
        if self.classify(request["action"]) is not ActionClass.PROTECTED:
            raise PolicyError(f"{request['action']} is {self.classify(request['action']).value}; only protected actions take approvals")
        if request["requester"] == approver.name:
            raise PolicyError("a requester cannot approve their own request")
        if self.evaluate(registers, request_id, now).outcome != "waiting_for_approval":
            raise PolicyError("this request is not waiting for approval")
        if budget_usd <= 0 or max_calls <= 0:
            raise PolicyError("an approval must state a positive budget and call limit")
        if not 0 < hours <= self.max_approval_hours:
            raise PolicyError(f"an approval expires within {self.max_approval_hours:g} hours")
        if approver.spend_limit_usd is not None:
            committed = self.committed_spend(registers, approver.name, now)
            if committed + budget_usd > approver.spend_limit_usd:
                raise PolicyError(
                    f"{approver.name} has {committed:.2f} of {approver.spend_limit_usd:.2f} USD committed in the "
                    f"last {self.spend_window_hours:g} hours; this approval would exceed it"
                )
        try:
            return registers.approvals.append({
                "kind": "approval",
                "id": f"APR-{request_id}-{len(registers.approvals.rows()) + 1}",
                "request_id": request_id,
                "request_sha256": content_sha256(request),
                "action": request["action"],
                "approver": approver.name,
                "budget_usd": round(float(budget_usd), 2),
                "max_calls": int(max_calls),
                "expires": iso(now + timedelta(hours=hours)),
                "at": iso(now),
            })
        except RegisterError as exc:
            raise PolicyError(str(exc)) from exc

    def withdraw(self, registers: Registers, request_id: str, approver_name: str, token: str, reason: str, now: datetime | None = None) -> dict[str, Any]:
        approver = self.verify_approver(approver_name, token)
        if _request(registers, request_id) is None:
            raise PolicyError(f"no request {request_id}")
        return registers.approvals.append({
            "kind": "withdrawal",
            "request_id": request_id,
            "by": approver.name,
            "reason": reason,
            "at": iso(now or utc_now()),
        })

    def committed_spend(self, registers: Registers, approver_name: str, now: datetime) -> float:
        """Budgets this approver committed in the rolling window, withdrawn or expired ones included."""
        since = now - timedelta(hours=self.spend_window_hours)
        return sum(
            row["budget_usd"]
            for row in registers.approvals.find(kind="approval", approver=approver_name)
            if parse_iso(row["at"]) > since
        )


def _request(registers: Registers, request_id: str) -> dict[str, Any] | None:
    rows = registers.requests.find(kind="request", id=request_id)
    return rows[0] if rows else None
