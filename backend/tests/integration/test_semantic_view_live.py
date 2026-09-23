"""Optional live Semantic View versioning and deterministic query contract."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from fastapi import HTTPException

from app.core.config import settings
from app.core.database import db
from app.core.security import encrypt_password
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.intelligence.semantic_view_schema import ensure_semantic_view_schema
from app.modules.intelligence.semantic_views import (
    SemanticViewCreate,
    SemanticViewQuery,
    SemanticViewVersionCreate,
    semantic_view_service,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("NOVA_LIVE_STARROCKS_TEST") != "1", reason="Live StarRocks test is opt-in"
)


async def test_semantic_view_validate_publish_query_and_version_swap():
    document = yaml.safe_load(
        Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
    )
    plan = SemanticPlan(metrics=("total_revenue",))
    compiled = SemanticCompiler().compile(
        SemanticModelIR.from_ossie(parse_ossie(yaml.safe_dump(document)).as_dict()), plan
    ).sql
    document["verified_queries"] = [{
        "verified_query_id": "total_revenue", "question": "Total revenue?",
        "semantic_plan": plan.as_dict(), "verified_sql": compiled,
    }]
    source = yaml.safe_dump(document)
    user = {
        "username": settings.STARROCKS_ROOT_USER,
        "encrypted_password": encrypt_password(settings.STARROCKS_ROOT_PASSWORD),
        "active_role": None,
        "session_id": None,
    }
    await db.init_system_pool()
    view_id = None
    try:
        await ensure_semantic_view_schema()
        created = await semantic_view_service.create(
            SemanticViewCreate(
                name="nova_sales", database="NOVA_DEMO",
                schema_name=f"test_{uuid4().hex[:8]}", definition=source,
            ),
            user,
        )
        view_id = created["id"]
        assert (await semantic_view_service.validate(view_id, 1, user))["valid"]
        await semantic_view_service.publish(view_id, 1, user)
        result = await semantic_view_service.query(
            view_id, SemanticViewQuery(metrics=["total_revenue"], limit=5), user
        )
        assert result["version"] == 1
        assert result["columns"] == ["total_revenue"]
        document["metrics"][0]["expression"]["dialects"][0]["expression"] = (
            "SUM(orders.total_amount) + 1"
        )
        changed_sql = SemanticCompiler().compile(
            SemanticModelIR.from_ossie(parse_ossie(yaml.safe_dump(document)).as_dict()), plan
        ).sql
        document["verified_queries"][0]["verified_sql"] = changed_sql
        second = await semantic_view_service.add_version(
            view_id, SemanticViewVersionCreate(definition=yaml.safe_dump(document)), user
        )
        assert second["version"] == 2
        report = await semantic_view_service.validate(view_id, 2, user)
        assert report["valid"]
        assert report["regression"]["changed"] == 1
        assert report["regression"]["cases"][0]["status"] == "result_changed"
        assert (await semantic_view_service.describe(view_id, user))["active_version"] == 1
        with pytest.raises(HTTPException) as error:
            await semantic_view_service.publish(view_id, 2, user)
        assert error.value.status_code == 409
        await semantic_view_service.publish(view_id, 2, user, acknowledge_regressions=True)
        assert (await semantic_view_service.describe(view_id, user))["active_version"] == 2
    finally:
        if view_id:
            await semantic_view_service.drop(view_id, user)
        await db.close_system_pool()
