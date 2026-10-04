"""Mission provenance pins actual releases and preserves narrow historical access."""

from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import mission as mission_module
from app.modules.agents import releases, resources, router
from app.modules.agents.mission import MissionService
from app.modules.agents.mission_schema import (
    MissionDeliverable,
    MissionResume,
    ObjectRef,
    object_binding_key,
)
from app.modules.intelligence import context_graph, context_sources
from app.modules.intelligence.context_sources import ContextSourceRef
from app.modules.intelligence.contracts import (
    EvidenceRef,
    Investigation,
    KnowledgeState,
    Scope,
    SemanticRef,
    fingerprint,
    utc_now,
)
from app.modules.intelligence.engine import CycleBudget, intelligence_service, table_digest
from tests.unit.test_intelligence_engine import MemoryRepository
from tests.unit.test_studio_missions import NEW_SESSION, USER, MissionIO, add_run, create

SEMANTIC = SemanticRef(view_id="sales", version=1, fingerprint="frozen-definition")


class PinnedRepository(MemoryRepository):
    def __init__(self):
        super().__init__()
        self.pins = {}

    async def get(self, kind, record_id, scope, model, **kwargs):
        if kind == "investigations":
            return deepcopy(
                self.pins.get((record_id, kwargs.get("revision"), scope.model_dump_json()))
            )
        return await super().get(kind, record_id, scope, model, **kwargs)


