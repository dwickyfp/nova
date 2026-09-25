"""Opt-in StarRocks smoke test for importing a legacy semantic model."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.database import db
from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.intelligence.semantic_migration import migrate_legacy_semantic_models
from app.modules.intelligence.semantic_view_schema import ensure_semantic_view_schema
from app.modules.intelligence.semantic_views import semantic_view_service

pytestmark = pytest.mark.skipif(
    os.environ.get("NOVA_LIVE_STARROCKS_TEST") != "1", reason="Live StarRocks test is opt-in"
)


@pytest.mark.asyncio
async def test_legacy_model_and_verified_query_import_as_private_view() -> None:
    model_id = str(uuid4())
    name = f"migration_{model_id[:8]}"
    definition = parse_ossie(
        Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
    ).as_dict()
    definition["name"] = name
    ir = SemanticModelIR.from_ossie(definition)
    plan = SemanticPlan(metrics=("total_revenue",))
    sql = SemanticCompiler().compile(ir, plan).sql
    verified_id = str(uuid4())

    await db.init_system_pool()
    try:
        await ensure_semantic_view_schema()
        await agent_repository.ensure_schema()
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_SEMANTIC_MODELS "
            "(semantic_model_id,owner_name,name,description,database_name,"
            "schema_name,ossie_version,definition,source_file_id,created_at,updated_at) "
            "VALUES (%s,'root',%s,'migration test','NOVA_DEMO','', '0.1.1',"
            "%s,'migration-source',NOW(),NOW())",
            [model_id, name, json.dumps(definition)],
        )
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_SEMANTIC_VERIFIED_QUERIES "
            "(verified_query_id,owner_name,semantic_model_id,model_fingerprint,"
            "question,semantic_plan,verified_sql,verified_by,verified_at,tags,"
            "usage_count,success_count) "
            "VALUES (%s,'root',%s,%s,'Total revenue?',%s,%s,'root',NOW(),'[]',0,0)",
            [verified_id, model_id, ir.fingerprint, json.dumps(plan.as_dict()), sql],
        )

        report = await migrate_legacy_semantic_models()
        assert not any(row["model_id"] == model_id for row in report["unresolved"])
        view = await semantic_view_service._get(model_id)
        assert view is not None
        assert view["visibility"] == "PRIVATE"
        assert view["active_version"] == 1
        version = await semantic_view_service._version(model_id, 1)
        assert version is not None
        assert len(version["definition"]["verified_queries"]) == 1
        assert version["validation"]["migration"]["source_file_id"] == "migration-source"

        second = await migrate_legacy_semantic_models()
        assert second["already_present"] >= 1
        assert (await semantic_view_service._version(model_id, 1)) == version
    finally:
        for sql, params in (
            (
                "DELETE FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VERIFIED_QUERIES "
                "WHERE verified_query_id=%s", [verified_id],
            ),
            (
                "DELETE FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS "
                "WHERE view_id=%s", [model_id],
            ),
            (
                "DELETE FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS WHERE id=%s", [model_id],
            ),
            (
                "DELETE FROM NOVA_SYSTEM.CONFIG_SEMANTIC_MODELS "
                "WHERE semantic_model_id=%s", [model_id],
            ),
        ):
            with suppress(Exception):
                await db.execute_system(sql, params)
        await db.close_system_pool()
