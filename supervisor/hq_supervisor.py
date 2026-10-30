"""Engineering HQ supervisor.

Two jobs, one loop:
  1. Requests. Agents drop proposed requests into `<state_dir>/inbox/`. Each one is recorded in the
     requests register, classified by the policy, and then run, held, or refused. Protected requests
     wait until an approver records an approval at a terminal (`approve`).
  2. Services. Keeps a fixed set of local services up: fixed argument lists, no shell, 127.0.0.1 only,
     health checked by a marker in the response body. A port held by something it did not start is
     left alone and reported, never killed.

Commands:
  run       the loop (add --once to process requests a single time and exit)
  propose   record a request file directly (same checks as the inbox)
  status    requests and their current verdicts
  approve   approver only: approve a protected request (token asked at the terminal)
  withdraw  approver only: refuse a request
  decide    propose or confirm a decision (evidence first; confirmations need the approver's token)
  evidence  record an evidence row for files that exist now
  verify    check every register chain, every staged release and every vault snapshot
"""

from __future__ import annotations

import argparse
import ctypes
import getpass
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ConfigError, expand_argv, load_config  # noqa: E402
from manifest import ManifestError, ReleaseStore  # noqa: E402
from policy import Policy, PolicyError  # noqa: E402
from registers import RegisterError, Registers, iso, sha256_file, utc_now  # noqa: E402
from vault import Vault, VaultError  # noqa: E402

REQUEST_ID = re.compile(r"^REQ-[A-Za-z0-9-]{1,40}$")
ACTION = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
TERMINAL = {"done", "failed", "refused", "draft_written", "withdrawn"}
STATE_SCHEMA = "engineering-hq.supervisor-state.v1"
USER_AGENT = "engineering-hq-supervisor/1.0"


# -- process helpers ----------------------------------------------------------------------

def pid_alive(pid: int) -> bool:
    """True if a process with this id is running. Never sends a signal on Windows (os.kill would terminate it)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class KillOnCloseJob:
    """Windows only: children join a job object that the OS closes when the supervisor exits for any
    reason, including a crash or a forced stop, so no supervised service is left running behind it."""

    def __init__(self) -> None:
        self.handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes

        class Limits(ctypes.Structure):
            _fields_ = [("a", ctypes.c_longlong), ("b", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD),
                        ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", wintypes.DWORD), ("f", ctypes.c_size_t),
                        ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Limits), ("Io", ctypes.c_ulonglong * 6), ("i", ctypes.c_size_t), ("j", ctypes.c_size_t),
                        ("k", ctypes.c_size_t), ("m", ctypes.c_size_t)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel32 = kernel32
        self.handle = kernel32.CreateJobObjectW(None, None)
        info = Extended()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not kernel32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if self.handle and not self.kernel32.AssignProcessToJobObject(self.handle, int(process._handle)):  # type: ignore[attr-defined]
            process.kill()
            raise ctypes.WinError(ctypes.get_last_error())


class SingleInstance:
    """A lock file holding the supervisor's PID. A stale lock (dead PID) is taken over."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> "SingleInstance":
        if self.path.exists():
            try:
                pid = int(self.path.read_text(encoding="ascii").strip())
            except ValueError:
                pid = -1
            if pid != os.getpid() and pid_alive(pid):
                raise SystemExit(f"another supervisor is running (pid {pid}); not starting a second one")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"{os.getpid()}\n", encoding="ascii")
        return self

    def __exit__(self, *_exc: object) -> None:
        try:
            if self.path.read_text(encoding="ascii").strip() == str(os.getpid()):
                self.path.unlink()
        except OSError:
            pass


# -- services -----------------------------------------------------------------------------

