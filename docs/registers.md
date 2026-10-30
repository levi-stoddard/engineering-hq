# Registers

The supervisor keeps four registers under `<state_dir>/registers/`:

| Register | One row per | Written by |
|---|---|---|
| `requests.jsonl` | proposed request, and each change of its status | the supervisor |
| `approvals.jsonl` | approval or withdrawal | an approver, through the console |
| `evidence.jsonl` | measured result (a check run, a release activation, a rollback, a review) | the supervisor, or anyone recording a result |
| `decisions.jsonl` | decision, each confirmation, and supersession | anyone proposes; only required approvers confirm |

I kept them separate so each one answers one question:
- requests: what was asked;
- approvals: who allowed what;
- evidence: what was measured;
- decisions: what we agreed.

## Rules shared by all four

- **Append-only.** No row is edited or deleted. A change of state is a new row that refers to the old one.
- **Hash-chained.** Each row has the next sequence number, plus:
  - `prev_sha256`: the previous row's hash;
  - `row_sha256`: the SHA-256 of the row itself, as canonical JSON (sorted keys, no whitespace, UTF-8).

  `verify()` reports any edited, removed or reordered row.
- **A broken chain stops the work.** Nothing more is appended to a register that fails verification, and the policy refuses every protected action while any register is broken.
- **Ids are unique within a register.**

Schemas: [`schemas/`](../schemas). The tests validate both the committed sample rows and freshly written rows against them.

## Evidence

Purpose: record what was actually measured, in a form that goes stale honestly.

```json
{
  "kind": "evidence",
  "id": "EV-REQ-acme-001-1",
  "subject": "unit-tests",
  "evidence_type": "check_run",
  "result": "PASS",
  "recorded_by": "supervisor",
  "command": ["unit-tests"],
  "artifacts": [{ "path": "state/runs/REQ-acme-001.log", "sha256": "1fbc04e7...c5a7" }],
  "notes": "request REQ-acme-001",
  "at": "2026-10-30T19:43:48Z",
  "seq": 1,
  "prev_sha256": "0000...0000",
  "row_sha256": "f5c7747e...9c9b"
}
```

- **Results.** `PASS`, `FAIL` or `NOT_TESTED`. There's no "looks good".
- **Hashed artifacts.** Every artifact is hashed when the row is written, so later the evidence can be classified:
  - **CURRENT**: every artifact still matches, and the row is within its age window (168 hours by default);
  - **STALE**: everything matches, but the row is older than the window;
  - **INVALID**: the row is missing, the chain is broken, or an artifact changed or vanished.

## Decisions

Purpose: record what was agreed, by whom, about which exact document, on which evidence.

```json
{ "kind": "decision", "id": "DEC-001", "title": "Ship thermostat firmware 1.4.0",
  "document": "decisions/DEC-001-ship-1.4.0.md", "document_sha256": "7eef9e66...d8db",
  "required_approvers": ["owner", "reviewer"], "evidence_ids": ["EV-REQ-acme-001-1"],
  "proposed_by": "implementer", "at": "2026-10-30T19:43:48Z", "seq": 1, "...": "chain fields" }

{ "kind": "confirmation", "decision_id": "DEC-001", "approver": "owner",
  "document_sha256": "7eef9e66...d8db", "at": "2026-10-30T19:43:48Z", "seq": 2, "...": "chain fields" }
```

- **Evidence first.** A decision can only be proposed if every evidence id it cites is CURRENT.
- **Status is derived from the rows:**
  - `PROPOSED` until every required approver has a confirmation row with the decision's exact document hash;
  - then `ADOPTED`;
  - `SUPERSEDED` once a supersession row exists.
- **One confirmation per approver.** A confirmation for a different hash is refused.

## Approvals

Purpose: record each approval of a protected request, so it can be checked again at the moment of execution.

```json
{ "kind": "approval", "id": "APR-REQ-acme-002-1", "request_id": "REQ-acme-002",
  "request_sha256": "1bec6cc8...1a1e", "action": "release", "approver": "owner",
  "budget_usd": 2.0, "max_calls": 3, "expires": "2026-10-31T19:43:48Z",
  "at": "2026-10-30T19:43:48Z", "seq": 1, "...": "chain fields" }
```

`request_sha256` is the hash of the request row's content. If the request is changed or replaced, the approval no longer matches and the request goes back to waiting.

A withdrawal is `{ "kind": "withdrawal", "request_id", "by", "reason", "at" }`.

## Requests

```json
{ "kind": "request", "id": "REQ-acme-002", "action": "release", "requester": "implementer",
  "summary": "Release thermostat firmware 1.4.0 to the update channel.",
  "payload": { "component": "thermostat-firmware", "version": "1.4.0", "source": "thermostat-fw-1.4.0", "decision_id": "DEC-001" },
  "at": "...", "seq": 4, "...": "chain fields" }

{ "kind": "status", "request_id": "REQ-acme-002", "status": "waiting_for_approval",
  "detail": "protected action: needs a recorded approval", "at": "...", "...": "chain fields" }
```

Statuses:
- `proposed`
- `waiting_for_approval`
- `authorized_held`
- `running`
- `done`
- `failed`
- `refused`
- `draft_written`
- `withdrawn`
- `allowed_held` (allowed, but no handler is configured for the action)

A complete set of registers from the example scenario is in [`examples/acme/sample/registers/`](../examples/acme/sample/registers). It was produced by `python examples/acme/make_sample.py`.
