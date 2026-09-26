from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.assistant.registry import build_registry
from app.modules.assistant.service import _planning_history
from app.modules.assistant.state import AssistantMessage, AssistantThread
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.provision_user import ProvisionUserTool
from app.modules.assistant.tools.query_mutate import QueryMutateTool, validated_write_sql
from app.modules.assistant.tools.search_knowledge import search_references
from app.modules.query.repository import QueryResult


def context(role="ACCOUNTADMIN"):
    return SimpleNamespace(user={"username": "audit_admin", "active_role": role,
                               "assigned_roles": [role], "encrypted_password": "encrypted"},
                           secure_input={"password": "test-only-secret"},
                           audit_session_id="audit-session", database="analytics", schema_name=None)


def test_default_registry_has_no_api_bridge():
    from app.modules.agents.tool_catalog import BUILTIN_TOOLS

    registry = build_registry()
    assert {"query_mutate", "provision_user"} <= set(registry.names())
    assert not {"call_ui_operation", "list_ui_operations", "find_ui_operation"} & set(registry.names())
    assert not {"call_ui_operation", "list_ui_operations"} & set(BUILTIN_TOOLS)


@pytest.mark.parametrize("sql", ["DROP ROLE ACCOUNTADMIN", "REVOKE SELECT ON a.b FROM ROLE ACCOUNTADMIN",
                                 "CREATE USER 'alice'", "CREATE USER 'alice' IDENTIFIED BY 'secret'",
                                 "SELECT 1; DROP USER root", "SET ROLE analyst", "COPY INTO @s.x.csv FROM t"])
def test_write_tool_rejects_protected_secret_and_unsupported_sql(sql):
    with pytest.raises(Exception):
        validated_write_sql({"sql": sql})


async def test_write_calls_query_service_under_caller_identity(monkeypatch):
    from app.modules.assistant.tools import query_mutate as module
    from app.modules.query.service import query_service

    execute = AsyncMock(return_value=[QueryResult(affected_rows=1)])
    monkeypatch.setattr(query_service, "execute_statements", execute)
    monkeypatch.setattr(module, "write_audit_log", AsyncMock())
    invocation = ToolInvocation("w", "query_mutate", {"sql": "INSERT INTO analytics.t VALUES (1)"})
    result = await QueryMutateTool().run(invocation, context())
    assert result.ok
    assert execute.call_args.kwargs["username"] == "audit_admin"
    assert execute.call_args.kwargs["role"] == "ACCOUNTADMIN"
    assert execute.call_args.kwargs["confirm_destructive"] is True
    assert QueryMutateTool().classification_for(invocation) == "destructive"


async def test_write_reports_partial_completion_without_retry(monkeypatch):
    from app.modules.assistant.tools import query_mutate as module
    from app.modules.query.service import query_service

    execute = AsyncMock(return_value=[QueryResult(affected_rows=1), QueryResult(error="Unknown column")])
    monkeypatch.setattr(query_service, "execute_statements", execute)
    monkeypatch.setattr(module, "write_audit_log", AsyncMock())
    result = await QueryMutateTool().run(ToolInvocation("w", "query_mutate", {"sql": "INSERT INTO a VALUES(1); UPDATE a SET b=2"}), context())
    assert not result.ok and "1 statements completed" in result.error
    assert execute.await_count == 1


async def test_provision_protected_input_and_default_password_change(monkeypatch):
    from app.modules.assistant.tools import provision_user as module

    monkeypatch.setattr(module.settings, "RANGER_ENABLED", False)
    service = SimpleNamespace(list_roles=AsyncMock(return_value=[{"name": "ACCOUNTADMIN"}]),
                              user_exists=AsyncMock(return_value=False), create_user=AsyncMock(),
                              assign_role=AsyncMock(), set_default_roles=AsyncMock(), drop_user=AsyncMock())
    monkeypatch.setattr(module, "user_service", service)
    flag = AsyncMock()
    monkeypatch.setattr(module, "set_must_change_password", flag)
    audit = AsyncMock()
    monkeypatch.setattr(module, "write_audit_log", audit)
    ctx = context()
    invocation = ToolInvocation("p", "provision_user", {"username": "dwicky.f.putra", "role": "ACCOUNTADMIN"})
    result = await ProvisionUserTool().run(invocation, ctx)
    assert result.ok
    flag.assert_awaited_once_with("dwicky.f.putra", required=True)
    service.set_default_roles.assert_awaited_once_with("dwicky.f.putra", "%", "explicit", ["ACCOUNTADMIN"])
    assert ctx.secure_input is None
    assert "test-only-secret" not in repr(result) + repr(audit.call_args_list) + ProvisionUserTool().preview(invocation)


