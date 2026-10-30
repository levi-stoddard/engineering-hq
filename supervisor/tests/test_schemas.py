"""The published schemas describe what the code actually writes."""

import json

import pytest
from jsonschema import Draft202012Validator

from conftest import OWNER_TOKEN, REPO
from hq_supervisor import Supervisor

SCHEMAS = REPO / "schemas"
SAMPLE = REPO / "examples" / "acme" / "sample"


def validator(name):
    schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def errors(name, instance):
    return [e.message for e in validator(name).iter_errors(instance)]


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("request_file", sorted((REPO / "examples" / "acme" / "requests").glob("*.json")), ids=lambda p: p.stem)
def test_example_requests_match_the_request_schema(request_file):
    assert errors("approval-request.schema.json", json.loads(request_file.read_text(encoding="utf-8"))) == []


def test_sample_registers_and_manifest_match_their_schemas():
    for row in rows(SAMPLE / "registers" / "evidence.jsonl"):
        assert errors("evidence-register.schema.json", row) == []
    for row in rows(SAMPLE / "registers" / "decisions.jsonl"):
        assert errors("decision-register.schema.json", row) == []
    for row in rows(SAMPLE / "registers" / "approvals.jsonl"):
        assert errors("approval-record.schema.json", row) == []
    manifest = json.loads((SAMPLE / "release-manifest-1.4.0.json").read_text(encoding="utf-8"))
    assert errors("release-manifest.schema.json", manifest) == []


def test_rows_written_now_match_the_schemas(config, acme):
    sup = Supervisor(config)
    sup.propose(json.loads((acme / "requests" / "REQ-acme-002-release-1.4.0.json").read_text(encoding="utf-8")))
    sup.process()
    sup.policy.record_approval(sup.registers, "REQ-acme-002", "owner", OWNER_TOKEN, "REQ-acme-002", 1, 1, 24)
    sup.process()
    for row in sup.registers.approvals.rows():
        assert errors("approval-record.schema.json", row) == []
    for row in sup.registers.evidence.rows():
        assert errors("evidence-register.schema.json", row) == []
    assert errors("release-manifest.schema.json", sup.releases.manifest("1.4.0")) == []


def test_the_schema_rejects_a_request_that_carries_its_own_approval():
    request = {"id": "REQ-x", "action": "release", "requester": "implementer", "summary": "x", "approved": True}
    assert errors("approval-request.schema.json", request)
