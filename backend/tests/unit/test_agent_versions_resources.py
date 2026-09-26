import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.agents import repository, resources, router, versions
from app.modules.agents.registry import build_registry
from app.modules.agents.tools.ai_search import AISearchTool
from app.modules.agents.tools.intelligence_views import FeatureLookupTool
from app.modules.assistant.tools import ToolInvocation

AGENT = {
    "agent_id": "sales",
    "owner_name": "alice",
    "name": "Sales",
    "config_revision": "r1",
    "semantic_view_ids": [],
    "default_tools": ["ai_search"],
    "instructions_response": "Use net revenue",
    "created_at": "2026-09-26T00:00:00",
    "updated_at": "2026-09-26T00:00:00",
    "resource_bindings": {
        "search_indexes": [{"index": "sales_docs", "filters": {"region": "ID"}}],
        "feature_groups": ["customers"],
    },
}
USER = {"username": "alice", "encrypted_password": "sealed", "active_role": "sales"}


@pytest.fixture
def context():
    return SimpleNamespace(agent_id="sales", user=USER, role="regional_sales", audit_session_id="s")


@pytest.fixture
def history(monkeypatch):
    rows = {}

    async def execute(sql, params=None):
        if sql.startswith("INSERT"):
            version, agent, owner, config, label, created = params
            assert version not in rows
            rows[version] = (agent, owner, config, label, created)
            return {"affected": 1}
        agent, owner, *rest = params
        if "version_id=%s" in sql:
            version = rest[0]
            row = rows.get(version)
            return {"rows": [[version, *row[2:]]] if row and row[:2] == (agent, owner) else []}
        limit, offset = rest
        return {
            "rows": [
                [key, row[3], row[4]]
                for key, row in reversed(list(rows.items()))
                if row[:2] == (agent, owner)
            ][offset : offset + limit]
        }

    monkeypatch.setattr(versions.db, "execute_system", execute)
    return rows


@pytest.mark.asyncio
async def test_snapshots_are_immutable_and_owner_scoped(history):
    snapshot = await versions.agent_versions.store(AGENT, label="Baseline", version_id="r1")
    assert snapshot["configuration"]["resource_bindings"] == AGENT["resource_bindings"]
    assert "owner_name" not in snapshot["configuration"]
    assert await versions.agent_versions.get("sales", "bob", "r1") is None
    assert await versions.agent_versions.get("other", "alice", "r1") is None
    again = await versions.agent_versions.store(AGENT, label="Other label", version_id="r1")
    assert again["label"] == "Baseline"
    with pytest.raises(HTTPException, match="cannot be modified"):
        await versions.agent_versions.store(
            {**AGENT, "name": "Different"}, label="Bad", version_id="r1"
        )
    assert len(history) == 1


@pytest.mark.asyncio
async def test_history_pagination(history):
    for i in range(4):
        await versions.agent_versions.store(AGENT, label=f"Draft {i}")
    first = await versions.agent_versions.list("sales", "alice", 0, limit=2)
    second = await versions.agent_versions.list("sales", "alice", 2, limit=2)
    assert first["has_more"] and not second["has_more"]
    assert not (
        {v["version_id"] for v in first["versions"]} & {v["version_id"] for v in second["versions"]}
    )
    assert not (await versions.agent_versions.list("sales", "bob", 0))["versions"]


@pytest.mark.asyncio
async def test_snapshot_rejects_credentials_before_writing(history):
    with pytest.raises(HTTPException) as error:
        await versions.agent_versions.store(
            {**AGENT, "instructions_response": "api_key=sk-secret12345678901234567890"},
            label="Draft",
        )
    assert error.value.status_code == 422
    assert not history