async def test_provision_denies_non_admin_before_services(monkeypatch):
    from app.modules.assistant.tools import provision_user as module

    create = AsyncMock()
    monkeypatch.setattr(module.user_service, "create_user", create)
    result = await ProvisionUserTool().run(ToolInvocation("p", "provision_user", {"username": "alice", "role": "analyst"}), context("analyst"))
    assert not result.ok and result.error_class == "AUTHORIZATION_DENIED"
    create.assert_not_awaited()


@pytest.mark.parametrize("topic", ["sql-select", "sql-tables", "sql-views-catalog", "sql-operations", "sql-extensions"])
def test_exact_sql_topics_are_retrievable(topic):
    refs = search_references(topic)
    assert refs[0]["source"] == f"knowledge:{topic}"
    assert refs[0]["truncated"] == "false"


def test_planning_context_is_bounded_and_new_threads_are_empty():
    first = AssistantThread("one", "user", "one", messages=[AssistantMessage(str(i), "user", "x"*2000) for i in range(20)])
    assert len(_planning_history(first, "current")) == 6
    assert all(len(x["content"]) <= 1000 for x in _planning_history(first, "current"))
    assert _planning_history(AssistantThread("two", "user", "two"), "user tadi") == []


async def test_sql_batch_stops_on_returned_error(monkeypatch):
    from app.modules.query.service import QueryService

    service = QueryService()
    execute = AsyncMock(side_effect=[QueryResult(error="Invalid first statement"), QueryResult(affected_rows=1)])
    monkeypatch.setattr(service, "execute", execute)
    results = await service.execute_statements("SELECT missing; DROP TABLE t", "u", "encrypted")
    assert len(results) == 1 and execute.await_count == 1