@pytest.fixture
def owners(monkeypatch):
    from app.modules.agents.semantic.runtime import semantic_ir_to_definition
    from app.modules.intelligence.actions import action_service
    from tests.unit.test_semantic_intelligence import sales_model

    io, service, repository = MissionIO(), MissionService(), PinnedRepository()
    monkeypatch.setattr(mission_module.settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)

    async def execute(sql, params=None):
        if "CONFIG_INTELLIGENCE_INVESTIGATIONS" in sql:
            matches = [
                value.model_dump_json()
                for (ident, revision, _), value in repository.pins.items()
                if [
                    ident,
                    value.scope.principal,
                    value.scope.active_role,
                    value.scope.security_context_version,
                    revision,
                ]
                == params
            ]
            return {"rows": [[value] for value in matches]}
        return await io.execute(sql, params)

    monkeypatch.setattr(mission_module.db, "execute_system", execute)
    monkeypatch.setattr(
        mission_module, "require_thread", AsyncMock(return_value={"agent_id": "finance"})
    )
    monkeypatch.setattr(mission_module, "mission_service", service)
    monkeypatch.setattr(mission_module, "write_audit_log", AsyncMock())
    monkeypatch.setattr(action_service, "revalidate", AsyncMock())

    @asynccontextmanager
    async def lock(*args):
        yield AsyncMock()

    monkeypatch.setattr(mission_module.harness_repository, "admission_lock", lock)
    monkeypatch.setattr(intelligence_service, "repository", repository)
    authorize = AsyncMock(
        return_value={
            "status": "DEPRECATED",
            "definition": semantic_ir_to_definition(sales_model()),
        }
    )
    monkeypatch.setattr(intelligence_service, "authorize_semantic", authorize)
    monkeypatch.setattr(intelligence_service, "_audit", AsyncMock())
    agent = {"agent_id": "finance", "owner_name": "alice", "release_manifest_id": "latest-release"}
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(mission_module.agent_repository, "get_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(releases, "load_runtime_manifest", AsyncMock(return_value=None))
    monkeypatch.setattr(resources, "validate_resources", AsyncMock())
    return io, service, repository, authorize


def release(identifier="executed-release", version="evaluated-version", agent="finance"):
    dependencies = {"semantic_views": [SEMANTIC.model_dump()], "configuration": {}}
    return {
        "id": identifier,
        "agent_id": agent,
        "version_id": version,
        "fingerprint": fingerprint(dependencies),
        "dependencies": dependencies,
    }


async def attach(io, service, mission, *, run_id="direct", user=USER, agent="finance"):
    add_run(io, mission, run_id, agent=agent, status="completed")
    return await service.attach_run(mission.mission_id, run_id, user)


async def test_actual_bound_release_is_idempotent_and_remains_exact_after_resume(
    owners, monkeypatch
):
    io, service, repository, authorize = owners
    manifest = release()
    stored = AsyncMock(return_value=manifest)
    monkeypatch.setattr(releases, "get_manifest", stored)
    mission = await attach(io, service, await create(service))
    pinned = await service.record_release(mission.mission_id, "direct", manifest, USER)
    assert pinned.release_pins[0].manifest_id == "executed-release"
    assert pinned.release_pins[0].version_id == "evaluated-version"
    assert pinned.release_pins[0].run_id == "direct"
    assert pinned == await service.record_release(mission.mission_id, "direct", manifest, USER)
    assert mission_module.write_audit_log.await_args.kwargs["action"] == "PIN_RELEASE"
    assert "dependencies" not in pinned.release_pins[0].model_dump()
    stored.assert_awaited_with("finance", "alice", manifest_id="executed-release")

    resumed = await service.resume(
        pinned.mission_id,
        MissionResume(expected_revision=pinned.revision, operation_id="resume"),
        NEW_SESSION,
    )
    result = await context_graph.project_canonical_context(
        ContextSourceRef(kind="mission", id=pinned.mission_id, revision=resumed.revision),
        NEW_SESSION,
    )
    nodes = [node for (kind, _), node in repository.rows.items() if kind == "nodes"]
    release_node = next(node for node in nodes if node.kind == "agent_release")
    assert release_node.reference_id == "executed-release"
    assert (
        release_node.reference_agent_id == "finance" and release_node.reference_run_id == "direct"
    )
    assert release_node.reference_parent_id == pinned.mission_id
    assert release_node.state == KnowledgeState.INFERRED
    assert any(
        edge.relationship == "executed_with" and edge.source == result["root_id"]
        for (kind, _), edge in repository.rows.items()
        if kind == "edges"
    )
    assert all(call.kwargs["manifest_id"] == "executed-release" for call in stored.await_args_list)
    assert all(call.args[1]["username"] == "alice" for call in authorize.await_args_list)
    assert resumed.run_bindings["direct"] == Scope.from_user(USER)
    with pytest.raises(HTTPException):
        await service.record_release(pinned.mission_id, "direct", manifest, NEW_SESSION)


@pytest.mark.parametrize(
    "change, code",
    [
        ({"agent_id": "different-agent"}, 403),
        ({"dependencies": {}}, 409),
    ],
)
async def test_release_capture_refuses_forged_bound_identity_and_audits(
    owners, monkeypatch, change, code
):
    io, service, _, _ = owners
    manifest = release()
    monkeypatch.setattr(releases, "get_manifest", AsyncMock(return_value=manifest))
    mission = await attach(io, service, await create(service))
    with pytest.raises(HTTPException) as error:
        await service.record_release(mission.mission_id, "direct", {**manifest, **change}, USER)
    assert error.value.status_code == code
    assert io.missions[mission.mission_id].release_pins == []
    assert mission_module.write_audit_log.await_args.kwargs["status"] == "REFUSED"


async def test_one_run_cannot_adopt_a_different_release_and_revocation_blocks_resume(
    owners, monkeypatch
):
    io, service, _, authorize = owners
    manifest = release()
    monkeypatch.setattr(releases, "get_manifest", AsyncMock(return_value=manifest))
    mission = await attach(io, service, await create(service))
    mission = await service.record_release(mission.mission_id, "direct", manifest, USER)
    with pytest.raises(HTTPException) as collision:
        await service.record_release(
            mission.mission_id, "direct", release("different-release"), USER
        )
    assert collision.value.status_code == 409
    authorize.side_effect = HTTPException(404, "Revoked historical semantic version")
    with pytest.raises(HTTPException) as revoked:
        await service.resume(
            mission.mission_id,
            MissionResume(expected_revision=mission.revision, operation_id="resume"),
            NEW_SESSION,
        )
    assert revoked.value.status_code == 404
    assert io.missions[mission.mission_id].scope == Scope.from_user(USER)


async def test_recorded_shared_release_uses_agent_owner_metadata_and_current_caller_access(
    owners,
    monkeypatch,
):
    io, service, _, _ = owners
    manifest = release()
    monkeypatch.setattr(
        router, "_require_agent", AsyncMock(return_value={"owner_name": "publisher"})
    )
    stored = AsyncMock(return_value=manifest)
    monkeypatch.setattr(releases, "get_manifest", stored)
    mission = await attach(io, service, await create(service))
    mission = await service.record_release(mission.mission_id, "direct", manifest, USER)
    pin = mission.release_pins[0]
    ref = ContextSourceRef(
        kind="agent_release",
        id=pin.manifest_id,
        agent_id=pin.agent_id,
        parent_id=mission.mission_id,
        run_id=pin.run_id,
        fingerprint=pin.fingerprint,
    )
    assert (await context_sources.read_context_source(ref, USER, CycleBudget()))[
        "value"
    ] == manifest
    stored.assert_awaited_with("finance", "publisher", manifest_id=pin.manifest_id)
    router._require_agent.assert_awaited_with("finance", USER)
    with pytest.raises(HTTPException):
        await context_sources.read_context_source(
            ref.model_copy(update={"run_id": "unrelated"}), USER, CycleBudget()
        )
    with pytest.raises(HTTPException):
        await context_sources.read_context_source(
            ref.model_copy(update={"parent_id": None}), USER, CycleBudget()
        )


async def test_historical_context_reads_only_original_binding_and_exact_mission_revision(
    owners,
    monkeypatch,
):
    io, service, repository, authorize = owners
    old_scope = Scope.from_user(USER)
    source = Investigation(
        id="inv",
        scope=old_scope,
        revision=1,
        semantic=SEMANTIC,
        news_id="news",
        status="complete",
        residual=0,
        method="validated-test",
    )
    repository.pins[(source.id, 1, old_scope.model_dump_json())] = source
    repository.pins[(source.id, 2, old_scope.model_dump_json())] = source.model_copy(
        update={"revision": 2}
    )
    repository.pins[("unrelated", 1, old_scope.model_dump_json())] = source.model_copy(
        update={"id": "unrelated"}
    )
    mission = await create(service)
    first = ObjectRef(kind="investigation", id=source.id, revision=1)
    mission = await service.link(mission.mission_id, first, mission.revision, USER)
    latest = first.model_copy(update={"revision": 2})
    mission = await service.link(mission.mission_id, latest, mission.revision, USER)
    resumed = await service.resume(
        mission.mission_id,
        MissionResume(expected_revision=mission.revision, operation_id="resume"),
        NEW_SESSION,
    )
    ref = ContextSourceRef(
        kind="investigation", id=source.id, revision=1, parent_id=resumed.mission_id
    )
    result = await context_sources.read_context_source(ref, NEW_SESSION, CycleBudget())
    assert result["value"]["revision"] == 1 and result["value"]["scope"] == old_scope.model_dump(
        mode="json"
    )
    assert resumed.object_bindings[object_binding_key(first)] == old_scope
    with pytest.raises(HTTPException):
        await intelligence_service.get("investigations", source.id, NEW_SESSION, revision=1)
    for change in ({"id": "unrelated"}, {"revision": 3}):
        with pytest.raises(HTTPException):
            await context_sources.read_context_source(
                ref.model_copy(update=change), NEW_SESSION, CycleBudget()
            )
    wrong_binding = old_scope.model_copy(update={"session_id": "another-original-session"})
    io.missions[mission.mission_id].object_bindings[object_binding_key(first)] = wrong_binding
    with pytest.raises(HTTPException):
        await context_sources.read_context_source(ref, NEW_SESSION, CycleBudget())
    io.missions[mission.mission_id].object_bindings[object_binding_key(first)] = old_scope
    authorize.side_effect = HTTPException(404, "Source access revoked")
    with pytest.raises(HTTPException):
        await context_sources.read_context_source(ref, NEW_SESSION, CycleBudget())


@pytest.mark.parametrize("source_kind", ["investigation", "deliverable"])
async def test_mission_context_shares_query_budget_and_reuses_evidence(
    owners, monkeypatch, source_kind
):
    io, service, repository, _ = owners
    mission = await create(service)
    scope = Scope.from_user(USER)
    result = {
        "columns": ["total_revenue"],
        "rows": [[12]],
        "model_fingerprint": SEMANTIC.fingerprint,
    }
    execute = AsyncMock(return_value=result)
    monkeypatch.setattr(intelligence_service.semantic, "execute_plan", execute)
    evidence = EvidenceRef(
        id="shared-query",
        source_type="query",
        source_id="query",
        scope=scope,
        semantic=SEMANTIC,
        semantic_plan={"metrics": ["total_revenue"]},
        method="semantic-compiler-v1",
        digest=table_digest(result),
        observed_at=utc_now(),
    )

    def pin(identifier, proof):
        source = Investigation(
            id=identifier, scope=scope, revision=1, semantic=SEMANTIC,
            news_id="news", status="complete", residual=0,
            method="validated-test", evidence=[proof],
        )
        repository.pins[(identifier, 1, scope.model_dump_json())] = source
        ref = ObjectRef(kind="investigation", id=identifier, revision=1)
        current = io.missions[mission.mission_id]
        io.missions[mission.mission_id] = current.model_copy(update={
            "object_refs": [*current.object_refs, ref],
            "object_bindings": {**current.object_bindings, object_binding_key(ref): scope},
        })
        return ref

    refs = [pin(identifier, evidence) for identifier in ("first", "second")]
    if source_kind == "deliverable":
        for ref in refs:
            document = MissionDeliverable(
                deliverable_id=ref.id, mission_id=mission.mission_id,
                mission_revision=mission.revision, kind="investigation_report",
                title="Pinned report", markdown="Recorded source", evidence_refs=[evidence.id],
                object_refs=[ref], created_at=utc_now(),
            )
            io.deliverables[ref.id] = (
                document.model_dump_json(), "request", mission_module.scope_params(scope),
            )
    budget = CycleBudget(queries=19)
    for ref in refs:
        await context_sources.read_context_source(
            ContextSourceRef(
                kind=source_kind, id=ref.id, revision=1, parent_id=mission.mission_id,
            ), USER, budget,
        )
    assert budget.queries == 20
    execute.assert_awaited_once()

    different = pin("different", evidence.model_copy(update={
        "id": "different-query", "semantic_plan": {"metrics": ["order_count"]},
    }))
    with pytest.raises(HTTPException) as refused:
        await context_sources.read_context_source(
            ContextSourceRef(
                kind="investigation", id=different.id, revision=1, parent_id=mission.mission_id,
            ), USER, budget,
        )
    assert refused.value.status_code == 429
    execute.assert_awaited_once()
