import os
from uuid import uuid4

import pytest

from app.core.database import db
from app.core.security import encrypt_password
from app.modules.query.service import QueryService
from app.sql_frontend.analysis.effects import PlanEffects
from app.sql_frontend.analysis.semantics import StatementSemantics, default_semantics
from app.sql_frontend.ast.builder import AstBuilderRegistry, ast_builders
from app.sql_frontend.ast.statements import Statement
from app.sql_frontend.binding.relations import RelationBinder
from app.sql_frontend.capabilities.starrocks import resolve_engine_capabilities
from app.sql_frontend.context import ExecutionContext
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import Atomicity, CompositePlan
from app.sql_frontend.planning.planner import SQLPlanner
from app.sql_frontend.planning.registry import PlannerRegistry

pytestmark = pytest.mark.engine


def composite_service(sqls):
    class TransactionStatement(Statement):
        pass

    class Planner:
        async def plan(self, logical, context):
            steps = [
                await SQLPlanner().plan(ast_builders.build(parse_statement(sql)), context)
                for sql in sqls
            ]
            return CompositePlan(tuple(steps), Atomicity.SINGLE_ENGINE_TRANSACTION)

    registry = PlannerRegistry()
    registry.register(TransactionStatement, Planner())
    builders = AstBuilderRegistry()
    builders.register("QueryStatementContext", TransactionStatement)
    semantics = default_semantics()
    semantics.register(
        TransactionStatement,
        StatementSemantics(
            lambda _: PlanEffects(
                reads_data=True, writes_data=True, updates_rows=True, deletes_rows=True
            )
        ),
    )
    return QueryService(builders=builders, planner=SQLPlanner(registry, semantics=semantics))


@pytest.fixture
async def transaction_database(app):
    name = "hardening_" + uuid4().hex[:12]
    role = name + "_role"
    try:
        await db.execute_system(f"CREATE DATABASE {name}")
        await db.execute_system(f"CREATE ROLE {role}")
        await db.execute_system(f"GRANT ALL ON {name}.* TO ROLE {role}")
        await db.execute_system(f"GRANT {role} TO USER nova_admin")
        for table in ("a", "b"):
            await db.execute_system(
                f"CREATE TABLE {name}.{table} (id INT NOT NULL) PRIMARY KEY(id) "
                "DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1')"
            )
        yield name, role
    finally:
        await db.execute_system(f"DROP DATABASE IF EXISTS {name}")
        await db.execute_system(f"DROP ROLE IF EXISTS {role}")