@pytest.mark.parametrize("sql", [
    "CREATE USER 'audit.user' IDENTIFIED BY '<temporary_password>'; ALTER USER 'audit.user' REQUIRE PASSWORD CHANGE; GRANT ACCOUNTADMIN TO USER 'audit.user'; SET DEFAULT ROLE ACCOUNTADMIN TO 'audit.user'",
    "CREATE TABLE a.accounts (id BIGINT NOT NULL, name VARCHAR(100)) PRIMARY KEY(id) DISTRIBUTED BY HASH(id)",
    "CREATE TABLE a.logs (id BIGINT, t DATETIME) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id)",
    "CREATE TABLE a.totals (d DATE, revenue DECIMAL(18,2) SUM) AGGREGATE KEY(d) DISTRIBUTED BY HASH(d)",
    "CREATE TABLE a.copy AS SELECT * FROM a.orders",
    "CREATE TABLE a.empty LIKE a.orders",
    "INSERT INTO a.accounts(id,name) VALUES(1,'test')",
    "UPDATE a.accounts SET name='new' WHERE id=1",
    "DELETE FROM a.accounts WHERE id=1",
    "ALTER TABLE a.accounts ADD COLUMN status VARCHAR(20)",
    "TRUNCATE TABLE a.accounts; DROP TABLE IF EXISTS a.accounts",
    "SELECT c.id,COALESCE(SUM(o.amount),0) FROM a.customers c LEFT JOIN a.orders o ON c.id=o.customer_id GROUP BY c.id",
    "WITH t AS (SELECT id,SUM(amount) AS total FROM a.orders GROUP BY id) SELECT * FROM t WHERE total>100",
    "SELECT * FROM (SELECT id,ROW_NUMBER() OVER(PARTITION BY customer_id ORDER BY amount DESC,id) AS rn FROM a.orders) t WHERE rn<=3",
    "SELECT DATE_TRUNC('month',d),SUM(amount) FROM a.orders WHERE d>='2025-01-01' AND d<'2026-01-01' GROUP BY 1",
    "SELECT GET_JSON_STRING(payload,'$.city'),SUM(profit)/NULLIF(SUM(revenue),0) FROM a.sales GROUP BY 1",
    "CREATE VIEW a.v AS SELECT customer_id,SUM(amount) FROM a.orders GROUP BY customer_id",
    "CREATE MATERIALIZED VIEW a.mv DISTRIBUTED BY HASH(customer_id) REFRESH MANUAL AS SELECT customer_id,SUM(amount) FROM a.orders GROUP BY customer_id",
    "REFRESH MATERIALIZED VIEW a.mv WITH SYNC MODE",
    "CREATE DATABASE IF NOT EXISTS audit_db; SHOW DATABASES; SHOW TABLES FROM audit_db; SHOW CATALOGS",
    "SHOW GRANTS FOR 'audit.user'; SHOW ROLES; SHOW PROCESSLIST; SHOW VARIABLES LIKE 'query_timeout'",
    "EXPLAIN SELECT * FROM a.orders; DESCRIBE a.orders; SHOW CREATE TABLE a.orders",
    "ANALYZE TABLE a.orders; SET query_timeout=60",
    "LIST @stage1/; LIST FILES @stage1/",
    "SELECT * FROM @stage1.orders.csv LIMIT 5",
    "COPY INTO a.orders FROM @stage1.orders.csv",
    "INSERT INTO a.orders SELECT * FROM @stage1.orders.csv",
    "COPY INTO @stage1.orders.parquet FROM a.orders",
    "CREATE TASK daily SCHEDULE = 'USING CRON 0 3 * * *' AS INSERT INTO a.copy SELECT * FROM a.orders",
    "CREATE ML_MODEL churn TYPE=CLASSIFICATION TARGET=label AS SELECT age,balance,label FROM a.customers",
    "CREATE ML_MODEL forecast TYPE=FORECAST TARGET=revenue TIMESTAMP=d HORIZON=30 AS SELECT d,revenue FROM a.sales",
    "SELECT id,ML_PREDICT('production_churn',age,balance) FROM a.customers",
    "SELECT * FROM ML_FORECAST(MODEL => 'production_forecast', HORIZON => 30)",
    "SELECT AI_SUMMARIZE(review_text),AI_SENTIMENT(review_text),AI_TRANSLATE(review_text,'Indonesian') FROM a.reviews LIMIT 5",
])
def test_sql_family_corpus_is_valid_without_runtime_access(sql):
    from app.modules.assistant.tools.validate_sql import check_sql

    assert all(check["valid"] for check in check_sql(sql)), check_sql(sql)


@pytest.mark.parametrize("sql", [
    "GRANT ROLE analyst TO USER 'alice'", "SELECT FROM", "SELECT * FROM t WHERE",
    "CREATE ML_MODEL x TYPE=FORECAST AS SELECT * FROM t",
    "CREATE SEMANTIC VIEW sales", "CREATE STAGE stage1", "CREATE WAREHOUSE compute",
    "COPY INTO a.orders FROM @stage1.orders.csv ON_ERROR='continue'",
    "SELECT * FROM ML_FORECAST(MODEL => 'x', HORIZON => -2)",
])
def test_invalid_or_unimplemented_drafts_fail_validation(sql):
    from app.modules.assistant.tools.validate_sql import check_sql

    assert any(not check["valid"] for check in check_sql(sql))


def test_packaged_grammar_is_searchable_including_uncommon_admin_sql():
    from app.modules.assistant.sql_reference import grammar_rules
    from app.sql_dialect.grammar import StarRocksParser

    rules = grammar_rules()
    assert set(rules) == set(StarRocksParser.ruleNames)
    assert "withClause" not in rules["queryStatement"]
    assert "TRUNCATE PLAN ADVISOR" in rules["truncatePlanAdvisorStatement"]
    for query, expected in [("createTableStatement", "createTableStatement"),
                            ("CREATE RESOURCE GROUP", "createResourceGroupStatement"),
                            ("CREATE USER", "createUserStatement")]:
        refs = search_references(f"syntax:{query}")
        assert refs[0]["source"] == f"syntax:{expected}"
        assert all(len(r["text"]) <= 3500 for r in refs)


async def test_password_input_cleared_on_validation_failure(monkeypatch):
    ctx = context("analyst")
    secret = ctx.secure_input
    outcome = await ProvisionUserTool().run(ToolInvocation("p", "provision_user", {"username": "alice", "role": "analyst"}), ctx)
    assert not outcome.ok and secret == {} and ctx.secure_input is None


