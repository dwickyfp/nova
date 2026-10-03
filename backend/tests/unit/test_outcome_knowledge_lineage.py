import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import memory
from app.modules.intelligence import decisions, engine
from app.modules.intelligence.contracts import KnowledgeState, Outcome, OutcomeLearningRef
from app.modules.intelligence.decisions import DecisionOperation
from tests.unit.test_intelligence_decisions import program as _program
from tests.unit.test_intelligence_engine import USER
from tests.unit.test_intelligence_knowledge import journal as _journal

program = _program
journal = _journal


@pytest.fixture
async def learning(program, journal, monkeypatch):
    service, repo, source, _, body = program
    body.learning_enabled = True
    created = await decisions.create_decision(body, USER)
    selected = await decisions.operate_decision(
        created.id,
        DecisionOperation(
            operation_id="select-learning",
            expected_revision=1,
            operation="select",
            option_id="transfer",
        ),
        USER,
    )
    approved = await decisions.operate_decision(
        selected.id,
        DecisionOperation(
            operation_id="approve-learning",
            expected_revision=selected.revision,
            operation="approve",
        ),
        USER,
    )
    monkeypatch.setattr(memory, "memory_repository", journal)
    original = service.observe

    async def complete(*args, **kwargs):
        observed = await original(*args, **kwargs)
        return observed.model_copy(update={"completeness": 1})

    monkeypatch.setattr(service, "observe", complete)
    pending = await service.evaluate_outcome(approved.id, USER)
    assert pending.status == "pending"
    assert not journal.history
    monkeypatch.setattr(engine, "utc_now", lambda: approved.outcome_window.end)
    return service, repo, source, approved, journal


async def assert_pinned_pair(service, repo, decision, journal):
    outcome = await service.evaluate_outcome(decision.id, USER)
    assert outcome.status == "complete" and outcome.completeness == 1
    assert outcome.attribution == "observed_after"
    assert outcome.dimensions["attributed_business_impact"] is None
    knowledge = next(
        row for row in journal.history if row.revision == outcome.learning_refs[0].revision
    )
    assert knowledge.definition == {
        "outcome_id": outcome.id,
        "outcome_revision": outcome.revision,
    }
    assert knowledge.semantic == outcome.semantic
    assert knowledge.state == KnowledgeState.INFERRED and knowledge.visibility == "PRIVATE"
    assert knowledge.authority == "observed_outcome"
    assert journal.projection["memory_id"] == outcome.learning_refs[0].id
    reloaded = await service.get("outcomes", outcome.id, USER, revision=outcome.revision)
    assert reloaded == outcome
    snapshots = len(repo.history), len(journal.history)
    repeated = await service.evaluate_outcome(decision.id, USER)
    assert repeated == outcome
    assert (len(repo.history), len(journal.history)) == snapshots
    return outcome


async def test_outcome_knowledge_pins_final_persisted_revision_and_retry_is_stable(learning):
    service, repo, _, decision, journal = learning
    await assert_pinned_pair(service, repo, decision, journal)
    assert len(journal.history) == 1


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize(
    "boundary",
    [
        "before_outcome_link",
        "after_outcome_link",
        "before_knowledge",
        "after_knowledge",
        "projection",
        "after_projection",
    ],
)
async def test_learning_recovers_each_durable_write_boundary(
    learning, monkeypatch, boundary, cancelled
):
    service, repo, _, decision, journal = learning
    original_save = repo.save
    original_append = journal._append_revision
    original_projection = journal._write_projection
    failed = False

    def interrupt():
        nonlocal failed
        failed = True
        raise asyncio.CancelledError() if cancelled else RuntimeError("interrupted learning")

    async def save(kind, record, **kwargs):
        linking = kind == "outcomes" and bool(record.learning_refs)
        if linking and not failed and boundary == "before_outcome_link":
            interrupt()
        saved = await original_save(kind, record, **kwargs)
        if linking and not failed and boundary == "after_outcome_link":
            interrupt()
        return saved

    async def append(*args):
        if not failed and boundary == "before_knowledge":
            interrupt()
        await original_append(*args)
        if not failed and boundary == "after_knowledge":
            interrupt()

    async def project(**kwargs):
        if not failed and boundary == "projection":
            interrupt()
        await original_projection(**kwargs)
        if not failed and boundary == "after_projection":
            interrupt()

    monkeypatch.setattr(repo, "save", save)
    monkeypatch.setattr(journal, "_append_revision", append)
    monkeypatch.setattr(journal, "_write_projection", project)
    with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
        await service.evaluate_outcome(decision.id, USER)
    pending = next(row for (kind, _), row in repo.rows.items() if kind == "outcomes")
    if pending.learning_refs and boundary != "after_projection":
        with pytest.raises(HTTPException) as error:
            await service.get("outcomes", pending.id, USER)
        assert error.value.status_code == 404
    await assert_pinned_pair(service, repo, decision, journal)
    assert len(journal.history) == 1
    if pending.learning_refs:
        assert repo.rows[("outcomes", pending.id)].revision == pending.revision


