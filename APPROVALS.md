# Approvals

How a protected action gets approved, who can approve it, and what doesn't count as an approval.

## Roles

| Role | Can propose | Can approve, withdraw, confirm decisions |
|---|---|---|
| `implementer` (coding agent) | yes | no |
| `orchestrator` | yes | no |
| `slack-agent` | yes, on a person's behalf | no |
| approver (`owner`, `reviewer`, ...) | yes | yes, never for their own request |

Approvers are listed in the config by role name with the SHA-256 of their token. Each approver creates their own token with `python scripts/new_token.py`, alone, and keeps it in a password manager. The token is never stored by the system, never typed on a command line, in chat or in a file.

## What is waiting for me?

```bash
python supervisor/hq_supervisor.py --config <config> status
```

Requests in `waiting_for_approval` are yours to decide. The read-only status page (`/api/requests`) shows the same list.

## Approve a request

```bash
python supervisor/hq_supervisor.py --config <config> approve REQ-acme-002 --as owner --usd 2 --calls 3 --hours 24
```

1. You're asked for your token (hidden). A wrong token stops here and records nothing.
2. The request is shown: action, requester, summary, payload.
3. You type the request id to confirm.
4. The approval is recorded with:
   - your role;
   - the budget (USD) and the call limit;
   - the expiry (at most `max_approval_hours`, 168 in the example);
   - the SHA-256 of the request you just read.

It is refused, and nothing is recorded, when:
- the command isn't running in a real terminal;
- approvals are switched off;
- the request isn't protected, or isn't waiting;
- you are the requester;
- the budget or call limit isn't positive, or the expiry is too long;
- your rolling spend limit would be exceeded;
- any register chain fails verification.

The supervisor acts on its next pass. It checks the approval again just before acting. If execution is switched off, the request is shown as `authorized_held` and nothing runs.

## Refuse a request

```bash
python supervisor/hq_supervisor.py --config <config> withdraw REQ-acme-005 --as reviewer --reason "soak test not finished"
```

A withdrawal is its own row. The request and its history are kept; it can't run afterwards.

## Decisions

A decision is broader than one request, for example "ship 1.4.0".
1. **Propose it (anyone).** Cite current evidence and name the document:
   ```bash
   python supervisor/hq_supervisor.py --config <config> decide propose --id DEC-001 --title "Ship 1.4.0" \
     --document decisions/DEC-001-ship-1.4.0.md --approvers owner,reviewer --evidence EV-REQ-acme-001-1 --by implementer
   ```
2. **Each required approver confirms it with their own token.**
   ```bash
   python supervisor/hq_supervisor.py --config <config> decide confirm DEC-001 --as owner
   ```

The confirmation hashes the document as it is now. If it changed since the decision was proposed, the confirmation is refused. The decision is ADOPTED only when every required approver has confirmed the same hash. Relaying someone else's agreement isn't a confirmation.

## What is not an approval

- A message in Slack, from anyone. The slack-agent says so and files nothing.
- A request that passes its checks, or a review package marked READY_FOR_REVIEW.
- A click on any page. There is no approve button anywhere.
- An approval of a different version of the request, an expired one, or a withdrawn one.
- An approval of another snapshot or release. An approval covers exactly the hash it names.

## Switches

In the config:
- `approvals_enabled: false` refuses every new approval. It's the kill switch.
- `execution_enabled: false` holds approved work instead of running it.

Both default to off when the config doesn't mention them.
