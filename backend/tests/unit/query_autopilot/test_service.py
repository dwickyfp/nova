from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query_autopilot.models import ActionKind, Candidate, Evidence, State, utcnow
from app.modules.query_autopilot.service import AutopilotService, Conflict, public_record
from tests.unit.query_autopilot.test_policy_experiments import fixtures


class MemoryRepository:
    def __init__(self):
        self.records = {}
        self.writes = []

    async def get(self, kind, identifier):
        return deepcopy(self.records.get((kind, identifier)))

    async def put(self, kind, identifier, payload, **metadata):
        existing = self.records.get((kind, identifier))
        if metadata.get("insert_only") and existing:
            return False
        expected = metadata.get("expected_version")
        if expected is not None and (not existing or existing["version"] != expected):
            return False
        self.records[kind, identifier] = deepcopy(payload)
        self.writes.append((kind, identifier, deepcopy(payload)))
        return True

    async def page(self, kind, *, after="", limit=100, **filters):
        rows = [
            v for (table, key), v in sorted(self.records.items()) if table == kind and key > after
        ]
        for key, value in filters.items():
            if value is not None and key in {"family_id", "cohort_id", "state"}:
                rows = [r for r in rows if r.get(key) == value]
        return deepcopy(rows[:limit])

    async def cleanup_before(self, kind, when):
        self.writes.append(("cleanup", kind, when))


class Payloads:
    def __init__(self):
        self.values = {}
        self.deleted = []

    def reference(self, identifier):
        return identifier

    async def put(self, identifier, text):
        self.values[identifier] = text
        return identifier

    async def get(self, reference, **_):
        return self.values[reference]

    async def delete(self, reference):
        self.deleted.append(reference)
        self.values.pop(reference, None)


class SQL:
    def __init__(self, repo):
        self.repo = repo
        self.calls = []
        self.optimized = False
        self.fail_maintenance = False

    async def capabilities(self):
        from app.sql_frontend.capabilities.starrocks import EngineCapabilities

        return EngineCapabilities(query_profiles=True, be_logs=True)

    @asynccontextmanager
    async def connection(self, scope, **_):
        self.calls.append(("connection", scope.principal, scope.active_role))
        yield object()

    async def execute(self, statement, scope, **options):
        self.calls.append((statement, scope.database, options["category"]))
        if statement.startswith("ANALYZE"):
            # Intent must already be durable before either sandbox or production write.
            kind = "experiments" if options["category"] == "experiment" else "actions"
            assert any(
                t == kind and v.get("state") == "APPLYING"
                for (t, _), v in self.repo.records.items()
            )
            self.optimized = True
            if self.fail_maintenance and options["category"] == "maintenance":
                raise RuntimeError("uncertain network outcome")
        if statement.startswith("SELECT get_query_profile"):
            return QueryResult(
                columns=["profile"],
                rows=[["- QueryCumulativeCpuTime: 1ms\n- QueryExecutionWallTime: 2ms"]],
            )
        if statement.startswith("SHOW PARTITIONS"):
            return QueryResult(columns=["PartitionId", "VisibleVersion"], rows=[[1, 7]])
        if statement.startswith("EXPLAIN"):
            return QueryResult(columns=["plan"], rows=[["1:OlapScanNode"], ["cardinality: 10"]])
        latency = 100 if "COUNT" in statement or not self.optimized else 40
        return QueryResult(
            rows=[[1]],
            row_count=1,
            column_types=("INT",),
            engine_roundtrip_ms=latency,
            engine_query_ids=[str(uuid4())],
        )


@asynccontextmanager
async def unlocked(*_):
    yield