@pytest.mark.parametrize("linked", [False, True])
async def test_existing_bug_and_pre_link_crash_recover_immutable_pair(
    learning, monkeypatch, linked
):
    service, repo, _, decision, journal = learning
    upsert = journal.upsert
    monkeypatch.setattr(journal, "upsert", AsyncMock(side_effect=RuntimeError("before learning")))
    with pytest.raises(RuntimeError, match="before learning"):
        await service.evaluate_outcome(decision.id, USER)
    monkeypatch.setattr(journal, "upsert", upsert)
    observed = next(row for (kind, _), row in repo.rows.items() if kind == "outcomes")
    assert not observed.learning_refs
    fact = (
        f"Prediction for {decision.target_metric} was {observed.predicted:g}; "
        f"observed {observed.actual:g}. Attribution: {observed.attribution}."
    )
    await journal.upsert(
        user_name=USER["username"],
        agent_id=decision.agent_id,
        role_name=USER["active_role"],
        fact_key="outcome_" + observed.id,
        fact=fact,
        source_quote=fact,
        source_thread_id=decision.thread_id or observed.id,
        source_message_id=f"{observed.id}:{observed.revision}",
        existing_id=None,
        outcome={
            "id": observed.id,
            "revision": observed.revision,
            "semantic": observed.semantic.model_dump(),
        },
        source_scope=observed.scope,
        observed_at=observed.updated_at,
    )
    old_knowledge = deepcopy(journal.history[0])
    mismatched = observed
    if linked:
        mismatched = await repo.save(
            "outcomes",
            observed.model_copy(
                update={
                    "learning_refs": [
                        OutcomeLearningRef(
                            id=old_knowledge.memory_id, revision=old_knowledge.revision
                        )
                    ]
                }
            ),
            expected_revision=observed.revision,
        )
        with pytest.raises(HTTPException) as error:
            await service.get("outcomes", mismatched.id, USER)
        assert error.value.status_code == 404
    repaired = await assert_pinned_pair(service, repo, decision, journal)
    assert repaired.revision > mismatched.revision
    assert journal.history[0] == old_knowledge
    assert len(journal.history) == 2


