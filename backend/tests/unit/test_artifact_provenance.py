"""Semantic View results stay savable without exposing or trusting client SQL."""

import pytest
from fastapi import HTTPException

from app.modules.agents import artifact_provenance

USER = {"username": "analyst", "active_role": "SALES", "roles": ["SALES"], "session_id": "s1"}


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key, value, ex=None):
        self.values[key] = value
        self.ttls[key] = ex

    async def get(self, key):
        return self.values.get(key)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(artifact_provenance.session_store, "_redis", fake)
    return fake


async def test_proof_round_trips_for_the_issuing_session(redis):
    identity = await artifact_provenance.issue_provenance(
        {"sql": "SELECT region, SUM(amount) FROM sales.orders GROUP BY region"}, USER
    )
    assert identity
    assert list(redis.ttls.values()) == [artifact_provenance.PROOF_TTL_SECONDS]
    assert all("sales.orders" not in key for key in redis.values)
    sql = await artifact_provenance.read_provenance(identity, USER)
    assert "FROM sales.orders" in sql


@pytest.mark.parametrize(
    "other",
    [
        {**USER, "session_id": "s2"},
        {**USER, "username": "someone"},
        {**USER, "active_role": "FINANCE", "roles": ["SALES", "FINANCE"]},
        {**USER, "security_context_version": 2},
    ],
)
async def test_another_context_cannot_redeem_a_proof(redis, other):
    identity = await artifact_provenance.issue_provenance({"sql": "SELECT 1"}, USER)
    with pytest.raises(HTTPException) as missing:
        await artifact_provenance.read_provenance(identity, other)
    assert missing.value.status_code == 410


async def test_expired_proof_asks_for_a_fresh_run(redis):
    with pytest.raises(HTTPException) as expired:
        await artifact_provenance.read_provenance("gone", USER)
    assert expired.value.status_code == 410


@pytest.mark.parametrize("result", [{}, {"sql": "  "}, {"sql": "DROP TABLE sales.orders"}])
async def test_results_without_a_savable_statement_get_no_proof(redis, result):
    assert await artifact_provenance.issue_provenance(result, USER) is None
    assert redis.values == {}


async def test_proofs_need_redis(monkeypatch):
    monkeypatch.setattr(artifact_provenance.session_store, "_redis", None)
    with pytest.raises(HTTPException) as unavailable:
        await artifact_provenance.read_provenance("any", USER)
    assert unavailable.value.status_code == 503
