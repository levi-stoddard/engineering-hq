# Ten-minute walkthrough

A script for showing the system live, using the example team. Everything runs offline. The demo approver tokens are `acme-demo-owner` and `acme-demo-reviewer`; they're only accepted because the example config says `demo: true`.

Start from a clean state (delete `examples/acme/state` and `examples/acme/releases` if they exist). Then:

```bash
pip install -r supervisor/requirements-dev.txt
alias hq='python supervisor/hq_supervisor.py --config examples/acme/config.yaml'   # PowerShell: function hq { python supervisor/hq_supervisor.py --config examples/acme/config.yaml @args }
```

## 1. The setup (1 min)

Open `examples/acme/config.yaml` and point out:
- the four action classes;
- `approvers` lists role names and token hashes, not people or tokens;
- the two switches;
- `checks`, the only commands a request can trigger, chosen by name.

## 2. Run the supervisor with the example config (1 min)

```bash
mkdir -p examples/acme/state/inbox
cp examples/acme/requests/REQ-acme-00{1,2,3,4}*.json examples/acme/state/inbox/
hq run --once
hq status
```

What to show:
- the routine test run is `done`;
- the prepare-only review is `draft_written` (open `examples/acme/state/drafts/REQ-acme-003.md`: "DRAFT, NOT SUBMITTED");
- the forbidden `edit_evidence` is `refused`;
- the release is `waiting_for_approval`.

## 3. Trigger a protected action and show the refusal (2 min)

The release doesn't run however often the loop passes over it: `hq run --once` again, still waiting. Then show the ways an approval is refused:
- a wrong token: `hq approve REQ-acme-002 --as owner --usd 2 --calls 3`, enter `wrong`. It's refused and nothing is recorded.
- the wrong id typed back: refused.
- no real terminal: `echo x | hq approve ...` refuses before asking anything.

## 4. Add an approval (1 min)

```bash
hq approve REQ-acme-002 --as owner --usd 2 --calls 3 --hours 24     # token: acme-demo-owner, then type REQ-acme-002
hq run --once
hq status                                                          # REQ-acme-002 done
cat examples/acme/releases/CURRENT                                 # 1.4.0
```

## 5. Show the registers and the manifest (2 min)

```bash
cat examples/acme/state/registers/approvals.jsonl                  # bound to request_sha256, budget, expiry
cat examples/acme/state/registers/evidence.jsonl                   # test run + release_activated, artifacts hashed
cat examples/acme/releases/1.4.0/MANIFEST.json                     # every file pinned; no timestamps
hq verify
```

Then break something, to show the chain catches it. Edit any number in `approvals.jsonl` and run `hq verify` (it fails). Also run `hq status` and `hq run --once`: the release can't run while the chain is broken. Undo the edit and verify again.

## 6. Second release and rollback (2 min)

```bash
cp examples/acme/requests/REQ-acme-005*.json examples/acme/requests/REQ-acme-006*.json examples/acme/state/inbox/
hq run --once
hq approve REQ-acme-005 --as reviewer --usd 2 --calls 3            # token: acme-demo-reviewer
hq run --once && cat examples/acme/releases/CURRENT                # 1.4.1
hq approve REQ-acme-006 --as owner --usd 1 --calls 1
hq run --once && cat examples/acme/releases/CURRENT                # back to 1.4.0
cat examples/acme/releases/history.jsonl
```

To show the rollback guard: before approving the rollback, edit `examples/acme/releases/1.4.0/CHANGELOG.md`. The rollback then fails with "failed verification; rollback refused", and `CURRENT` stays on 1.4.1.

## 7. Close (1 min)

- **The status page:**
  1. Run `python supervisor/status_server.py --config examples/acme/config.yaml`.
  2. Open `http://127.0.0.1:8765/api/requests`.

  It's read-only, and it has no approve button, on purpose.
- **The Slack side:** run `cd slack-agent && npm start`, type `merge acme/thermostat-firmware#7`, then `approve REQ-acme-002`.

---

## The eight hardest questions

**1. What stops an agent from approving its own work?**
Approvals only come from the console, after a token check against a hash in the config, and agents never hold approver tokens. The policy also refuses an approval from the request's own requester. The approval, withdrawal and policy-changing actions themselves are in the forbidden class.

**2. A hash chain in a JSON file isn't tamper-proof. Why bother?**
It isn't, and the docs don't claim it is. It catches accidental and casual edits: any edited, deleted or reordered row is detected, and protected work stops until the chain is repaired. Defending against someone who can rewrite every file would need signatures or an external anchor, and that's the next step I'd add.

**3. Why a terminal instead of an "Approve" button in Slack or on the dashboard?**
A chat message or a click on an unauthenticated local page can't prove who sent it, and an agent with chat access could fake one. At a terminal the person proves identity with a token only they hold, and confirms by typing the exact request id.

**4. What happens if an approval goes stale between approving and running?**
The supervisor evaluates the request again immediately before acting. An expired or withdrawn approval doesn't run, and neither does one that was for a different version of the request (the hash no longer matches). Holding execution behind a switch also lets a team approve during the day and run later.

**5. Why treat unknown actions as protected rather than refusing them?**
Refusing would push people to over-classify things as routine just to get work done. Protected keeps the new capability usable, with a person in the loop, until someone deliberately classifies it.

**6. How do you know the rollback target is the thing you approved originally?**
Each release is pinned by a manifest of file hashes, and rollback verifies the previous release against it before switching. If anything changed, the rollback is refused and the current release stays live. A mismatch means it's no longer the approved artifact.

**7. How is a spend limit enforced when you can't see the bill in real time?**
Every approval states a budget, and each approver can have a rolling-window limit summed over everything they approved, withdrawn or expired ones included. It limits committed budgets, not metered charges, and I say so in the docs. Metering is a separate piece that would turn it into a hard stop.

**8. What would you change to run this for a larger team?**
- Move the registers into a database with append-only permissions, and sign approvals.
- Replace the inbox folder with an authenticated API.
- Add per-repository policy files reviewed like code.

The rules at the centre wouldn't change: action classes, approvals bound to exact requests, evidence before decisions, and verify-before-switch.