@pytest.mark.parametrize("role,target,exists", [("analyst", "alice", True),
                                               ("ACCOUNTADMIN", "root", True),
                                               ("ACCOUNTADMIN", "missing", False)])
async def test_password_flag_cannot_bypass_admin_or_user_checks(monkeypatch, role, target, exists):
    from app.modules.query import service as module
    from app.modules.users.service import user_service

    flag, audit = AsyncMock(), AsyncMock()
    monkeypatch.setattr(module, "set_must_change_password", flag)
    monkeypatch.setattr(module, "write_audit_log", audit)
    monkeypatch.setattr(user_service, "user_exists", AsyncMock(return_value=exists))
    result = await module.QueryService().execute(sql=f"ALTER USER '{target}' REQUIRE PASSWORD CHANGE",
                                                username="caller", encrypted_password="encrypted", role=role)
    assert result.error
    flag.assert_not_awaited()
    assert audit.call_args.kwargs["status"] == "ERROR"


def test_draft_plan_rejects_mutation_capability():
    from app.modules.assistant.planning import validate_turn_plan, TurnPlanningError

    with pytest.raises(TurnPlanningError, match="SQL authoring"):
        validate_turn_plan({"intent": "sql_authoring", "tools": ["query_mutate"],
                            "required_tools": [], "skills": []}, {"query_mutate"})


def test_protected_sql_binding_masks_request_and_adds_password_policy():
    from app.modules.query.router import QueryRequest, _bind_temporary_password

    req = QueryRequest(sql="CREATE USER 'audit.user' IDENTIFIED BY '<temporary_password>'",
                       temporary_password="private'p\\x")
    assert "private" not in repr(req) + req.model_dump_json()
    sql = _bind_temporary_password(req)
    assert "IDENTIFIED BY 'private\\'p\\\\x'" in sql
    assert "ALTER USER 'audit.user' REQUIRE PASSWORD CHANGE" in sql


@pytest.mark.parametrize("sql", ["SELECT '<temporary_password>'",
                                 "CREATE USER 'a' IDENTIFIED BY '<temporary_password>'; SELECT '<temporary_password>'",
                                 "SELECT 1"])
def test_protected_input_cannot_be_used_to_echo_password(sql):
    from fastapi import HTTPException
    from app.modules.query.router import QueryRequest, _bind_temporary_password

    with pytest.raises(HTTPException):
        _bind_temporary_password(QueryRequest(sql=sql, temporary_password="private-marker"))


@pytest.mark.parametrize("sql", [
    "CREATE USER 'alice' IDENTIFIED BY 'private-marker'",
    "ALTER USER 'alice' IDENTIFIED BY 'private-marker'",
    "SET PASSWORD FOR 'alice' = PASSWORD('private-marker')",
    "CREATE USER 'alice' IDENTIFIED WITH mysql_native_password BY 'private-marker'",
    "CREATE USER 'alice' IDENTIFIED BY 'private\\';marker'",
    'CREATE USER alice IDENTIFIED BY "private-marker"',
    "CREATE USER alice IDENTIFIED /* credential */ BY 'private-marker'",
    "ALTER USER alice IDENTIFIED -- credential\n BY 'private-marker'",
    "SET /* account */ PASSWORD FOR 'alice' = PASSWORD(/* input */ 'private-marker')",
])
def test_account_credentials_redacted_in_responses_and_audit(sql):
    from app.common.sql_guard import redact_sql_credentials
    from app.common.responses import SanitizingJSONResponse

    redacted = redact_sql_credentials(sql)
    assert "private" not in redacted
    assert "***" in redacted
    assert redact_sql_credentials(redacted) == redacted
    assert b"private" not in SanitizingJSONResponse({"sql": sql}).body


def test_password_with_escaped_quote_and_semicolon_remains_one_statement():
    from app.common.sql_guard import split_sql_statements
    from app.modules.query.router import QueryRequest, _bind_temporary_password

    sql = _bind_temporary_password(QueryRequest(
        sql="CREATE USER 'audit.user' IDENTIFIED BY '<temporary_password>'",
        temporary_password="private';not a statement\\suffix",
    ))
    statements = split_sql_statements(sql)
    assert len(statements) == 2
    assert statements[1] == "ALTER USER 'audit.user' REQUIRE PASSWORD CHANGE"
