"""Learning observes bounded shapes, respects opt-out, and reauthorizes reviewed sources."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents.semantic import learning_sources as module
from app.modules.agents.semantic.runtime import semantic_ir_to_definition
from app.modules.intelligence import autopilot
from app.modules.intelligence.contracts import Scope, SemanticRef, fingerprint
from tests.unit.test_semantic_intelligence import sales_model
from tests.unit.test_usage_autopilot_control import setup as proposal_setup

USER = {
    "username": "alice",
    "active_role": "ANALYST",
    "assigned_roles": ["ANALYST"],
    "security_context_version": 3,
    "session_id": "current",
}
REF = SemanticRef(view_id="sales", version=2, fingerprint="published-definition")
DEFINITION = semantic_ir_to_definition(sales_model())


def request(sources=None):
    return module.LearningRequest(
        agent_id="agent",
        thread_id="thread",
        semantic=REF,
        sources=sources or [module.LearningSourceRef(kind="semantic_usage", id="sales")],
    )


def setup(monkeypatch, *, enabled=True):
    from app.modules.agents import mission, router
    from app.modules.assistant.repository import assistant_repository

    monkeypatch.setattr(
        router,
        "_require_agent",
        AsyncMock(
            return_value={
                "semantic_view_ids": ["sales"],
            }
        ),
    )
    monkeypatch.setattr(mission, "require_thread", AsyncMock(return_value={"agent_id": "agent"}))
    learning = AsyncMock(return_value=enabled)
    monkeypatch.setattr(assistant_repository, "learning_enabled", learning)
    authorize = AsyncMock(return_value={"status": "ACTIVE", "definition": DEFINITION})
    monkeypatch.setattr(module.intelligence_service, "authorize_semantic", authorize)
    usage = AsyncMock(
        return_value={
            "digest": "d" * 64,
            "patterns": [
                {
                    "pattern_id": "pattern",
                    "metrics": ["total_revenue"],
                    "dimensions": ["city"],
                    "filter_shape": [{"field": "city", "operator": "="}],
                    "time_grain": "month",
                    "observations": 1000,
                }
            ],
        }
    )
    monkeypatch.setattr(module, "load_scoped_usage", usage)
    return learning, authorize, usage


async def test_usage_keeps_inferred_state_and_stable_bounded_observations(monkeypatch):
    _, authorize, usage = setup(monkeypatch)
    first = await module.collect_observations(request(), USER)
    retry = await module.collect_observations(request(), USER)
    assert first == retry
    observed = first["observations"][0]
    assert observed["state"] == "INFERRED" and observed["authority"] == "usage_observation"
    assert observed["observations"] == 1000 and observed["freshness"] == "unknown"
    assert first["review_required"] and first["bounded"]
    assert "session_id" not in str(first)
    assert authorize.await_args.kwargs["active"] is True
    assert usage.await_args.args[0] == Scope.from_user(USER)


async def test_opt_out_stops_all_source_reads(monkeypatch):
    _, authorize, usage = setup(monkeypatch, enabled=False)
    result = await module.collect_observations(request(), USER)
    assert result["reason"] == "learning_disabled" and result["observations"] == []
    authorize.assert_not_awaited()
    usage.assert_not_awaited()


async def test_learning_reauthorizes_semantic_access_under_current_principal(monkeypatch):
    _, authorize, usage = setup(monkeypatch)
    authorize.side_effect = HTTPException(404, "Revoked")
    with pytest.raises(HTTPException) as error:
        await module.collect_observations(request(), USER)
    assert error.value.status_code == 404
    usage.assert_not_awaited()


def test_shapes_drop_literals_sql_bodies_and_unrecognized_concepts():
    context = module.LearningContext(request(), USER, DEFINITION)
    shape = module.safe_shape(
        {
            "metrics": ["total_revenue"],
            "dimensions": ["city"],
            "filters": [{"field": "city", "operator": "=", "value": "PRIVATE-CUSTOMER"}],
            "sql": "SELECT credentials",
            "body": "PRIVATE-DOCUMENT-BODY",
            "time": {"grain": "month"},
        },
        context,
    )
    assert shape["filter_shape"] == [{"field": "city", "operator": "="}]
    for value in ("PRIVATE-CUSTOMER", "credentials", "PRIVATE-DOCUMENT-BODY", "sql"):
        assert value not in str(shape)
    assert module.safe_shape({"metrics": ["private_unknown_metric"]}, context) is None


async def test_published_verified_query_source_never_exposes_sql_or_question(monkeypatch):
    setup(monkeypatch)
    definition = {
        **DEFINITION,
        "verified_queries": [
            {
                "verified_query_id": "verified",
                "question": "PRIVATE-QUESTION",
                "verified_sql": "SELECT PRIVATE-LITERAL",
                "semantic_plan": {"metrics": ["total_revenue"], "dimensions": [], "filters": []},
            }
        ],
    }
    module.intelligence_service.authorize_semantic.return_value["definition"] = definition
    result = await module.collect_observations(
        request(
            [
                module.LearningSourceRef(kind="verified_query", id="verified"),
            ]
        ),
        USER,
    )
    assert result["observations"][0]["authority"] == "published_verified_query"
    assert result["observations"][0]["state"] == "INFERRED"
    assert "PRIVATE" not in str(result) and "verified_sql" not in str(result)


async def test_unpublished_verified_query_cannot_support_learning(monkeypatch):
    setup(monkeypatch)
    with pytest.raises(HTTPException) as error:
        await module.collect_observations(
            request(
                [
                    module.LearningSourceRef(kind="verified_query", id="unpublished"),
                ]
            ),
            USER,
        )
    assert error.value.status_code == 404


async def test_decision_opt_out_and_exact_revision_are_respected(monkeypatch):
    setup(monkeypatch)
    record = {
        "semantic": REF.model_dump(),
        "learning_enabled": False,
        "thread_id": "thread",
        "target_metric": "total_revenue",
        "evidence": [],
    }
    get = AsyncMock(return_value=SimpleNamespace(model_dump=lambda **kwargs: dict(record)))
    monkeypatch.setattr(module.intelligence_service, "get", get)
    source = module.LearningSourceRef(kind="decision", id="decision", revision=2)
    result = await module.collect_observations(request([source]), USER)
    assert result["observations"] == [] and get.await_args.kwargs["revision"] == 2
    record["learning_enabled"] = True
    result = await module.collect_observations(request([source]), USER)
    assert result["observations"][0]["authority"] == "lifecycle_evidence"
    assert result["observations"][0]["attribution"] == "unknown"


async def test_outcome_keeps_recorded_causal_label_and_does_not_upgrade_association(monkeypatch):
    setup(monkeypatch)
    decision = {
        "semantic": REF.model_dump(),
        "learning_enabled": True,
        "thread_id": "thread",
        "target_metric": "total_revenue",
    }
    outcome = {
        "semantic": REF.model_dump(),
        "status": "complete",
        "decision_id": "decision",
        "decision_revision": 2,
        "attribution": "association",
        "evidence": [],
    }

    async def get(kind, *args, **kwargs):
        record = outcome if kind == "outcomes" else decision
        return SimpleNamespace(model_dump=lambda **kwargs: dict(record))

    monkeypatch.setattr(module.intelligence_service, "get", AsyncMock(side_effect=get))
    result = await module.collect_observations(
        request(
            [
                module.LearningSourceRef(kind="outcome", id="outcome", revision=3),
            ]
        ),
        USER,
    )
    assert result["observations"][0]["attribution"] == "association"
    assert result["observations"][0]["state"] == "INFERRED"


async def test_learning_proposal_retries_recover_same_reviewable_candidate(monkeypatch):
    body, _, published, _ = proposal_setup(monkeypatch)
    learning = request()
    learning.semantic = body.base
    evidence = {
        "digest": "c" * 64,
        "observations": [{"state": "INFERRED"}],
        "scope": Scope.from_user(USER).model_dump(exclude={"session_id"}),
    }
    collect = AsyncMock(return_value=evidence)
    monkeypatch.setattr(autopilot, "collect_observations", collect)
    body = body.model_copy(
        update={"usage_digest": None, "learning": learning, "learning_digest": "c" * 64}
    )
    first = await autopilot.propose("sales", body, USER)
    retry = await autopilot.propose(
        "sales", body.model_copy(update={"operation_id": "retry-1"}), USER
    )
    assert first["proposal_id"] == retry["proposal_id"]
    assert first["details"]["learning_evidence"] == evidence
    autopilot.rule_proposal_repository.create.assert_awaited_once()
    published.assert_not_awaited()
    collect.return_value = {**evidence, "digest": "b" * 64}
    with pytest.raises(HTTPException) as error:
        await autopilot.propose("sales", body, USER)
    assert error.value.status_code == 409


async def test_absent_learning_fields_preserve_existing_proposal_request_digest(monkeypatch):
    body, _, _, _ = proposal_setup(monkeypatch)
    expected = fingerprint(
        body.model_dump(
            mode="json",
            exclude={
                "operation_id",
                "usage_digest",
                "learning",
                "learning_digest",
            },
        )
    )
    created = await autopilot.propose("sales", body, USER)
    assert created["details"]["request_digest"] == expected


async def test_cross_agent_thread_cannot_learn(monkeypatch):
    from app.modules.agents import mission

    _, authorize, usage = setup(monkeypatch)
    mission.require_thread.return_value = {"agent_id": "another-agent"}
    with pytest.raises(HTTPException) as error:
        await module.collect_observations(request(), USER)
    assert error.value.status_code == 404
    authorize.assert_not_awaited()
    usage.assert_not_awaited()


async def test_deliverable_uses_only_pinned_factual_sources_and_never_markdown(monkeypatch):
    from app.modules.agents import mission
    from app.modules.agents.mission_schema import ObjectRef

    setup(monkeypatch)
    obj = ObjectRef(kind="investigation", id="investigation", revision=2)
    deliverable = SimpleNamespace(
        deliverable_id="deliverable",
        mission_revision=3,
        object_refs=[obj],
        markdown="PRIVATE-DOCUMENT-BODY",
        sources=[{"unrestricted_sql": "SELECT PRIVATE"}],
    )
    monkeypatch.setattr(
        mission.mission_service,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                thread_id="thread",
            )
        ),
    )
    monkeypatch.setattr(
        mission.mission_service, "deliverables", AsyncMock(return_value=[deliverable])
    )
    read = AsyncMock(
        return_value={
            "semantic": REF.model_dump(),
            "evidence": [
                {
                    "semantic": REF.model_dump(),
                    "id": "evidence",
                    "digest": "e" * 64,
                    "semantic_plan": {
                        "metrics": ["total_revenue"],
                        "filters": [
                            {
                                "field": "city",
                                "operator": "=",
                                "value": "PRIVATE-LITERAL",
                            }
                        ],
                    },
                }
            ],
        }
    )
    payload = read.return_value
    read.return_value = SimpleNamespace(model_dump=lambda **kwargs: payload)
    monkeypatch.setattr(module.intelligence_service, "get_for_mission", read)
    result = await module.collect_observations(
        request(
            [
                module.LearningSourceRef(
                    kind="mission_deliverable", id="deliverable", mission_id="mission", revision=3
                ),
            ]
        ),
        USER,
    )
    assert read.await_args.args[1] == obj.id
    assert read.await_args.kwargs["revision"] == obj.revision
    assert read.await_args.kwargs["mission_id"] == "mission"
    assert result["observations"][0]["authority"] == "lifecycle_evidence"
    assert "PRIVATE" not in str(result) and "markdown" not in str(result)


async def test_learning_review_refuses_revoked_sources_before_preview(monkeypatch):
    body, _, published, _ = proposal_setup(monkeypatch)
    learning = request()
    learning.semantic = body.base
    summary = {
        "digest": "d" * 64,
        "observations": [{"state": "INFERRED"}],
        "scope": Scope.from_user(USER).model_dump(exclude={"session_id"}),
    }
    collect = AsyncMock(return_value=summary)
    monkeypatch.setattr(autopilot, "collect_observations", collect)
    body = body.model_copy(
        update={
            "usage_digest": None,
            "learning": learning,
            "learning_digest": "d" * 64,
        }
    )
    created = await autopilot.propose("sales", body, USER)
    collect.side_effect = HTTPException(404, "Source revoked")
    with pytest.raises(HTTPException) as error:
        await autopilot._proposal("sales", created["proposal_id"], USER)
    assert error.value.status_code == 404
    published.assert_not_awaited()


async def test_historical_outcome_reads_its_dependency_against_the_mission_budget(monkeypatch):
    setup(monkeypatch)
    outcome = {
        "semantic": REF.model_dump(),
        "status": "complete",
        "decision_id": "decision",
        "decision_revision": 2,
        "attribution": "observed_after",
        "evidence": [],
    }
    decision = {
        "semantic": REF.model_dump(),
        "learning_enabled": True,
        "thread_id": "thread",
        "target_metric": "total_revenue",
    }

    async def pinned(kind, ident, user, **kwargs):
        assert kind == "outcomes" and kwargs["mission_id"] == "mission"
        kwargs["budget"].mission_records[("decisions", "decision")] = 2
        return SimpleNamespace(model_dump=lambda **kwargs: outcome)

    source = AsyncMock(side_effect=pinned)
    get = AsyncMock(return_value=SimpleNamespace(model_dump=lambda **kwargs: decision))
    monkeypatch.setattr(module.intelligence_service, "get_for_mission", source)
    monkeypatch.setattr(module.intelligence_service, "get", get)
    result = await module.collect_observations(
        request(
            [
                module.LearningSourceRef(
                    kind="outcome", id="outcome", revision=3, mission_id="mission"
                ),
            ]
        ),
        USER,
    )
    assert result["observations"][0]["attribution"] == "observed_after"
    assert get.await_args.args[0] == "decisions" and get.await_args.kwargs["revision"] == 2
    assert get.await_args.kwargs["budget"] is source.await_args.kwargs["budget"]


async def test_context_query_pattern_retains_usage_authority_and_current_observation(monkeypatch):
    from app.modules.intelligence import context_graph
    from app.modules.intelligence.contracts import ContextNode, KnowledgeState

    setup(monkeypatch)
    node = ContextNode(
        id="pattern-node",
        scope=Scope.from_user(USER),
        kind="query_pattern",
        name="Revenue usage",
        reference_id="pattern",
        semantic=REF,
        state=KnowledgeState.INFERRED,
        source_kind="usage",
    )
    monkeypatch.setattr(module.intelligence_service, "get", AsyncMock(return_value=node))
    derive = AsyncMock(return_value=node)
    monkeypatch.setattr(context_graph, "derive_authority", derive)
    source = module.LearningSourceRef(kind="context_query_pattern", id="pattern-node", revision=1)
    result = await module.collect_observations(request([source]), USER)
    assert result["observations"][0]["authority"] == "usage_observation"
    assert result["observations"][0]["state"] == "INFERRED"
    derive.return_value = node.model_copy(update={"source_kind": "unknown"})
    with pytest.raises(HTTPException) as error:
        await module.collect_observations(request([source]), USER)
    assert error.value.status_code == 404
