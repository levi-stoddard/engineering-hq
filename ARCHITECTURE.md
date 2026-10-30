# Architecture

I built this so that AI coding agents could do most of the implementation work on a small, remote team without anyone having to trust them blindly. The agents are fast and usually right. The system exists for the times they are confidently wrong, or are asked to do something nobody should do unattended.

## Components

| Component | Language | What it does |
|---|---|---|
| `supervisor/hq_supervisor.py` | Python | The loop: takes requests from the inbox, asks the policy what may happen, runs the allowed ones through fixed handlers, and keeps a small set of local services up |
| `supervisor/policy.py` | Python | Action classes, approvals, switches, spend limits |
| `supervisor/registers.py` | Python | Four append-only, hash-chained JSONL registers: requests, approvals, evidence, decisions |
| `supervisor/manifest.py` | Python | Reproducible release manifests, staging, activation, rollback |
| `supervisor/vault.py` | Python | Simulation vault: read-only snapshots, staging, approval by exact hash, archive |
| `supervisor/status_server.py` | Python | Read-only status page on 127.0.0.1 |
| `slack-agent/` | Node | Slack listener with read-only Drive, GitHub and Linear connectors. It answers questions, writes review packages, and turns change requests into supervisor requests |
| `scripts/` | PowerShell + Python | Start, stop, verified backup, read-only health check |
| `schemas/` | JSON Schema | The request, approval, evidence, decision and manifest formats, tested against what the code writes |

## Roles

The system knows roles, not people:
- `implementer`: a coding agent (Claude Code, Gemini, Hermes, Antigravity). It proposes requests.
- `slack-agent`: answers in chat and files requests on a person's behalf.
- `orchestrator`: whatever assigns work to implementers. It proposes requests too.
- `approver` (in the example config, `owner` and `reviewer`): a person with a token. Only an approver can approve, withdraw or confirm a decision.

No agent role can do any of the approver actions. A requester can't approve their own request, even when the requester is an approver.

## Data flow

```
implementer / orchestrator / slack-agent
        |  request JSON (id, action, requester role, summary, payload)
        v
  <state_dir>/inbox/  -->  requests register (proposed)
        |
        v
  policy.evaluate()  -- routine ------------> handler runs --> evidence row
        |            -- prepare_only -------> draft written, nothing runs
        |            -- forbidden ----------> refused
        |            -- protected ----------> waiting_for_approval
        |                                        |
        |               approver at a terminal: approve (token + typed id + budget + expiry)
        |                                        v
        |                               approvals register
        v                                        |
  evaluate again at execution time  <------------+
        |  (still valid? not expired, not withdrawn, request unchanged, chains intact, switch on)
        v
  handler: release / rollback / promote_snapshot --> manifest + history + evidence row
```

All state lives under the configured `state_dir`, `releases.dir` and `vault.dir`. All of it is gitignored, so it can be backed up with `scripts/backup.ps1`.

## Design decisions, and why

### Read-only connectors by default
The slack-agent can read the tracker, the repositories and one Drive folder, and that is all.
- **Two layers of enforcement.** Tokens are scoped read-only, and the code enforces it again: every live connector sends its requests through one helper that refuses anything but GET and HEAD. Linear's API is GraphQL over POST, so there the helper only passes documents that are a single `query`.
- **Why both.** If a token turns out to have broader scope than intended, the code still won't write.
- **Change requests become supervisor requests.** When someone asks the agent to merge, deploy or delete, it files a request for the supervisor instead.

### Protected-action classes, with unknown meaning protected
The policy has four classes: routine, prepare-only, protected and forbidden.
- **Unknown actions are protected.** When an agent gains a new capability, nobody has to remember to add it to a block list; it waits for a person by default.
- **Forbidden is not "protected with a high bar".** No approval unlocks it. It covers the things that would undermine the system itself: granting or enlarging approvals, editing evidence, changing the policy, recording a decision on an approver's behalf.