def port_in_use(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
        return True
    except OSError:
        return False


def health_ok(port: int, path: str, marker: str, timeout: float = 1.5) -> bool:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"Host": f"127.0.0.1:{port}", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200 and marker in response.read(300_000).decode("utf-8", "replace")
    except Exception:
        return False


class ManagedService:
    def __init__(self, spec: dict[str, Any], base: Path, log_dir: Path, job: KillOnCloseJob | None = None) -> None:
        self.spec = spec
        self.job = job
        self.base = base
        self.log_dir = log_dir
        self.process: subprocess.Popen[bytes] | None = None
        self.log_handle: Any = None
        self.status = "initializing"
        self.detail = ""
        self.failures = 0
        self.retry_after = 0.0

    @property
    def name(self) -> str:
        return str(self.spec["name"])

    def _healthy(self) -> bool:
        return health_ok(int(self.spec["port"]), str(self.spec["health"]), str(self.spec["marker"]))

    def tick(self) -> None:
        if self.process is not None:
            code = self.process.poll()
            if code is not None:
                self._close_log()
                self.process = None
                self.failures += 1
                self.retry_after = time.monotonic() + min(30, 2 ** min(self.failures, 5))
                self.status, self.detail = "crashed", f"exit code {code}"
                return
            if self._healthy():
                self.status, self.detail, self.failures = "healthy", "started by the supervisor", 0
            else:
                self.status, self.detail = "starting", "waiting for health"
            return
        if port_in_use(int(self.spec["port"])):
            if self._healthy():
                self.status, self.detail = "reused", "a healthy instance was already running; left as is"
            else:
                self.status, self.detail = "blocked_foreign", "port held by something else; nothing touched"
            return
        self._start()

    def _start(self) -> None:
        now = time.monotonic()
        if now < self.retry_after:
            return
        cwd = (self.base / self.spec.get("cwd", ".")).resolve()
        argv = expand_argv(list(self.spec["argv"]), sys.executable)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_handle = open(self.log_dir / f"{self.name}.log", "ab", buffering=0)
        try:
            self.process = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=self.log_handle,
                stderr=subprocess.STDOUT,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if self.job:
                self.job.assign(self.process)
            self.status, self.detail = "starting", f"pid {self.process.pid}"
        except OSError as exc:
            self._close_log()
            self.failures += 1
            self.retry_after = now + min(30, 2 ** min(self.failures, 5))
            self.status, self.detail = "start_failed", type(exc).__name__

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        self._close_log()

    def _close_log(self) -> None:
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "port": self.spec["port"],
            "status": self.status,
            "detail": self.detail,
            "pid": self.process.pid if self.process else None,
            "owned_by_supervisor": self.process is not None,
        }


# -- requests -----------------------------------------------------------------------------