async def test_retry_after_knowledge_append_restores_memories_api_without_new_revisions(
    learning, monkeypatch
):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_current_user
    from app.modules.agents import router
    from app.modules.intelligence.semantic_views import semantic_view_service

    service, repo, source, decision, journal = learning
    append = journal._append_revision

    async def crash_after_append(*args):
        await append(*args)
        raise RuntimeError("after journal before projection")

    monkeypatch.setattr(journal, "_append_revision", crash_after_append)
    with pytest.raises(RuntimeError, match="before projection"):
        await service.evaluate_outcome(decision.id, USER)
    interrupted = next(row for (kind, _), row in repo.rows.items() if kind == "outcomes")
    assert len(journal.history) == 1 and journal.projection is None
    with pytest.raises(HTTPException) as missing:
        await journal.revisions(
            interrupted.learning_refs[0].id,
            user_name=USER["username"],
            agent_id=decision.agent_id,
            role_name=USER["active_role"],
        )
    assert missing.value.status_code == 404

    async def list_rows(sql, params):
        if sql.startswith("SELECT memory_id, user_name"):
            if journal.projection is None:
                return {"rows": []}
            row = journal.projection
            keys = (
                "memory_id",
                "user_name",
                "agent_id",
                "role_name",
                "fact_key",
                "fact",
                "source_quote",
                "source_thread_id",
            )
            return {
                "rows": [
                    [*[row[key] for key in keys], interrupted.created_at, interrupted.updated_at]
                ]
            }
        if sql.startswith("SELECT memory_id,payload,branches"):
            latest = journal.history[-1]
            return {"rows": [[latest.memory_id, latest.model_dump_json(), 1]]}
        return {"rows": []}

    monkeypatch.setattr(memory.db, "execute_system", AsyncMock(side_effect=list_rows))
    monkeypatch.setattr(router, "memory_repository", journal)
    monkeypatch.setattr(router, "_require_agent", AsyncMock())
    monkeypatch.setattr(engine, "intelligence_service", service)
    monkeypatch.setattr(semantic_view_service, "_readable_version", source._readable_version)
    app = FastAPI()
    app.include_router(router.router, prefix="/api/v1/agents")
    app.dependency_overrides[get_current_user] = lambda: USER
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        url = f"/api/v1/agents/{decision.agent_id}/memories"
        before = await client.get(url)
        assert before.status_code == 200 and before.json()["memories"] == []
        monkeypatch.setattr(journal, "_append_revision", append)
        recovered = await service.evaluate_outcome(decision.id, USER)
        assert recovered == interrupted and len(journal.history) == 1
        after = await client.get(url)
        assert after.status_code == 200
        memories = after.json()["memories"]
        assert len(memories) == 1
        assert memories[0]["knowledge"]["definition"] == {
            "outcome_id": recovered.id,
            "outcome_revision": recovered.revision,
        }
        assert memories[0]["knowledge"]["revision"] == recovered.learning_refs[0].revision


async def test_stale_outcome_cas_cannot_publish_knowledge(learning, monkeypatch):
    service, repo, _, decision, journal = learning
    original = repo.save

    async def raced(kind, record, **kwargs):
        if kind == "outcomes" and record.learning_refs:
            old = repo.rows[(kind, record.id)]
            await original(
                kind,
                old.model_copy(update={"actual": old.actual + 1}),
                expected_revision=old.revision,
            )
        return await original(kind, record, **kwargs)

    monkeypatch.setattr(repo, "save", raced)
    with pytest.raises(HTTPException) as error:
        await service.evaluate_outcome(decision.id, USER)
    assert error.value.status_code == 409
    assert not journal.history and journal.projection is None


async def test_revoked_source_cannot_repair_partial_learning(learning, monkeypatch):
    service, _, source, decision, journal = learning
    monkeypatch.setattr(journal, "_append_revision", AsyncMock(side_effect=RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        await service.evaluate_outcome(decision.id, USER)
    source.revoked = True
    with pytest.raises(HTTPException) as error:
        await service.evaluate_outcome(decision.id, USER)
    assert error.value.status_code == 404
    assert not journal.history and journal.projection is None


async def test_incomplete_outcome_cannot_expose_learning(learning):
    service, repo, _, decision, journal = learning
    complete = await assert_pinned_pair(service, repo, decision, journal)
    incomplete = Outcome.model_validate(complete.model_dump())
    incomplete.actual = None
    incomplete.status = "missing_data"
    incomplete.completeness = 0
    with pytest.raises(HTTPException) as error:
        await service.authorize_record(incomplete, USER, engine.CycleBudget())
    assert error.value.status_code == 409
