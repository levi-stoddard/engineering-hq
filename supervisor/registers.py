"""Append-only, hash-chained registers.

Every register is a JSON Lines file. Each row carries the SHA-256 of the row before it
(`prev_sha256`) and of itself (`row_sha256`), computed over canonical JSON. Editing,
deleting or reordering any row breaks the chain, and `verify()` reports where.

Nothing here updates a row in place. A change of state (a confirmation, a withdrawal,
a status) is a new row that refers to the earlier one.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

GENESIS = "0" * 64
CHAIN_FIELDS = ("seq", "prev_sha256", "row_sha256")


class RegisterError(Exception):
    """A write that the register rules refuse."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def content_sha256(row: dict[str, Any]) -> str:
    """Hash of a row's content, ignoring the chain fields. Used to bind approvals to requests."""
    return sha256_bytes(canonical({k: v for k, v in row.items() if k not in CHAIN_FIELDS}))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class Ledger:
    """One append-only JSONL file with a SHA-256 chain."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def append(self, row: dict[str, Any]) -> dict[str, Any]:
        if any(field in row for field in CHAIN_FIELDS):
            raise RegisterError("chain fields are assigned by the register, not by the caller")
        existing = self.rows()
        problems = self._verify_rows(existing)
        if problems:
            raise RegisterError(f"{self.path.name} failed verification; nothing appended: {problems[0]}")
        if "id" in row and any(r.get("id") == row["id"] for r in existing):
            raise RegisterError(f"{self.path.name} already has a row with id {row['id']}")
        prev = existing[-1]["row_sha256"] if existing else GENESIS
        entry = dict(row)
        entry["seq"] = len(existing) + 1
        entry["prev_sha256"] = prev
        entry["row_sha256"] = sha256_bytes(canonical({k: v for k, v in entry.items() if k != "row_sha256"}))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    def verify(self) -> list[str]:
        return self._verify_rows(self.rows())

    def _verify_rows(self, rows: list[dict[str, Any]]) -> list[str]:
        problems: list[str] = []
        prev = GENESIS
        for index, row in enumerate(rows, start=1):
            if row.get("seq") != index:
                problems.append(f"{self.path.name} row {index}: sequence is {row.get('seq')}, expected {index}")
            if row.get("prev_sha256") != prev:
                problems.append(f"{self.path.name} row {index}: does not chain to the row before it")
            expected = sha256_bytes(canonical({k: v for k, v in row.items() if k != "row_sha256"}))
            if row.get("row_sha256") != expected:
                problems.append(f"{self.path.name} row {index}: content was changed after it was written")
            prev = row.get("row_sha256", "")
        return problems

    def find(self, **match: Any) -> list[dict[str, Any]]:
        return [r for r in self.rows() if all(r.get(k) == v for k, v in match.items())]


class Registers:
    """The four registers the supervisor keeps under its state directory."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.requests = Ledger(self.state_dir / "registers" / "requests.jsonl")
        self.approvals = Ledger(self.state_dir / "registers" / "approvals.jsonl")
        self.evidence = Ledger(self.state_dir / "registers" / "evidence.jsonl")
        self.decisions = Ledger(self.state_dir / "registers" / "decisions.jsonl")

    def all(self) -> Iterable[Ledger]:
        return (self.requests, self.approvals, self.evidence, self.decisions)

    def verify(self) -> list[str]:
        return [problem for ledger in self.all() for problem in ledger.verify()]

    # -- evidence -------------------------------------------------------------------------

    def record_evidence(
        self,
        evidence_id: str,
        subject: str,
        evidence_type: str,
        result: str,
        recorded_by: str,
        root: Path,
        artifacts: Iterable[str] = (),
        command: list[str] | None = None,
        notes: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if result not in {"PASS", "FAIL", "NOT_TESTED"}:
            raise RegisterError("result must be PASS, FAIL or NOT_TESTED")
        listed = []
        for relative in artifacts:
            path = (Path(root) / relative).resolve()
            if not path.is_file():
                raise RegisterError(f"evidence artifact not found: {relative}")
            listed.append({"path": str(relative).replace("\\", "/"), "sha256": sha256_file(path)})
        return self.evidence.append({
            "kind": "evidence",
            "id": evidence_id,
            "subject": subject,
            "evidence_type": evidence_type,
            "result": result,
            "recorded_by": recorded_by,
            "command": command or [],
            "artifacts": listed,
            "notes": notes,
            "at": iso(now or utc_now()),
        })

    def evidence_freshness(self, evidence_id: str, root: Path, max_age_hours: float = 168, now: datetime | None = None) -> tuple[str, str]:
        """CURRENT, STALE or INVALID, with the reason.

        INVALID: the row is missing, the chain is broken, or an artifact no longer matches its hash.
        STALE: everything matches but the evidence is older than `max_age_hours`.
        """
        if self.evidence.verify():
            return "INVALID", "evidence register failed verification"
        rows = self.evidence.find(id=evidence_id)
        if not rows:
            return "INVALID", f"no evidence row {evidence_id}"
        row = rows[0]
        for artifact in row["artifacts"]:
            path = Path(root) / artifact["path"]
            if not path.is_file():
                return "INVALID", f"artifact missing: {artifact['path']}"
            if sha256_file(path) != artifact["sha256"]:
                return "INVALID", f"artifact changed since it was recorded: {artifact['path']}"
        age = (now or utc_now()) - parse_iso(row["at"])
        if age > timedelta(hours=max_age_hours):
            return "STALE", f"recorded {age.days} days ago"
        return "CURRENT", "artifacts match what was recorded"

    # -- decisions ------------------------------------------------------------------------

    def propose_decision(
        self,
        decision_id: str,
        title: str,
        document: Path,
        required_approvers: list[str],
        evidence_ids: list[str],
        root: Path,
        proposed_by: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Evidence comes first: a decision must cite current evidence, or it is not recorded."""
        if not evidence_ids:
            raise RegisterError("a decision must cite at least one evidence row")
        for evidence_id in evidence_ids:
            level, reason = self.evidence_freshness(evidence_id, root, now=now)
            if level != "CURRENT":
                raise RegisterError(f"evidence {evidence_id} is {level}: {reason}")
        if not required_approvers:
            raise RegisterError("a decision needs at least one required approver")
        return self.decisions.append({
            "kind": "decision",
            "id": decision_id,
            "title": title,
            "document": str(document).replace("\\", "/"),
            "document_sha256": sha256_file(Path(root) / document),
            "required_approvers": sorted(required_approvers),
            "evidence_ids": list(evidence_ids),
            "proposed_by": proposed_by,
            "at": iso(now or utc_now()),
        })

    def confirm_decision(self, decision_id: str, approver: str, document_sha256: str, now: datetime | None = None) -> dict[str, Any]:
        decision = self._decision(decision_id)
        if approver not in decision["required_approvers"]:
            raise RegisterError(f"{approver} is not a required approver of {decision_id}")
        if document_sha256 != decision["document_sha256"]:
            raise RegisterError("the confirmed document is not the exact document this decision names")
        if self.decisions.find(kind="confirmation", decision_id=decision_id, approver=approver):
            raise RegisterError(f"{approver} has already confirmed {decision_id}")
        return self.decisions.append({
            "kind": "confirmation",
            "decision_id": decision_id,
            "approver": approver,
            "document_sha256": document_sha256,
            "at": iso(now or utc_now()),
        })

    def decision_status(self, decision_id: str) -> str:
        decision = self._decision(decision_id)
        confirmed = {
            row["approver"]
            for row in self.decisions.find(kind="confirmation", decision_id=decision_id)
            if row["document_sha256"] == decision["document_sha256"]
        }
        if self.decisions.find(kind="superseded", decision_id=decision_id):
            return "SUPERSEDED"
        return "ADOPTED" if confirmed >= set(decision["required_approvers"]) else "PROPOSED"

    def _decision(self, decision_id: str) -> dict[str, Any]:
        rows = self.decisions.find(kind="decision", id=decision_id)
        if not rows:
            raise RegisterError(f"no decision {decision_id}")
        return rows[0]