class Supervisor:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.base = Path(config["_base"])
        self.state_dir = Path(config["state_dir"])
        self.registers = Registers(self.state_dir)
        self.policy = Policy.from_config(config)
        self.releases = ReleaseStore(Path(config["releases"]["dir"]))
        self.release_sources = Path(config["releases"]["sources"])
        self.vault = Vault(Path(config["vault"]["dir"]))

    # intake

    def propose(self, request: dict[str, Any]) -> dict[str, Any]:
        errors = validate_request(request)
        if errors:
            raise RegisterError("; ".join(errors))
        row = self.registers.requests.append({
            "kind": "request",
            "id": request["id"],
            "action": request["action"],
            "requester": request["requester"],
            "summary": request["summary"],
            "payload": request.get("payload", {}),
            "at": iso(utc_now()),
        })
        self._set_status(request["id"], "proposed", f"class: {self.policy.classify(request['action']).value}")
        return row

    def intake(self) -> list[str]:
        inbox = self.state_dir / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        processed = inbox / "processed"
        notes = []
        for path in sorted(inbox.glob("*.json")):
            try:
                request = json.loads(path.read_text(encoding="utf-8"))
                self.propose(request)
                notes.append(f"recorded {request['id']}")
                outcome = "accepted"
            except (ValueError, KeyError, RegisterError) as exc:
                notes.append(f"rejected {path.name}: {exc}")
                outcome = "rejected"
            processed.mkdir(exist_ok=True)
            os.replace(path, processed / f"{path.stem}.{outcome}.json")
        return notes

    # status tracking

    def status_of(self, request_id: str) -> tuple[str, str]:
        rows = self.registers.requests.find(kind="status", request_id=request_id)
        return (rows[-1]["status"], rows[-1]["detail"]) if rows else ("unknown", "")

    def _set_status(self, request_id: str, status: str, detail: str) -> None:
        if self.status_of(request_id) != (status, detail):
            self.registers.requests.append({
                "kind": "status",
                "request_id": request_id,
                "status": status,
                "detail": detail,
                "at": iso(utc_now()),
            })

    def requests(self) -> list[dict[str, Any]]:
        return self.registers.requests.find(kind="request")

    # one pass over open requests

    def process(self) -> list[str]:
        notes = self.intake()
        for request in self.requests():
            status, _ = self.status_of(request["id"])
            if status in TERMINAL:
                continue
            verdict = self.policy.evaluate(self.registers, request["id"])
            if verdict.outcome == "refused":
                final = "withdrawn" if "withdrawn" in verdict.reason else "refused"
                self._set_status(request["id"], final, verdict.reason)
            elif verdict.outcome == "draft_only":
                self._run(request, None, draft=True)
            elif verdict.outcome in {"allowed", "authorized"}:
                self._run(request, verdict.approval_id)
            else:
                self._set_status(request["id"], verdict.outcome, verdict.reason)
            notes.append(f"{request['id']} {request['action']}: {self.status_of(request['id'])[0]}")
        return notes

    def _run(self, request: dict[str, Any], approval_id: str | None, draft: bool = False) -> None:
        handler = HANDLERS.get(request["action"])
        if draft:
            handler = handle_prepare_draft
        if handler is None:
            self._set_status(request["id"], "authorized_held" if approval_id else "allowed_held", "no handler is configured for this action")
            return
        # Re-check immediately before acting: an approval can expire or be withdrawn between passes.
        if not draft and self.policy.evaluate(self.registers, request["id"]).outcome not in {"allowed", "authorized"}:
            return
        self._set_status(request["id"], "running", "handler started")
        try:
            status, detail = handler(self, request, approval_id)
        except (ManifestError, VaultError, RegisterError, PolicyError, OSError, KeyError, ValueError) as exc:
            status, detail = "failed", f"{type(exc).__name__}: {exc}"
        self._set_status(request["id"], status, detail)

    def evidence_id(self, request_id: str) -> str:
        return f"EV-{request_id}-{len(self.registers.evidence.rows()) + 1}"


def validate_request(request: Any) -> list[str]:
    if not isinstance(request, dict):
        return ["a request is a JSON object"]
    errors = []
    if not REQUEST_ID.match(str(request.get("id", ""))):
        errors.append("id must look like REQ-<letters, digits, dashes>")
    if not ACTION.match(str(request.get("action", ""))):
        errors.append("action must be a lowercase identifier")
    if not ROLE.match(str(request.get("requester", ""))):
        errors.append("requester must be a role name such as implementer")
    if not isinstance(request.get("summary"), str) or not 0 < len(request["summary"]) <= 500:
        errors.append("summary must be 1-500 characters")
    if not isinstance(request.get("payload", {}), dict):
        errors.append("payload must be an object")
    unknown = set(request) - {"id", "action", "requester", "summary", "payload", "$schema"}
    if unknown:
        errors.append(f"unknown fields: {sorted(unknown)}")
    return errors


# -- handlers: the only things a request can make the supervisor do -----------------------

def handle_run_check(sup: Supervisor, request: dict[str, Any], _approval: str | None) -> tuple[str, str]:
    """Runs a check defined in the config by name. The request picks a name; it never supplies a command."""
    name = request["payload"].get("check", "")
    check = (sup.config.get("checks") or {}).get(name)
    if check is None:
        return "refused", f"no check named {name!r} in the config"
    argv = expand_argv(list(check["argv"]), sys.executable)
    cwd = (sup.base / check.get("cwd", ".")).resolve()
    result = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, shell=False, timeout=int(check.get("timeout", 300)))
    log_path = sup.state_dir / "runs" / f"{request['id']}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"$ {' '.join(argv)}\nexit {result.returncode}\n\n{result.stdout}{result.stderr}", encoding="utf-8")
    outcome = "PASS" if result.returncode == 0 else "FAIL"
    sup.registers.record_evidence(
        sup.evidence_id(request["id"]), subject=name, evidence_type="check_run", result=outcome,
        recorded_by="supervisor", root=sup.base, artifacts=[os.path.relpath(log_path, sup.base)],
        command=[name], notes=f"request {request['id']}",
    )
    return ("done" if outcome == "PASS" else "failed"), f"check {name}: {outcome}"