@pytest.fixture
async def setup(monkeypatch):
    from app.modules.query_autopilot import service as module

    monkeypatch.setattr(module, "exclusive", unlocked)
    monkeypatch.setattr(module.session_store, "get", AsyncMock(return_value=None))
    candidate, enrollment, policy = fixtures()
    candidate = candidate.model_copy(
        update={"state": State.PROPOSED, "experiment_id": None, "experiment_digest": None}
    )
    enrollment = enrollment.model_copy(update={"control_families": ("control",)})
    repo, payloads = MemoryRepository(), Payloads()
    sql = SQL(repo)
    service = AutopilotService(repo, sql=sql, payloads=payloads, client=object())
    service.audit = AsyncMock()
    await repo.put("enrollments", enrollment.id, enrollment.model_dump(mode="json"))
    await repo.put("policies", "default", policy.model_dump(mode="json"))
    for family, identifier, statement in (
        ("f", "sample", "SELECT id FROM orders"),
        ("control", "control-sample", "SELECT COUNT(*) FROM orders"),
    ):
        await payloads.put(identifier, statement)
        item = Evidence(
            id=identifier,
            family_id=family,
            cohort_id=candidate.scope.cohort_id,
            kind="replay_sample",
            availability="available",
            source="opt_in_workload",
            expires_at=utcnow() + timedelta(hours=24),
            payload_ref=identifier,
        )
        await repo.put("evidence", identifier, item.model_dump(mode="json"))
    evidence = Evidence(
        id="ev",
        family_id="f",
        cohort_id=candidate.scope.cohort_id,
        kind="statistics",
        availability="available",
        source="starrocks",
        expires_at=utcnow() + timedelta(hours=24),
    )
    await repo.put("evidence", "ev", evidence.model_dump(mode="json"))
    user = {
        "username": "admin",
        "active_role": "ACCOUNTADMIN",
        "security_context_version": 1,
        "session_id": "session",
    }
    candidate = await service.save_candidate(candidate, user)
    return service, repo, candidate, enrollment, policy, user


async def test_experiment_binding_drives_auto_apply_without_admin_substitution(setup):
    service, repo, candidate, enrollment, policy, user = setup
    job = await service.mutate(
        "c", "experiment", version=1, idempotency_key="experiment-1", user=user
    )
    assert await service.run_operation(await repo.get("jobs", job["id"])) == {
        "state": "COMPLETED",
        "reason": None,
    }
    ready = Candidate.model_validate(await repo.get("opportunities", "c"))
    trial = await repo.get("experiments", ready.experiment_id)
    assert ready.version == candidate.version + 1
    assert ready.state == State.READY_AUTO
    assert ready.experiment_digest == trial["result_digest"]
    assert trial["candidate_binding"] == ready.proposal_binding
    auto = await repo.get("jobs", "auto:" + job["id"])
    result = await service.run_operation(auto)
    assert result["state"] == "COMPLETED"
    assert (await repo.get("opportunities", "c"))["state"] == "VERIFYING"
    production_connections = [c for c in service.sql.calls if c[0] == "connection"]
    assert ("connection", "admin", "ACCOUNTADMIN") not in production_connections
    assert any(c == ("connection", "analyst", "finance") for c in production_connections)
    action_count = sum(c[2] == "maintenance" for c in service.sql.calls)
    stale = await service.run_operation(auto)
    assert stale["state"] == "COMPLETED"
    assert (await repo.get("opportunities", "c"))["state"] == "VERIFYING"
    assert sum(c[2] == "maintenance" for c in service.sql.calls) == action_count


async def test_idempotency_conflict_and_evidence_mutation_invalidate_approval(setup):
    service, repo, candidate, enrollment, policy, user = setup
    first = await service.mutate(
        "c", "experiment", version=1, idempotency_key="same-key", user=user
    )
    assert first == await service.mutate(
        "c", "experiment", version=1, idempotency_key="same-key", user=user
    )
    with pytest.raises(Conflict, match="idempotency_key_reused"):
        await service.mutate("c", "experiment", version=2, idempotency_key="same-key", user=user)
    assert await service.evidence_current(candidate)
    evidence = await repo.get("evidence", "ev")
    evidence["summary"] = {"new": 1}
    await repo.put("evidence", "ev", evidence)
    assert not await service.evidence_current(candidate)
    assert (await service.run_operation(await repo.get("jobs", first["id"])))["state"] == "BLOCKED"
    assert not any(call[0].startswith("ANALYZE") for call in service.sql.calls)


