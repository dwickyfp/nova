"""Queue envelopes reject secrets and unbounded or ambiguous control values."""

from dataclasses import replace
from uuid import uuid4

import pytest

from app.common.durable_jobs import EffectProof, JobDefinition, JobLease, JobRepository


def definition():
    return JobDefinition("task", "sql.execute", uuid4(), 1, "a" * 64, uuid4(), "b" * 64, True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("queue", "outbox"),
        ("queue", "unknown"),
        ("kind", "https://service/?api_key=canary"),
        ("kind", "SELECT secret FROM table"),
        ("kind", "x" * 65),
        ("definition_ref", "password=canary"),
        ("idempotency_key", "access_token=canary"),
        ("definition_version", True),
        ("definition_version", 0),
        ("definition_version", 2**63),
        ("definition_digest", "encrypted:canary"),
        ("definition_digest", "a" * 63),
        ("resource_key", "https://user:canary@host/path"),
        ("external_effect", "false"),
    ],
)
def test_queue_rejects_credential_bearing_or_ambiguous_fields(field, value):
    with pytest.raises(ValueError) as caught:
        replace(definition(), **{field: value})
    assert "canary" not in str(caught.value)


@pytest.mark.parametrize("token,revision", [(0, 1), (1, 0), (True, 1), (1, 2**63)])
def test_lease_requires_positive_bounded_token_and_revision(token, revision):
    with pytest.raises(ValueError):
        JobLease(uuid4(), uuid4(), token, revision)


@pytest.mark.parametrize(
    "evidence,digest",
    [
        ("provider-response=canary", "a" * 64),
        (uuid4(), "token=canary"),
        (uuid4(), "A" * 64),
    ],
)
def test_receipt_accepts_only_opaque_evidence_and_exact_digest(evidence, digest):
    with pytest.raises(ValueError) as caught:
        EffectProof(evidence, digest)
    assert "canary" not in str(caught.value)


@pytest.mark.parametrize("error", [None, "", "bad token=canary", "A" * 65, "OUTCOME_UNKNOWN\n"])
def test_freeform_errors_cannot_enter_durable_queue(error):
    with pytest.raises(ValueError) as caught:
        JobRepository._error(error)
    assert "canary" not in str(caught.value)