def handle_prepare_draft(sup: Supervisor, request: dict[str, Any], _approval: str | None) -> tuple[str, str]:
    draft = sup.state_dir / "drafts" / f"{request['id']}.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(request["payload"], indent=2)
    draft.write_text(
        f"# Draft: {request['summary']}\n\nStatus: DRAFT, NOT SUBMITTED. A draft changes nothing.\n\n"
        f"Requested by: {request['requester']}\n\n```json\n{body}\n```\n",
        encoding="utf-8",
    )
    return "draft_written", os.path.relpath(draft, sup.base).replace("\\", "/")


def _inside(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise ValueError(f"{relative!r} is outside the allowed folder")
    return target


def handle_release(sup: Supervisor, request: dict[str, Any], approval_id: str | None) -> tuple[str, str]:
    payload = request["payload"]
    source = _inside(sup.release_sources, payload["source"])
    manifest = sup.releases.stage(source, payload["component"], payload["version"], payload.get("decision_id"))
    sup.releases.activate(payload["version"], actor="supervisor", approval_id=approval_id)
    manifest_path = sup.releases.path(payload["version"]) / "MANIFEST.json"
    sup.registers.record_evidence(
        sup.evidence_id(request["id"]), subject=f"{payload['component']} {payload['version']}", evidence_type="release_activated",
        result="PASS", recorded_by="supervisor", root=sup.base, artifacts=[os.path.relpath(manifest_path, sup.base)],
        notes=f"request {request['id']}, approval {approval_id}, manifest {manifest['manifest_sha256']}",
    )
    return "done", f"released {payload['version']} (manifest {manifest['manifest_sha256'][:12]})"


def handle_rollback(sup: Supervisor, request: dict[str, Any], approval_id: str | None) -> tuple[str, str]:
    row = sup.releases.rollback(actor="supervisor", approval_id=approval_id)
    sup.registers.record_evidence(
        sup.evidence_id(request["id"]), subject=f"rollback to {row['version']}", evidence_type="rollback",
        result="PASS", recorded_by="supervisor", root=sup.base,
        artifacts=[os.path.relpath(sup.releases.path(row["version"]) / "MANIFEST.json", sup.base)],
        notes=f"request {request['id']}, approval {approval_id}, from {row['previous']}",
    )
    return "done", f"rolled back from {row['previous']} to {row['version']}"


def handle_promote_snapshot(sup: Supervisor, request: dict[str, Any], approval_id: str | None) -> tuple[str, str]:
    payload = request["payload"]
    approval = sup.registers.approvals.find(kind="approval", id=approval_id)[0]
    record = sup.vault.promote(payload["entry_id"], payload["version"], payload["snapshot_sha256"], approval_id or "", approval["approver"])
    return "done", f"{record['id']} {record['version']} is now active"


HANDLERS = {
    "run_check": handle_run_check,
    "release": handle_release,
    "rollback": handle_rollback,
    "promote_snapshot": handle_promote_snapshot,
}


# -- loop -----------------------------------------------------------------------------------

def write_state(sup: Supervisor, services: list[ManagedService]) -> None:
    counts: dict[str, int] = {}
    for request in sup.requests():
        status, _ = sup.status_of(request["id"])
        counts[status] = counts.get(status, 0) + 1
    atomic_json(sup.state_dir / "state.json", {
        "schema": STATE_SCHEMA,
        "updated_utc": iso(utc_now()),
        "supervisor_pid": os.getpid(),
        "loopback_only": True,
        "switches": {name: sup.policy.switch(name) for name in ("approvals_enabled", "execution_enabled")},
        "requests": counts,
        "current_release": sup.releases.current(),
        "register_problems": sup.registers.verify(),
        "services": {service.name: service.snapshot() for service in services},
    })


def run(config: dict[str, Any], once: bool, interval: float) -> int:
    sup = Supervisor(config)
    if once:
        for note in sup.process():
            print(note)
        write_state(sup, [])
        return 0
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except (ValueError, OSError):
            pass
    job = KillOnCloseJob()
    services = [ManagedService(spec, sup.base, sup.state_dir / "logs", job) for spec in config.get("services") or []]
    stop_file = sup.state_dir / "STOP"
    with SingleInstance(sup.state_dir / "supervisor.lock"):
        print(f"supervisor running (pid {os.getpid()}); state in {sup.state_dir}. Ctrl+C stops it and the services it started.")
        try:
            while not stop.is_set():
                if stop_file.exists():  # graceful stop requested by scripts/stop.ps1
                    stop_file.unlink()
                    break
                for note in sup.process():
                    if not note.endswith(("waiting_for_approval", "authorized_held")):
                        print(note)
                for service in services:
                    service.tick()
                write_state(sup, services)
                stop.wait(interval)
        finally:
            for service in services:
                service.stop()
            write_state(sup, services)
    return 0


# -- console commands ---------------------------------------------------------------------

def ask_token(name: str) -> str:
    if not sys.stdin.isatty():
        raise SystemExit("approver commands need a real terminal; tokens are never read from arguments, files or environment variables")
    return getpass.getpass(f"Token for {name} (hidden): ")


def cmd_status(sup: Supervisor) -> int:
    for request in sup.requests():
        status, detail = sup.status_of(request["id"])
        print(f"{request['id']:<28} {request['action']:<18} {sup.policy.classify(request['action']).value:<13} {status:<22} {detail}")
    return 0


def cmd_approve(sup: Supervisor, args: argparse.Namespace) -> int:
    token = ask_token(args.as_)
    sup.policy.verify_approver(args.as_, token)
    request = sup.registers.requests.find(kind="request", id=args.request_id)
    if not request:
        raise SystemExit(f"no request {args.request_id}")
    print(json.dumps({k: request[0][k] for k in ("id", "action", "requester", "summary", "payload")}, indent=2))
    typed = input("Type the request id to approve it: ").strip()
    row = sup.policy.record_approval(sup.registers, args.request_id, args.as_, token, typed, args.usd, args.calls, args.hours)
    print(f"Approved: {row['id']} (budget {row['budget_usd']} USD, {row['max_calls']} calls, expires {row['expires']}).")
    print("The supervisor acts on it in its next pass.")
    return 0


def cmd_withdraw(sup: Supervisor, args: argparse.Namespace) -> int:
    token = ask_token(args.as_)
    sup.policy.withdraw(sup.registers, args.request_id, args.as_, token, args.reason)
    print(f"Withdrawn: {args.request_id}. The record and its history are kept.")
    return 0


def cmd_decide(sup: Supervisor, args: argparse.Namespace) -> int:
    if args.decide_command == "propose":
        row = sup.registers.propose_decision(
            args.id, args.title, Path(args.document), args.approvers.split(","), args.evidence.split(","), sup.base, args.by,
        )
        print(f"Proposed {row['id']} (document sha256 {row['document_sha256'][:12]}...). Each required approver confirms it with their own token.")
        return 0
    if args.decide_command == "confirm":
        token = ask_token(args.as_)
        sup.policy.verify_approver(args.as_, token)
        decision = sup.registers.decisions.find(kind="decision", id=args.id)
        if not decision:
            raise SystemExit(f"no decision {args.id}")
        document_hash = sha256_file(sup.base / decision[0]["document"])
        sup.registers.confirm_decision(args.id, args.as_, document_hash)
        print(f"{args.id}: {sup.registers.decision_status(args.id)}")
        return 0
    for row in sup.registers.decisions.find(kind="decision"):
        print(f"{row['id']:<12} {sup.registers.decision_status(row['id']):<10} {row['title']}")
    return 0


def cmd_evidence(sup: Supervisor, args: argparse.Namespace) -> int:
    row = sup.registers.record_evidence(
        args.id, args.subject, args.type, args.result, args.by, sup.base, artifacts=args.artifact or [], notes=args.notes,
    )
    print(f"Recorded {row['id']} ({row['result']}, {len(row['artifacts'])} artifacts hashed).")
    return 0


def cmd_verify(sup: Supervisor) -> int:
    problems = list(sup.registers.verify())
    for version in sorted(p.name for p in sup.releases.root.glob("*") if (p / "MANIFEST.json").is_file()):
        problems += [f"release {version}: {p}" for p in sup.releases.verify(version)]
    problems += sup.releases.history.verify()
    snapshots = sup.vault.root / "snapshots"
    for entry in sorted(snapshots.glob("*/*")) if snapshots.exists() else []:
        problems += [f"vault {entry.parent.name} {entry.name}: {p}" for p in sup.vault.verify(entry.parent.name, entry.name)]
    for problem in problems:
        print(f"FAIL {problem}")
    print("All registers, releases and snapshots verified." if not problems else f"{len(problems)} problem(s).")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hq_supervisor", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="path to the YAML config")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--once", action="store_true")
    run_parser.add_argument("--interval", type=float, default=2.0)
    propose_parser = commands.add_parser("propose")
    propose_parser.add_argument("file")
    commands.add_parser("status")
    commands.add_parser("verify")
    approve_parser = commands.add_parser("approve")
    approve_parser.add_argument("request_id")
    approve_parser.add_argument("--as", dest="as_", required=True)
    approve_parser.add_argument("--usd", type=float, required=True)
    approve_parser.add_argument("--calls", type=int, required=True)
    approve_parser.add_argument("--hours", type=float, default=24)
    withdraw_parser = commands.add_parser("withdraw")
    withdraw_parser.add_argument("request_id")
    withdraw_parser.add_argument("--as", dest="as_", required=True)
    withdraw_parser.add_argument("--reason", required=True)
    decide_parser = commands.add_parser("decide")
    decide_commands = decide_parser.add_subparsers(dest="decide_command", required=True)
    decide_commands.add_parser("list")
    propose_decision = decide_commands.add_parser("propose")
    for flag in ("--id", "--title", "--document", "--approvers", "--evidence", "--by"):
        propose_decision.add_argument(flag, required=True)
    confirm_decision = decide_commands.add_parser("confirm")
    confirm_decision.add_argument("id")
    confirm_decision.add_argument("--as", dest="as_", required=True)
    evidence_parser = commands.add_parser("evidence")
    for flag in ("--id", "--subject", "--type", "--by"):
        evidence_parser.add_argument(flag, required=True)
    evidence_parser.add_argument("--result", choices=["PASS", "FAIL", "NOT_TESTED"], required=True)
    evidence_parser.add_argument("--artifact", action="append")
    evidence_parser.add_argument("--notes", default="")
    args = parser.parse_args(argv)

    try:
        config = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"Refusing to start: {exc}", file=sys.stderr)
        return 2
    sup = Supervisor(config)
    try:
        if args.command == "run":
            return run(config, args.once, args.interval)
        if args.command == "propose":
            row = sup.propose(json.loads(Path(args.file).read_text(encoding="utf-8")))
            print(f"Recorded {row['id']} ({sup.policy.classify(row['action']).value}).")
            return 0
        if args.command == "status":
            return cmd_status(sup)
        if args.command == "verify":
            return cmd_verify(sup)
        if args.command == "approve":
            return cmd_approve(sup, args)
        if args.command == "withdraw":
            return cmd_withdraw(sup, args)
        if args.command == "decide":
            return cmd_decide(sup, args)
        if args.command == "evidence":
            return cmd_evidence(sup, args)
    except (PolicyError, RegisterError, ManifestError, VaultError) as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
