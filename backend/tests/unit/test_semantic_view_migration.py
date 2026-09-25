"""Legacy semantic metadata is copied once without widening visibility or losing VQRs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.intelligence.semantic_migration import migrate_legacy_semantic_models
from app.modules.intelligence.semantic_view_schema import ensure_semantic_view_schema

MODEL_COLUMNS = [
    "semantic_model_id", "owner_name", "name", "description", "database_name",
    "schema_name", "ossie_version", "definition", "source_file_id", "created_at",
    "updated_at",
]
VQR_COLUMNS = [
    "verified_query_id", "owner_name", "semantic_model_id", "model_fingerprint",
    "question", "semantic_plan", "verified_sql", "expected_result_signature",
    "verified_by", "verified_at", "tags", "usage_count", "success_count",
]


class _LegacyStore:
    def __init__(self, *, collision: bool = False, count: int = 2):
        definition = parse_ossie(
            Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
        ).as_dict()
        ir = SemanticModelIR.from_ossie(definition)
        plan = SemanticPlan(metrics=("total_revenue",))
        sql = SemanticCompiler().compile(ir, plan).sql
        self.models = [[
            "legacy-model-id", "owner", "nova_sales", "Sales", "NOVA_DEMO", "",
            "0.1.1", json.dumps(definition), "source-file-id", "2026-09-01 00:00:00",
            "2026-09-01 00:00:00",
        ]]
        self.verified = [
            [
                f"query-{index}", "owner", "legacy-model-id", ir.fingerprint,
                f"Revenue question {index}", json.dumps(plan.as_dict()), sql,
                None, "owner", "2026-09-01 00:00:00", "[]", 0, 0,
            ]
            for index in range(count)
        ]
        self.views: dict[str, dict] = {}
        if collision:
            self.views["other-view"] = {
                "name": "nova_sales", "catalog": "default_catalog",
                "database": "NOVA_DEMO", "schema": "", "active_version": 1,
                "status": "ACTIVE", "visibility": "PUBLIC",
            }
        self.versions: dict[str, dict] = {}

    async def execute_system(self, sql: str, params: list | None = None) -> dict:
        params = params or []
        if sql.startswith("SELECT semantic_model_id,owner_name"):
            return {"columns": MODEL_COLUMNS, "rows": self.models}
        if sql.startswith("SELECT verified_query_id,owner_name"):
            return {"columns": VQR_COLUMNS, "rows": self.verified}
        if sql.startswith("SELECT name,catalog_name"):
            view = self.views.get(params[0])
            return {"rows": [[view[key] for key in (
                "name", "catalog", "database", "schema", "active_version", "status"
            )]] if view else []}
        if sql.startswith("SELECT id FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS"):
            matches = [
                view_id for view_id, view in self.views.items()
                if [view[key] for key in ("catalog", "database", "schema", "name")] == params
            ]
            return {"rows": [[view_id] for view_id in matches]}
        if sql.startswith("SELECT validation FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS"):
            version = self.versions.get(params[0])
            return {"rows": [[version["validation"]]] if version else []}
        if sql.startswith("INSERT INTO NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS"):
            catalog, database, schema, name, view_id, _owner, *_dates = params
            self.views[view_id] = {
                "name": name, "catalog": catalog, "database": database,
                "schema": schema, "active_version": None, "status": "DRAFT",
                "visibility": "PRIVATE",
            }
            return {"rows": []}
        if sql.startswith("INSERT INTO NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS"):
            view_id, definition, fingerprint, status, validation, *_dates = params
            self.versions[view_id] = {
                "definition": json.loads(definition), "fingerprint": fingerprint,
                "status": status, "validation": json.loads(validation),
            }
            return {"rows": []}
        if sql.startswith("UPDATE NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS"):
            view = self.views[params[0]]
            view["active_version"] = 1
            view["status"] = "ACTIVE"
            return {"rows": []}
        raise AssertionError(f"Unexpected SQL: {sql}")


@pytest.mark.asyncio
async def test_visibility_column_upgrade_is_idempotent(monkeypatch):
    import app.modules.intelligence.semantic_view_schema as module

    async def execute(sql: str):
        if sql.startswith("ALTER TABLE"):
            raise RuntimeError("Duplicate column visibility already exists")
        return {"rows": []}

    monkeypatch.setattr(module.db, "execute_system", execute)
    await ensure_semantic_view_schema()


@pytest.mark.asyncio
async def test_migration_preserves_private_identity_vqr_and_is_idempotent(monkeypatch):
    import app.modules.intelligence.semantic_migration as module

    store = _LegacyStore(collision=True, count=21)
    monkeypatch.setattr(module.db, "execute_system", store.execute_system)
    audit = AsyncMock()
    monkeypatch.setattr(module, "write_audit_log", audit)

    first = await migrate_legacy_semantic_models()
    assert first["imported"] == 1
    assert not first["unresolved"]
    view = store.views["legacy-model-id"]
    assert view["visibility"] == "PRIVATE"
    assert view["status"] == "ACTIVE"
    assert view["name"] == "nova_sales_legacymo"
    definition = store.versions["legacy-model-id"]["definition"]
    assert definition["name"] == view["name"]
    assert len(definition["verified_queries"]) == 21
    migration = store.versions["legacy-model-id"]["validation"]["migration"]
    assert migration["source_checks_executed"] is False
    assert migration["source_file_id"] == "source-file-id"
    audit.assert_awaited_once()

    second = await migrate_legacy_semantic_models()
    assert second["imported"] == 0
    assert second["already_present"] == 1
    assert len(store.versions) == 1
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_legacy_verified_query_remains_draft_for_review(monkeypatch):
    import app.modules.intelligence.semantic_migration as module

    store = _LegacyStore(count=1)
    store.verified[0][6] = "DELETE FROM NOVA_DEMO.orders"
    monkeypatch.setattr(module.db, "execute_system", store.execute_system)
    monkeypatch.setattr(module, "write_audit_log", AsyncMock())

    report = await migrate_legacy_semantic_models()
    assert report["imported"] == 1
    assert report["drafts_needing_review"] == ["legacy-model-id"]
    assert store.views["legacy-model-id"]["status"] == "DRAFT"
    assert len(store.versions["legacy-model-id"]["definition"]["verified_queries"]) == 1


@pytest.mark.asyncio
async def test_unsupported_legacy_shape_becomes_private_review_draft(monkeypatch):
    import app.modules.intelligence.semantic_migration as module

    store = _LegacyStore(count=0)
    raw = {"datasets": [], "metrics": []}
    store.models[0][2] = ""
    store.models[0][4] = None
    store.models[0][6] = "1.0"
    store.models[0][7] = json.dumps(raw)
    monkeypatch.setattr(module.db, "execute_system", store.execute_system)
    monkeypatch.setattr(module, "write_audit_log", AsyncMock())

    report = await migrate_legacy_semantic_models()
    assert report["imported"] == 1
    assert report["drafts_needing_review"] == ["legacy-model-id"]
    assert report["unresolved"] == []
    view = store.views["legacy-model-id"]
    assert view["name"] == "legacy_legacymodelid"
    assert view["database"] == "NOVA_SYSTEM"
    assert view["visibility"] == "PRIVATE"
    assert view["status"] == "DRAFT"
    version = store.versions["legacy-model-id"]
    assert version["definition"] == {**raw, "verified_queries": []}
    assert version["status"] == "DRAFT"
    assert version["validation"]["errors"][0].startswith(
        "Unsupported legacy Ossie version 1.0"
    )
    assert version["validation"]["migration"]["raw_definition_preserved"] is True
    assert version["validation"]["migration"]["raw_definition"] == raw


@pytest.mark.asyncio
async def test_malformed_legacy_vqr_blocks_import_without_dropping_row(monkeypatch):
    import app.modules.intelligence.semantic_migration as module

    store = _LegacyStore(count=1)
    store.verified[0][10] = "{malformed JSON"
    monkeypatch.setattr(module.db, "execute_system", store.execute_system)
    monkeypatch.setattr(module, "write_audit_log", AsyncMock())

    report = await migrate_legacy_semantic_models()
    assert report["imported"] == 0
    assert report["unresolved"] == [{
        "model_id": "legacy-model-id",
        "reason": "a legacy verified query cannot be decoded",
    }]
    assert "legacy-model-id" not in store.views
    assert len(store.verified) == 1
