"""Legacy semantic metadata is copied once without widening visibility or losing VQRs."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.metadata_db import MetadataConflict
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.intelligence import semantic_migration as module
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
LEGACY_TIME = datetime(2026, 9, 1, tzinfo=UTC)


class _LegacyStore:
    """The Intelligence metadata repository, with the guards the migration relies on."""

    def __init__(self, *, collision: bool = False, count: int = 2):
        definition = parse_ossie(
            Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
        ).as_dict()
        ir = SemanticModelIR.from_ossie(definition)
        plan = SemanticPlan(metrics=("total_revenue",))
        sql = SemanticCompiler().compile(ir, plan).sql
        self.models = [[
            "legacy-model-id", "owner", "nova_sales", "Sales", "NOVA_DEMO", "",
            "0.1.1", definition, "source-file-id", LEGACY_TIME, LEGACY_TIME,
        ]]
        self.verified = [
            [
                f"query-{index}", "owner", "legacy-model-id", ir.fingerprint,
                f"Revenue question {index}", plan.as_dict(), sql,
                None, "owner", LEGACY_TIME, [], 0, 0,
            ]
            for index in range(count)
        ]
        self.views: dict[str, dict] = {}
        if collision:
            self.views["other-view"] = {
                "id": "other-view", "name": "nova_sales", "catalog_name": "default_catalog",
                "database_name": "NOVA_DEMO", "schema_name": "", "active_version": 1,
                "status": "ACTIVE", "visibility": "PUBLIC", "owner_name": "someone",
                "metadata_revision": 2,
            }
        self.versions: dict[str, dict] = {}
        self.actors: list[tuple] = []

    @asynccontextmanager
    async def transaction(self, actor, *, read_only=False):
        self.actors.append((actor.principal, actor.principal_type, read_only))
        from copy import deepcopy
        views, versions = deepcopy(self.views), deepcopy(self.versions)
        try:
            yield self
        except BaseException:
            self.views, self.versions = views, versions
            raise

    async def legacy_semantic_models(self):
        return [dict(zip(MODEL_COLUMNS, row, strict=True)) for row in self.models]

    async def legacy_verified_queries(self):
        return [dict(zip(VQR_COLUMNS, row, strict=True)) for row in self.verified]

    async def get_view(self, view_id):
        return self.views.get(view_id)

    async def find_view(self, catalog, database, schema, name):
        for view in self.views.values():
            if (view["catalog_name"], view["database_name"], view["schema_name"],
                    view["name"]) == (catalog, database, schema, name):
                return view
        return None

    async def get_version(self, view_id, version):
        assert version == 1
        return self.versions.get(view_id)

    async def create_view(self, *, catalog, database, schema, name, view_id, definition,
                          fingerprint, visibility="PUBLIC"):
        self.views[view_id] = {
            "id": view_id, "name": name, "catalog_name": catalog, "database_name": database,
            "schema_name": schema, "active_version": None, "status": "DRAFT",
            "visibility": visibility, "owner_name": self.actors[-1][0],
            "metadata_revision": 1,
        }
        await self.add_version(view_id, definition, fingerprint, expected_latest=0)
        return self.views[view_id]

    async def add_version(self, view_id, definition, fingerprint, *, expected_latest):
        assert expected_latest == 0 and view_id not in self.versions
        self.versions[view_id] = {
            "definition": json.loads(json.dumps(definition)), "fingerprint": fingerprint,
            "status": "DRAFT", "validation": None,
        }

    async def record_validation(self, view_id, version, report):
        stored = self.versions[view_id]
        assert stored["status"] in {"DRAFT", "VALIDATED"}
        stored["validation"] = json.loads(json.dumps(report))
        stored["status"] = "VALIDATED" if report.get("valid") is True else "DRAFT"
        return stored

    async def publish(self, view_id, version, *, expected_revision, expected_active):
        view = self.views[view_id]
        assert self.versions[view_id]["status"] == "VALIDATED"
        assert (view["metadata_revision"], view["active_version"]) == (
            expected_revision, expected_active,
        )
        self.versions[view_id]["status"] = "ACTIVE"
        view.update(active_version=version, status="ACTIVE",
                    metadata_revision=expected_revision + 1)
        return view


def _install(monkeypatch, store):
    monkeypatch.setattr(module, "intelligence_metadata", store.transaction)
    audit = AsyncMock()
    monkeypatch.setattr(module, "write_audit_log", audit)
    return audit


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
    store = _LegacyStore(collision=True, count=21)
    audit = _install(monkeypatch, store)

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
    # The store makes a new view its creator's draft: the import runs as the
    # migration service and records the legacy owner and times in the report.
    assert view["owner_name"] == "nova-service:semantic-migration"
    assert migration["legacy_owner"] == "owner"
    assert migration["legacy_created_at"] == LEGACY_TIME.isoformat()
    assert store.actors[0] == ("nova-service:semantic-migration", "SERVICE", True)
    assert {actor[:2] for actor in store.actors} == {
        ("nova-service:semantic-migration", "SERVICE")
    }
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["user_name"] == "nova-service:semantic-migration"

    second = await migrate_legacy_semantic_models()
    assert second["imported"] == 0
    assert second["already_present"] == 1
    assert len(store.versions) == 1
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_legacy_verified_query_remains_draft_for_review(monkeypatch):
    store = _LegacyStore(count=1)
    store.verified[0][6] = "DELETE FROM NOVA_DEMO.orders"
    _install(monkeypatch, store)

    report = await migrate_legacy_semantic_models()
    assert report["imported"] == 1
    assert report["drafts_needing_review"] == ["legacy-model-id"]
    assert store.views["legacy-model-id"]["status"] == "DRAFT"
    assert len(store.versions["legacy-model-id"]["definition"]["verified_queries"]) == 1


@pytest.mark.asyncio
async def test_unsupported_legacy_shape_becomes_private_review_draft(monkeypatch):
    store = _LegacyStore(count=0)
    raw = {"datasets": [], "metrics": []}
    store.models[0][2] = ""
    store.models[0][4] = None
    store.models[0][6] = "1.0"
    store.models[0][7] = raw
    _install(monkeypatch, store)

    report = await migrate_legacy_semantic_models()
    assert report["imported"] == 1
    assert report["drafts_needing_review"] == ["legacy-model-id"]
    assert report["unresolved"] == []
    view = store.views["legacy-model-id"]
    assert view["name"] == "legacy_legacymodelid"
    assert view["database_name"] == "NOVA_SYSTEM"
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
    store = _LegacyStore(count=1)
    store.verified[0][10] = "{malformed JSON"
    _install(monkeypatch, store)

    report = await migrate_legacy_semantic_models()
    assert report["imported"] == 0
    assert report["unresolved"] == [{
        "model_id": "legacy-model-id",
        "reason": "a legacy verified query cannot be decoded",
    }]
    assert "legacy-model-id" not in store.views
    assert len(store.verified) == 1


@pytest.mark.asyncio
async def test_a_validated_legacy_draft_is_published_on_rerun(monkeypatch):
    store = _LegacyStore(count=1)
    audit = _install(monkeypatch, store)
    original_publish = store.publish
    store.publish = AsyncMock(side_effect=MetadataConflict())

    first = await migrate_legacy_semantic_models()
    assert first["imported"] == 0
    assert first["unresolved"] == [{
        "model_id": "legacy-model-id", "reason": "the Semantic View changed during import",
    }]
    audit.assert_not_awaited()

    # A rolled-back import leaves nothing; simulate a validated draft left by an
    # earlier import whose publication did not happen.
    store.publish = original_publish
    await store.create_view(
        catalog="default_catalog", database="NOVA_DEMO", schema="", name="nova_sales",
        view_id="legacy-model-id", definition={"name": "nova_sales"}, fingerprint="f",
        visibility="PRIVATE",
    )
    await store.record_validation("legacy-model-id", 1, {
        "valid": True, "migration": {"source": "CONFIG_SEMANTIC_MODELS"},
    })
    second = await migrate_legacy_semantic_models()
    assert second["already_present"] == 1 and second["imported"] == 0
    assert store.views["legacy-model-id"]["status"] == "ACTIVE"
    assert store.versions["legacy-model-id"]["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_a_foreign_view_with_the_legacy_id_is_not_taken_over(monkeypatch):
    store = _LegacyStore(count=0)
    _install(monkeypatch, store)
    store.actors.append(("someone", "USER", False))
    await store.create_view(
        catalog="default_catalog", database="NOVA_DEMO", schema="", name="other",
        view_id="legacy-model-id", definition={"name": "other"}, fingerprint="f",
    )

    report = await migrate_legacy_semantic_models()
    assert report["unresolved"] == [{
        "model_id": "legacy-model-id", "reason": "existing view ID is not a legacy import",
    }]
    assert store.views["legacy-model-id"]["status"] == "DRAFT"