async def execute(service, database, role):
    return await service.execute_statements(
        "SELECT 1",
        "nova_admin",
        encrypt_password(os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")),
        database=database,
        role=role,
        confirm_destructive=True,
    )


async def test_composite_commits_real_rows_using_production_adapter(transaction_database):
    database, role = transaction_database
    service = composite_service(["INSERT INTO a VALUES(1)", "INSERT INTO b VALUES(2)"])
    results = await execute(service, database, role)
    assert results[0].success, results[0].error
    assert "Composite transaction committed" in results[0].warnings
    assert (await db.execute_system(f"SELECT id FROM {database}.a"))["rows"] == [[1]]
    assert (await db.execute_system(f"SELECT id FROM {database}.b"))["rows"] == [[2]]


async def test_composite_rolls_back_real_rows_after_engine_failure(transaction_database):
    database, role = transaction_database
    service = composite_service(["INSERT INTO a VALUES(1)", "INSERT INTO b VALUES(1,2)"])
    results = await execute(service, database, role)
    assert not results[0].success and results[0].execution_failure["outcome"] == "rolled_back"
    assert results[0].execution_failure["completed_steps"] == [0]
    assert (await db.execute_system(f"SELECT id FROM {database}.a"))["rows"] == []


async def test_shared_nothing_rejects_update_before_begin(transaction_database):
    from app.sql_frontend.capabilities.starrocks import resolve_engine_capabilities

    caps = await resolve_engine_capabilities()
    if caps.identity.deployment_mode != "shared_nothing":
        pytest.skip("This acceptance scenario requires shared-nothing")
    database, role = transaction_database
    service = composite_service(["UPDATE a SET id=2"])
    results = await execute(service, database, role)
    assert not results[0].success and "deployment" in results[0].error
    assert (await db.execute_system(f"SELECT id FROM {database}.a"))["rows"] == []


async def test_http_script_confirmation_and_native_regressions(
    client, admin_token, transaction_database
):
    database, role = transaction_database
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "username": "nova_admin",
            "password": os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!"),
        },
    )
    assert login.status_code == 200
    client.headers["Authorization"] = "Bearer " + login.json()["access_token"]

    async def query(sql, confirmed=False):
        response = await client.post(
            "/api/v1/query/execute",
            json={"sql": sql, "database": database, "confirm_destructive": confirmed},
        )
        assert response.status_code == 200, response.text
        return response.json()

    activation = await query("SET ROLE " + role)
    assert activation[0]["success"]
    results = await query("INSERT INTO a VALUES(1); DELETE FROM a WHERE id=1")
    assert len(results) == 1 and results[0]["needs_confirmation"]
    assert results[0]["statement_kind"] == "script" and results[0]["effects"]["deletes_rows"]
    assert (await db.execute_system(f"SELECT * FROM {database}.a"))["rows"] == []
    confirmed = await query("INSERT INTO a VALUES(1); DELETE FROM a WHERE id=1", True)
    assert all(result["success"] and not result["needs_confirmation"] for result in confirmed)
    for sql in [
        "INSERT INTO a VALUES(1),(2)",
        "SELECT * FROM (SELECT id FROM a) s",
        "WITH c AS (SELECT * FROM a) SELECT * FROM c",
        "SELECT id,ROW_NUMBER() OVER(ORDER BY id) AS rn FROM a",
        "SHOW CREATE TABLE a",
        "SELECT @@version",
        "EXPLAIN SELECT * FROM a",
    ]:
        results = await query(sql)
        assert results[0]["success"], results[0].get("error")


async def test_live_stage_rewrite_and_authorized_schema_helper(client, admin_token):
    from dataclasses import replace

    from app.sql_frontend.binding.models import BoundRelation
    from app.sql_frontend.rules.registry import RuleRegistry
    from app.sql_frontend.rules.stage import StageReferenceRule

    client.headers["Authorization"] = f"Bearer {admin_token}"
    response = await client.post("/api/v1/query/execute", json={"sql": "SET ROLE ACCOUNTADMIN"})
    assert response.status_code == 200 and response.json()[0]["success"]

    class Rewrite:
        name = "length_changing_rule"

        def matches(self, plan, context):
            return bool(getattr(plan, "stage_bindings", ()))

        async def apply(self, plan, context):
            return replace(
                plan, engine_sql="/* harmless longer prefix */ " + plan.engine_sql + " LIMIT 1"
            )

    rules = RuleRegistry()
    rules.register(StageReferenceRule())
    rules.register(Rewrite())
    service = QueryService(planner=SQLPlanner(rules=rules))
    encrypted = encrypt_password(os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!"))
    result = await service.execute(
        "SELECT * FROM @products.products_new.csv",
        "nova_admin",
        encrypted,
        database="NOVA_ANALYTICS",
        schema="public",
        role="ACCOUNTADMIN",
    )
    assert result.success and result.row_count == 1
    assert result.executed_sql.startswith("/* harmless longer prefix */")
    assert result.executed_sql.endswith("LIMIT 1") and "minioadmin" not in result.executed_sql
    context = ExecutionContext(
        "nova_admin",
        database="NOVA_ANALYTICS",
        schema="public",
        role="ACCOUNTADMIN",
        encrypted_password=encrypted,
        capabilities=await resolve_engine_capabilities(),
    )
    binder = RelationBinder(None, stage_schema=service._adapters().stage_schema_provider(context))
    relation = await binder.bind_relation("SELECT * FROM @products.products_new.csv")
    assert isinstance(relation, BoundRelation)
    assert [column.name for column in relation.columns] == ["id", "name", "amount"]