@pytest.mark.asyncio
async def test_save_draft_never_updates_active_agent(history, monkeypatch):
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value=deepcopy(AGENT)))
    monkeypatch.setattr(router, "write_audit_log", AsyncMock())
    update = AsyncMock()
    monkeypatch.setattr(repository.agent_repository, "update_agent", update)
    draft = await router.save_agent_draft(
        "sales",
        versions.AgentDraftRequest(
            configuration={"instructions_response": "Use gross revenue"},
            expected_revision="r1",
        ),
        USER,
    )
    assert draft["configuration"]["instructions_response"] == "Use gross revenue"
    assert history["r1"][0] == "sales"
    update.assert_not_awaited()
    with pytest.raises(HTTPException) as error:
        await router.save_agent_draft(
            "sales", versions.AgentDraftRequest(configuration={}, expected_revision="stale"), USER
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["SAVE_DRAFT", "PUBLISH_VERSION"])
async def test_version_routes_write_audit_within_column_limits(monkeypatch, action):
    from app.common.audit import write_audit_log

    snapshot_id, active_id = str(uuid4()), str(uuid4())
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value=deepcopy(AGENT)))
    monkeypatch.setattr(router, "write_audit_log", write_audit_log)
    snapshot = {"version_id": snapshot_id, "configuration": {"name": "Sales updated"}}
    monkeypatch.setattr(versions.agent_versions, "store", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(versions.agent_versions, "get", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(
        repository.agent_repository, "update_agent",
        AsyncMock(return_value={**AGENT, "config_revision": active_id}),
    )
    records = []

    async def execute(sql, params):
        assert "INSERT INTO NOVA_SYSTEM.AUDIT_LOG" in sql
        columns = sql.split("(", 1)[1].split(")", 1)[0].replace("\n", "").split(",")
        columns = [column.strip() for column in columns if column.strip() != "event_time"]
        record = dict(zip(columns, params, strict=True))
        assert record["decision"] is None or len(record["decision"].encode()) <= 32
        records.append(record)
        return {"affected": 1}

    monkeypatch.setattr(versions.db, "execute_system", execute)
    if action == "SAVE_DRAFT":
        await router.save_agent_draft(
            "sales", versions.AgentDraftRequest(configuration={}, expected_revision="r1"), USER
        )
        expected = {"version_id": snapshot_id}
    else:
        await router.publish_agent_version(
            "sales", snapshot_id, versions.AgentPublishRequest(expected_revision="r1"), USER
        )
        expected = {"source_version": snapshot_id, "active_version": active_id}
    assert len(records) == 1
    assert records[0]["action"] == action
    assert records[0]["object_name"] == "sales"
    assert json.loads(records[0]["sql_text"]) == expected


@pytest.mark.asyncio
async def test_publish_checks_owner_revision_and_resources(history, monkeypatch):
    saved = await versions.agent_versions.store(AGENT, label="Restore")
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value=deepcopy(AGENT)))
    monkeypatch.setattr(router, "_normalize_view_binding", AsyncMock())
    validate = AsyncMock()
    monkeypatch.setattr(resources, "validate_resources", validate)
    monkeypatch.setattr(router, "_unavailable_mcp_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(router, "write_audit_log", AsyncMock())
    update = AsyncMock(return_value=AGENT)
    monkeypatch.setattr(repository.agent_repository, "update_agent", update)
    await router.publish_agent_version(
        "sales", saved["version_id"], versions.AgentPublishRequest(expected_revision="r1"), USER
    )
    assert update.call_args.kwargs["check_revision"] is True
    assert update.call_args.kwargs["expected_revision"] == "r1"
    assert update.call_args.kwargs["fields"]["resource_bindings"] == AGENT["resource_bindings"]
    validate.assert_awaited_once()
    update.reset_mock()
    with pytest.raises(HTTPException) as error:
        await router.publish_agent_version(
            "sales",
            saved["version_id"],
            versions.AgentPublishRequest(expected_revision="old"),
            USER,
        )
    assert error.value.status_code == 409
    update.assert_not_awaited()
    with pytest.raises(HTTPException) as error:
        await router.publish_agent_version(
            "sales",
            saved["version_id"],
            versions.AgentPublishRequest(expected_revision="r1"),
            {**USER, "username": "bob"},
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("affected", [0, 1])
async def test_publication_compare_and_swap(monkeypatch, affected):
    repo = repository.AgentRepository()
    monkeypatch.setattr(repo, "get_agent", AsyncMock(return_value=AGENT))
    monkeypatch.setattr(versions.agent_versions, "store", AsyncMock())
    execute = AsyncMock(return_value={"affected": affected})
    monkeypatch.setattr(repository.db, "execute_system", execute)
    if affected:
        await repo.update_agent(
            "sales",
            owner_name="alice",
            fields={"name": "New"},
            expected_revision="r1",
            check_revision=True,
        )
    else:
        with pytest.raises(HTTPException) as error:
            await repo.update_agent(
                "sales",
                owner_name="alice",
                fields={"name": "New"},
                expected_revision="r1",
                check_revision=True,
            )
        assert error.value.status_code == 409
    sql, params = execute.call_args.args
    assert "AND config_revision = %s" in sql
    assert params[-3:] == ["sales", "alice", "r1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("filters", [{"region": "US"}, {"region": True}, {"region": None}])
async def test_search_scope_cannot_be_overridden(context, monkeypatch, filters):
    query = AsyncMock()
    monkeypatch.setattr("app.modules.agents.tools.ai_search.search_service.query", query)
    tool = AISearchTool(AGENT["resource_bindings"])
    result = await tool.run(
        ToolInvocation(
            "c", "ai_search", {"index": "sales_docs", "query": "sales", "filters": filters}
        ),
        context,
    )
    assert not result.ok and result.error_class == "POLICY_VIOLATION"
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_bound_search_injects_fixed_filter_and_keeps_caller_role(context, monkeypatch):
    query = AsyncMock(return_value={"hits": [], "version": 2})
    monkeypatch.setattr("app.modules.agents.tools.ai_search.search_service.query", query)
    tool = build_registry(AGENT).get("ai_search")
    result = await tool.run(
        ToolInvocation(
            "c", "ai_search", {"index": "sales_docs", "query": "sales", "filters": {"year": 2026}}
        ),
        context,
    )
    assert result.ok
    assert query.call_args.args[1].filters == {"region": "ID", "year": 2026}
    assert query.call_args.args[2]["active_role"] == "regional_sales"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [AISearchTool(), AISearchTool(AGENT["resource_bindings"])])
async def test_foreign_search_denied_before_query(context, monkeypatch, tool):
    query = AsyncMock()
    monkeypatch.setattr("app.modules.agents.tools.ai_search.search_service.query", query)
    result = await tool.run(
        ToolInvocation("c", "ai_search", {"index": "finance", "query": "secrets"}), context
    )
    assert not result.ok
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_bound_search_still_denied_by_user_permissions(context, monkeypatch):
    query = AsyncMock(side_effect=HTTPException(403, "denied"))
    monkeypatch.setattr("app.modules.agents.tools.ai_search.search_service.query", query)
    result = await AISearchTool(AGENT["resource_bindings"]).run(
        ToolInvocation("c", "ai_search", {"index": "sales_docs", "query": "sales"}), context
    )
    assert not result.ok and "unauthorized" in result.error


@pytest.mark.asyncio
async def test_feature_lookup_requires_binding(context, monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr("app.modules.agents.tools.intelligence_views.feature_store.lookup", lookup)
    result = await FeatureLookupTool().run(
        ToolInvocation("c", "feature_lookup", {"group": "customers", "entity_key": {"id": 1}}),
        context,
    )
    assert not result.ok and result.error_class == "POLICY_VIOLATION"
    lookup.assert_not_awaited()


def test_resource_schema_rejects_duplicates_and_nonfinite_filters():
    for value in [
        {"search_indexes": [{"index": "a"}, {"index": "a"}]},
        {"search_indexes": [{"index": "a", "filters": {"x": float("nan")}}]},
        {"feature_groups": ["a", "a"]},
        {"unrestricted": True},
    ]:
        with pytest.raises(ValidationError):
            resources.ResourceBindings.model_validate(value)


@pytest.mark.asyncio
async def test_binding_validation_rejects_unavailable_and_unknown_filter(monkeypatch):
    monkeypatch.setattr(
        "app.modules.intelligence.search.search_service.list",
        AsyncMock(
            return_value=[
                {"name": "sales_docs", "active_version": 1, "filter_columns": ["region"]},
            ]
        ),
    )
    for binding in [{"index": "finance"}, {"index": "sales_docs", "filters": {"hidden": "x"}}]:
        with pytest.raises(HTTPException) as error:
            await resources.validate_resources(
                {"resource_bindings": {"search_indexes": [binding]}}, USER
            )
        assert error.value.status_code == 422
    fields = {
        "resource_bindings": {
            "search_indexes": [{"index": "sales_docs", "filters": {"region": "ID"}}]
        }
    }
    await resources.validate_resources(fields, USER)
    assert fields["resource_bindings"]["feature_groups"] == []


@pytest.mark.asyncio
async def test_unpublished_resources_and_revoked_catalog_access(monkeypatch):
    monkeypatch.setattr(
        "app.modules.intelligence.search.search_service.list", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "app.modules.intelligence.feature_store.feature_store.list_groups",
        AsyncMock(return_value=[]),
    )
    assert await resources.authorized_resources(AGENT["resource_bindings"], USER) == {
        "search_indexes": [],
        "feature_groups": [],
    }
    with pytest.raises(HTTPException):
        await resources.validate_resources(
            {"resource_bindings": {"feature_groups": ["customers"]}}, USER
        )


@pytest.mark.asyncio
async def test_additive_columns_do_not_invalidate_existing_role_verification(monkeypatch):
    from app.modules.agents.access import access_fingerprint

    monkeypatch.setattr(
        "app.modules.agents.access.resolve_agent_dependencies", AsyncMock(return_value=[])
    )
    old = {"agent_id": "legacy", "owner_name": "alice", "name": "Sales", "semantic_view_ids": []}
    baseline = await access_fingerprint(old)
    assert (
        await access_fingerprint({**old, "config_revision": None, "resource_bindings": {}})
        == baseline
    )
    assert (
        await access_fingerprint(
            {
                **old,
                "config_revision": "r2",
                "resource_bindings": {"search_indexes": [], "feature_groups": []},
            }
        )
        == baseline
    )
    assert (
        await access_fingerprint({**old, "resource_bindings": AGENT["resource_bindings"]})
        != baseline
    )


@pytest.mark.asyncio
async def test_history_failure_prevents_configuration_update(monkeypatch):
    repo = repository.AgentRepository()
    monkeypatch.setattr(repo, "get_agent", AsyncMock(return_value=AGENT))
    monkeypatch.setattr(
        versions.agent_versions, "store", AsyncMock(side_effect=RuntimeError("unavailable"))
    )
    execute = AsyncMock()
    monkeypatch.setattr(repository.db, "execute_system", execute)
    with pytest.raises(RuntimeError):
        await repo.update_agent("sales", owner_name="alice", fields={"name": "New"})
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_publication_revalidates_deleted_dependencies(monkeypatch):
    monkeypatch.setattr(
        repository.agent_repository, "list_custom_tools", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(repository.agent_repository, "list_skills", AsyncMock(return_value=[]))
    for fields in [{"default_tools": ["custom:deleted"]}, {"default_skills": ["deleted-skill"]}]:
        with pytest.raises(HTTPException) as error:
            await versions.validate_publication_dependencies(fields, "alice")
        assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_publication_checks_response_model_without_inference(monkeypatch):
    monkeypatch.setattr(
        "app.modules.ai_ml.service.ai_service.get_provider",
        AsyncMock(return_value={"is_active": True}),
    )
    monkeypatch.setattr(
        "app.modules.ai_ml.service.ai_service.list_models",
        AsyncMock(
            return_value=[
                {"name": "decision-only", "type": "decision", "is_active": True},
                {"name": "response", "type": "llm", "is_active": True},
            ]
        ),
    )
    with pytest.raises(HTTPException):
        await versions.validate_publication_dependencies(
            {"model_provider_id": "p", "model_name": "decision-only"}, "alice"
        )
    await versions.validate_publication_dependencies(
        {"model_provider_id": "p", "model_name": "response"}, "alice"
    )


@pytest.mark.asyncio
async def test_publish_and_restore_roundtrip_keeps_all_snapshots(monkeypatch):
    import json

    state = {**versions.configuration(AGENT), **deepcopy(AGENT)}
    snapshots = {}

    async def execute(sql, params=None):
        if "SELECT" in sql and "CONFIG_AGENTS " in sql:
            if params != ["sales", "alice"]:
                return {"rows": []}
            columns = repository._AGENT_COLUMNS.split(", ")
            return {"rows": [[state.get(column) for column in columns]]}
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_AGENTS"):
            assert "AND config_revision = %s" in sql
            if params[-1] != state["config_revision"]:
                return {"affected": 0}
            fields = sql.split(" SET ")[1].split(" WHERE ")[0].split(", ")
            for field, value in zip(fields, params, strict=False):
                key = field.split(" = ")[0]
                state[key] = (
                    json.loads(value) if key in {"resource_bindings", "default_tools"} else value
                )
            return {"affected": 1}
        if sql.startswith("INSERT"):
            version, agent_id, owner, config, label, created = params
            assert version not in snapshots
            snapshots[version] = [agent_id, owner, config, label, created]
            return {"affected": 1}
        if "CONFIG_AGENT_VERSIONS" in sql:
            agent_id, owner, version = params
            row = snapshots.get(version)
            return {"rows": [[version, *row[2:]]] if row and row[:2] == [agent_id, owner] else []}
        raise AssertionError(sql)

    monkeypatch.setattr(repository.db, "execute_system", execute)
    repo = repository.AgentRepository()
    initial = await repo.get_agent("sales", owner_name="alice")
    draft = await versions.agent_versions.store(
        {**initial, "instructions_response": "Gross"}, label="Draft"
    )
    assert state["instructions_response"] == "Use net revenue"
    published = await repo.update_agent(
        "sales",
        owner_name="alice",
        fields={"instructions_response": "Gross"},
        expected_revision="r1",
        check_revision=True,
    )
    baseline = await versions.agent_versions.get("sales", "alice", "r1")
    restored = await repo.update_agent(
        "sales",
        owner_name="alice",
        fields={"instructions_response": baseline["configuration"]["instructions_response"]},
        expected_revision=published["config_revision"],
        check_revision=True,
    )
    assert restored["instructions_response"] == "Use net revenue"
    assert (
        len({initial["config_revision"], published["config_revision"], restored["config_revision"]})
        == 3
    )
    assert len(snapshots) == 4
    assert (await versions.agent_versions.get("sales", "alice", draft["version_id"]))[
        "configuration"
    ]["instructions_response"] == "Gross"