async def test_retention_removes_payloads_but_keeps_decisions_and_hides_handles(setup):
    service, repo, candidate, _, _, _ = setup
    record = await repo.get("evidence", "sample")
    record["expires_at"] = (utcnow() - timedelta(seconds=1)).isoformat()
    await repo.put("evidence", "sample", record)
    assert public_record(record)["availability"] == "expired"
    await service.cleanup()
    assert "sample" in service.payloads.deleted
    assert (await repo.get("evidence", "sample"))["payload_ref"] is None
    assert await repo.get("opportunities", "c")
    assert {v[1] for v in repo.writes if v[0] == "cleanup"} == {
        "observations",
        "rollups",
        "baselines",
    }
    assert public_record({"id": "x", "actor_session_id": "secret", "payload_ref": "private"}) == {
        "id": "x"
    }
    assert public_record(
        {"nested": [{"api_key": "private", "count": 1}, {"payload_ref": "private"}]}
    ) == {"nested": [{"count": 1}, {}]}


@pytest.mark.parametrize("exists", [True, False])
async def test_compensation_reconciles_unknown_intent_without_resubmission(
    setup, monkeypatch, exists
):
    from app.modules.query_autopilot.engine_state import ObjectState

    service, repo, candidate, _, _, user = setup
    candidate = candidate.model_copy(
        update={
            "kind": ActionKind.MATERIALIZED_VIEW,
            "state": State.REGRESSED,
            "owned_object": "nova_ap_c",
        }
    )
    action = {"id": "original", "owned_object_binding": "original-object"}
    await repo.put(
        "actions",
        "rollback:original",
        {
            "id": "rollback:original",
            "candidate_id": candidate.id,
            "state": "APPLYING",
            "owned_object_binding": "original-object",
        },
    )
    monkeypatch.setattr(
        "app.modules.query_autopilot.service.inspect_object",
        AsyncMock(return_value=ObjectState(exists, "id", "definition", True)),
    )
    job = {
        "actor": user["username"],
        "actor_role": user["active_role"],
        "actor_version": 1,
        "actor_session_id": "session",
    }
    result = await service.compensate(candidate, action, job)
    assert result == (
        "compensation_outcome_uncertain_no_retry" if exists else "verified_object_removed"
    )
    assert not any(call[0].startswith("DROP") for call in service.sql.calls)
    if not exists:
        assert (await repo.get("opportunities", candidate.id))["state"] == "ROLLED_BACK"


@pytest.mark.parametrize("state", [State.VERIFYING, State.SUCCESS])
async def test_verification_recovers_durable_outcome_before_or_after_state_write(setup, state):
    service, repo, candidate, enrollment, policy, _ = setup
    candidate = candidate.model_copy(update={"state": state})
    await repo.put("opportunities", candidate.id, candidate.model_dump(mode="json"))
    job = {"id": "verify:applied"}
    await repo.put(
        "outcomes",
        job["id"],
        {
            "id": job["id"],
            "candidate_id": candidate.id,
            "candidate_binding": candidate.binding,
            "state": "SUCCESS",
            "reason": None,
            "rollback": "not_needed",
        },
    )
    assert await service.verify(candidate, enrollment, policy, job) == {
        "state": "COMPLETED",
        "reason": None,
    }
    assert (await repo.get("opportunities", candidate.id))["state"] == "SUCCESS"
    assert service.sql.calls == []


async def test_discovery_resumes_families_and_enrollments_beyond_bounded_pages(setup):
    service, repo, _, enrollment, _, _ = setup
    for index in range(1005):
        identifier = f"family-{index:04}"
        await repo.put(
            "families",
            identifier,
            {
                "id": identifier,
                "family_id": identifier,
                "cohort_id": enrollment.scope.cohort_id,
            },
        )
    seen = []
    for _ in range(12):
        batch = await service.discovery_batch("propose", limit=100)
        seen.extend(family["id"] for _, family in batch)
        if len(seen) >= 1005:
            break
    assert len(seen) == len(set(seen)) == 1005
    assert "family-1004" in seen
    # Disabled enrollments also advance the cursor instead of consuming every tick forever.
    repo.records = {key: value for key, value in repo.records.items() if key[0] != "enrollments"}
    for index in range(25):
        disabled = enrollment.model_copy(update={"id": f"disabled-{index:02}", "enabled": False})
        await repo.put("enrollments", disabled.id, disabled.model_dump(mode="json"))
    active = enrollment.model_copy(update={"id": "last-active"})
    await repo.put("enrollments", active.id, active.model_dump(mode="json"))
    assert await service.discovery_batch("enrich", limit=4) == []
    batch = await service.discovery_batch("enrich", limit=4)
    assert len(batch) == 4 and all(e.id == active.id for e, _ in batch)