### Approval records are first-class rows
An approval is a row in its own register with the approver, budget, call limit, expiry, and the SHA-256 of the exact request it covers.
- **Re-checked at execution time.** The supervisor checks again just before acting, so an approval that expired, was withdrawn, or no longer matches the request doesn't run.
- **Withdrawals are rows too.** Nothing is ever edited.
- **Approvals only at a terminal.** The approver proves identity with their own token, typed into a hidden prompt, and confirms by typing the request id back. The CLI refuses to run without a real terminal, and there is no environment variable or flag that bypasses that.
- **No approve button.** The status page has none, on purpose: a click on an unauthenticated local page can't prove who made it.

### Spend limits on stated budgets
- **Every approval states a budget.** An approver can also have a rolling limit (in the example, 10 USD in any 168 hours) that sums every approval they made in the window, withdrawn or expired ones included.
- **Stated, not metered.** The limit applies to stated budgets, not metered charges. Metering actual provider spend is a separate problem, and I didn't want this to claim more than it enforces.

### Switches default off
- **Two switches.** `approvals_enabled` is a kill switch for new approvals. `execution_enabled` decides whether approved work runs or is held.
- **Off unless set.** A switch the config doesn't mention is off, so a new install starts by holding everything.

### Evidence before decisions
- **Hashed when recorded.** Evidence rows hash every artifact at the moment they're recorded.
- **Three levels.** Evidence is `CURRENT` while the artifacts still match, `STALE` when it's older than its window, and `INVALID` when an artifact changed or vanished.
- **A decision must cite current evidence, or it isn't recorded.**
- **Adoption needs every confirmation.** A decision names its document by SHA-256 and is adopted only when every required approver has confirmed that same hash. Editing the document after one person confirmed means the confirmations no longer add up.

### Hash-chained registers, and what that does and doesn't prove
- **What the chain catches.** Each row carries the hash of the previous row and of itself, so editing, deleting or reordering a row is detected. While a chain is broken, the supervisor refuses every protected action, and new rows can't be appended.
- **What it doesn't prove.** This catches accidents and casual edits. It is not proof against someone who can rewrite every file and recompute every hash. That would need signatures or an external anchor, which is a reasonable next step and not something I'm claiming here.

### Reproducible manifests and tested rollback
- **Reproducible by design.** A manifest lists every file with its SHA-256 and size, plus a tree hash. It has no timestamps or machine paths, so the same files always produce the same `manifest_sha256`. That's tested, including across case-sensitive and case-insensitive filesystems.
- **Releases are immutable.** A release folder is never overwritten; a changed file is a new version.
- **Verify before switching.** Activation and rollback both verify the target against its manifest first. A damaged previous release blocks the rollback instead of being restored.

### Local services: loopback, fixed commands, never kill what you didn't start
- **Fixed commands, loopback only.** The supervisor starts services from fixed argument lists, with no shell, and checks their health on 127.0.0.1 with a marker string in the response body.
- **Never kill what it didn't start.** If a port is already held by something healthy, the supervisor reuses it. If it's held by something else, the supervisor reports it and touches nothing.
- **No orphans on Windows.** Children join a kill-on-close job object, so they never outlive the supervisor, even after a crash.
- **Stop only what was recorded.** `stop.ps1` stops only the process `start.ps1` recorded, and only if its command line still matches.

### Requests name things; they never carry commands
- **Fixed handlers.** A routine `run_check` request names a check defined in the config. A release request names a version and a folder that must sit inside the configured sources folder.
- **Nothing from a request is executed.** That holds even when an agent is confused or has been prompt-injected.

## Limits I know about

- **No metering.** Spend limits are enforced on approved budgets, not on metered usage.
- **No signatures.** The hash chain detects tampering but isn't signed.
- **Single machine.** There's no multi-host coordination. The original ran on one machine, with a second person connecting over a private network, and I left the networking out.
- **Windows-first scripts.** The PowerShell start, stop and backup scripts assume Windows. The Python and Node code is cross-platform, and CI runs the Python tests on Linux and Windows.
- **One option-style setting.** The slack-agent's tone (`AGENT_TONE=dry`) only adds a remark to refused change requests.
