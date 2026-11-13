"""Nova Catalog phase 2: connections, JDBC foreign catalogs, DDL, catalog grants.

The engine, ``NOVA_SYSTEM``, and audit are faked. What is checked here is what
Nova itself decides: which statements it builds, what it refuses before any
credential is resolved, and that no response, row, or audit entry carries an
endpoint, a reference, or a credential it should not.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.crypto import decrypt
from app.core.deps import get_current_user
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from app.modules.access_control.statement_router import SecurityStatementRouter
from app.modules.catalog import connections, service
from app.modules.catalog.connections import ConnectionService, validate_secret_ref
from app.modules.catalog.connections_router import router
from app.modules.catalog.errors import CatalogError
from app.modules.catalog.metadata_repository import CatalogMetadataRepository
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.ast.statements import CatalogStatement
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import CatalogPayload, NovaActionPlan
from app.sql_frontend.planning.planner import SQLPlanner
from app.sql_frontend.security import decode_security
from app.storage.secrets import SecretValue, _parse_secret_payload

ADMIN = {
    "username": "nova_admin",
    "encrypted_password": "enc",
    "session_id": "s1",
    "active_role": "ACCOUNTADMIN",
    "assigned_roles": ["ACCOUNTADMIN", "marketing"],
    "security_context_version": 1,
}
ANALYST = {**ADMIN, "username": "alice", "active_role": "marketing"}
PASSWORD = "Pg-s3cr3t\\'pw"


# ── Secret references ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ref",
    [
        "nova://pg_sales",
        "aws://nova/pg_sales",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:nova/pg-AbCdEf",
    ],
)
def test_secret_references_are_accepted(ref):
    assert validate_secret_ref(ref) == ref


@pytest.mark.parametrize(
    "ref", ["admin:hunter2", "hunter2", "nova://bad name", "vault://x", "aws://", ""]
)
def test_values_that_are_not_references_are_refused(ref):
    with pytest.raises(CatalogError):
        validate_secret_ref(ref)


def test_a_database_secret_payload_is_a_username_password_pair():
    value = _parse_secret_payload(
        json.dumps({"username": "reader", "password": "pw"}), provider="aws", reference="r"
    )
    assert value == SecretValue("reader", "pw")


# ── Store ─────────────────────────────────────────────────────────────────


def test_schema_includes_connections_and_encrypted_credentials():
    schema = "\n".join(migration.statement for migration in load_migrations())
    for table in ("catalog_connections", "catalog_connection_credentials"):
        assert f"nova_meta.{table}" in schema
    assert "username_enc" in schema and "password_enc" in schema


async def test_entered_credentials_are_written_encrypted(monkeypatch):
    repo = CatalogMetadataRepository(None, SecurityContext("admin", "ACCOUNTADMIN"))
    monkeypatch.setattr(repo, "_bound", AsyncMock())
    write = AsyncMock(return_value={"metadata_revision": 2})
    monkeypatch.setattr(repo, "_one", write)
    assert await repo.put_credentials("pg", "reader", PASSWORD) == 2
    _, params = write.call_args.args
    assert PASSWORD not in json.dumps(params, default=str)
    assert params[0].startswith("enc:") and params[1].startswith("enc:")
    assert decrypt(params[1]) == PASSWORD


# ── Service ───────────────────────────────────────────────────────────────


class MemoryConnections:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.secrets: dict[str, tuple[str, str]] = {}
        self.write_secrets: dict[str, tuple[str, str]] = {}
        self.reads = 0

    async def list(self):
        return [dict(row) for _, row in sorted(self.rows.items())]

    async def get(self, name):
        return dict(self.rows[name]) if name in self.rows else None

    async def upsert(self, row):
        self.rows[row["name"]] = dict(row)

    async def delete(self, name):
        self.rows.pop(name, None)
        self.secrets.pop(name, None)

    def _store(self, write):
        return self.write_secrets if write else self.secrets

    async def has_credentials(self, name, *, write=False):
        return name in self._store(write)

    async def put_credentials(self, name, username, password, by, *, write=False):
        self._store(write)[name] = (username, password)

    async def delete_write_credentials(self, name):
        self.write_secrets.pop(name, None)

    async def read_credentials(self, name, *, write=False):
        self.reads += 1
        return self._store(write).get(name)


class MemoryCatalogs:
    def __init__(self, rows=None):
        self.rows = {row["name"]: dict(row) for row in rows or []}

    async def get_catalog(self, name):
        return dict(self.rows[name]) if name in self.rows else None

    async def list_catalogs(self, *, include_dropped=False):
        return [dict(r) for r in self.rows.values() if include_dropped or not r.get("dropped_at")]

    async def upsert_catalog(self, row):
        self.rows[row["name"]] = dict(row)


@pytest.fixture
def audits(monkeypatch):
    records: list[dict] = []

    async def write(**kwargs):
        records.append(kwargs)

    monkeypatch.setattr(connections, "write_audit_log", write)
    monkeypatch.setattr(service, "write_audit_log", write)
    return records


@pytest.fixture
def engine(monkeypatch):
    """The caller-side engine call; records the statements Nova sends."""
    run = AsyncMock()
    monkeypatch.setattr(service.catalog_service, "_run_as_caller", run)
    monkeypatch.setattr(service.catalog_service, "_after_change", AsyncMock())
    return run


def _service(catalogs=None):
    return ConnectionService(MemoryConnections(), MemoryCatalogs(catalogs))


async def _managed(svc):
    return await svc.create(
        name="pg_shop", connector_type="postgres", endpoint="pg:5432/shop",
        secret_ref=None, username="reader", password=PASSWORD, comment="Shop", user=ADMIN,
    )


async def test_non_admin_is_refused_and_audited_before_any_credential_is_read(audits, engine):
    svc = _service()
    await _managed(svc)
    with pytest.raises(service.CatalogAccessDenied):
        await svc.create_foreign_catalog(name="pg_x", connection="pg_shop", user=ANALYST)
    for call in (
        svc.list_connections(ANALYST),
        svc.describe("pg_shop", ANALYST),
        svc.drop("pg_shop", ANALYST),
        svc.set_credentials("pg_shop", "u", "p", ANALYST),
        svc.create(name="other", connector_type="starrocks", endpoint="db", secret_ref="aws://x",
                   user=ANALYST),
    ):
        with pytest.raises(service.CatalogAccessDenied):
            await call
    assert svc.repo.reads == 0
    engine.assert_not_awaited()
    denied = [a for a in audits if a["status"] == "DENIED"]
    assert len(denied) == 6 and all(a["user_name"] == "alice" for a in denied)


async def test_managed_connection_never_shows_endpoint_reference_or_credentials(audits):
    svc = _service()
    summary = await _managed(svc)
    assert summary is not None
    payload = summary.model_dump_json()
    for hidden in ("pg:5432", "5432", "nova://", "reader", "s3cr3t", "endpoint"):
        assert hidden not in payload
    assert summary.credential_source == "managed" and summary.has_credentials
    stored = svc.repo.rows["pg_shop"]
    assert stored["secret_ref"] == "nova://pg_shop"
    assert "s3cr3t" not in json.dumps(stored, default=str)
    assert all("s3cr3t" not in json.dumps(a, default=str) for a in audits)
    assert audits[-1]["action"] == "CREATE_CONNECTION" and audits[-1]["status"] == "SUCCESS"


async def test_create_needs_exactly_one_credential_source(audits):
    svc = _service()
    for kwargs in (
        {"secret_ref": None},
        {"secret_ref": "aws://x", "username": "u", "password": "p"},
        {"secret_ref": None, "username": "u"},
        {"secret_ref": "nova://someone_else"},
    ):
        with pytest.raises(CatalogError):
            await svc.create(name="pg", connector_type="postgres", endpoint="pg/shop",
                             user=ADMIN, **kwargs)
    assert svc.repo.rows == {}
    assert audits[-1]["status"] == "ERROR"


async def test_duplicate_connection_is_an_error_unless_if_not_exists(audits):
    svc = _service()
    await _managed(svc)
    with pytest.raises(CatalogError):
        await _managed(svc)
    again = await svc.create(name="pg_shop", connector_type="postgres", endpoint="pg/other",
                             secret_ref="aws://x", user=ADMIN, if_not_exists=True)
    assert again is not None and svc.repo.rows["pg_shop"]["database_name"] == "shop"


async def test_foreign_catalog_runs_create_as_the_caller_and_records_its_connection(
    audits, engine
):
    svc = _service()
    await _managed(svc)
    await svc.create_foreign_catalog(
        name="pg_x", connection="pg_shop", user=ADMIN, comment="Orders",
        properties={"jdbc_meta_default_cache_enable": "false"},
    )
    sql, user = engine.await_args.args
    assert user is ADMIN
    assert "'jdbc_uri' = 'jdbc:postgresql://pg:5432/shop'" in sql
    assert "'user' = 'reader'" in sql and "'password' =" in sql
    row = svc.catalogs.rows["pg_x"]
    assert row["kind"] == "foreign" and row["catalog_type"] == "postgres"
    assert row["properties"]["nova.connection"] == "pg_shop"
    assert "s3cr3t" not in json.dumps(row, default=str)
    assert all("s3cr3t" not in json.dumps(a, default=str) for a in audits)
    summary = await svc.describe("pg_shop", ADMIN)
    assert summary is not None and summary.catalogs == ["pg_x"]


async def test_foreign_catalog_needs_entered_credentials(audits, engine):
    svc = _service()
    await svc.create(name="pg_shop", connector_type="postgres", endpoint="pg/shop",
                     secret_ref="nova://pg_shop", user=ADMIN)
    summary = await svc.describe("pg_shop", ADMIN)
    assert summary is not None and not summary.has_credentials
    with pytest.raises(CatalogError, match="credentials have not been configured"):
        await svc.create_foreign_catalog(name="pg_x", connection="pg_shop", user=ADMIN)
    engine.assert_not_awaited()
    await svc.set_credentials("pg_shop", "reader", PASSWORD, ADMIN)
    await svc.create_foreign_catalog(name="pg_x", connection="pg_shop", user=ADMIN)
    engine.assert_awaited_once()


async def test_external_reference_is_resolved_only_at_create(audits, engine, monkeypatch):
    resolved: list[str] = []

    def resolve(ref):
        resolved.append(ref)
        return SecretValue("reader", PASSWORD)

    monkeypatch.setattr(connections, "resolve_secret_reference", resolve)
    svc = _service()
    await svc.create(name="sr_shop", connector_type="starrocks", endpoint="sr-fe:9031",
                     secret_ref="aws://nova/sr_shop", user=ADMIN)
    assert resolved == []
    await svc.create_foreign_catalog(name="sr_x", connection="sr_shop", user=ADMIN)
    assert resolved == ["aws://nova/sr_shop"]
    assert "'jdbc_uri' = 'jdbc:mariadb://sr-fe:9031'" in engine.await_args.args[0]


async def test_a_connection_of_a_retired_type_cannot_back_a_new_catalog(audits, engine):
    svc = _service()
    await svc.create(name="pg_shop", connector_type="postgres", endpoint="pg/shop",
                     secret_ref=None, username="reader", password=PASSWORD, user=ADMIN)
    svc.repo.rows["pg_shop"]["connector_type"] = "mysql"
    with pytest.raises(CatalogError, match="can no longer back a catalog"):
        await svc.create_foreign_catalog(name="my_x", connection="pg_shop", user=ADMIN)
    engine.assert_not_awaited()


async def test_engine_refusal_is_audited_and_nothing_is_recorded(audits, engine):
    engine.side_effect = CatalogError("Access denied; you need CREATE EXTERNAL CATALOG")
    svc = _service()
    await _managed(svc)
    with pytest.raises(CatalogError):
        await svc.create_foreign_catalog(name="pg_x", connection="pg_shop", user=ADMIN)
    assert "pg_x" not in svc.catalogs.rows
    assert audits[-1]["action"] == "CREATE_FOREIGN_CATALOG"
    assert audits[-1]["status"] == "ERROR"


async def test_a_connection_in_use_cannot_be_dropped(audits, engine):
    svc = _service()
    await _managed(svc)
    await svc.create_foreign_catalog(name="pg_x", connection="pg_shop", user=ADMIN)
    with pytest.raises(CatalogError, match="pg_x"):
        await svc.drop("pg_shop", ADMIN)
    svc.catalogs.rows["pg_x"]["dropped_at"] = "now"
    assert await svc.drop("pg_shop", ADMIN) is True
    assert svc.repo.rows == {} and svc.repo.secrets == {}
    assert await svc.drop("pg_shop", ADMIN, if_exists=True) is False


async def test_refresh_of_a_table_refreshes_the_engine_cache_then_syncs(
    audits, engine, monkeypatch
):
    refresh = AsyncMock(return_value=[])
    monkeypatch.setattr(service.catalog_service, "refresh", refresh)
    svc = _service([{"name": "pg_x", "kind": "foreign"}])
    await svc.refresh_catalog("pg_x", ADMIN, schema="public", relation="orders")
    assert engine.await_args.args[0] == "REFRESH EXTERNAL TABLE `pg_x`.`public`.`orders`"
    refresh.assert_awaited_once_with("pg_x", ADMIN)
    engine.reset_mock()
    await svc.refresh_catalog("pg_x", ADMIN)
    engine.assert_not_awaited()


# ── SQL frontend ──────────────────────────────────────────────────────────


async def _plan(sql):
    statement = ast_builders.build(parse_statement(sql))
    return statement, await SQLPlanner().plan(statement, PlanningContext())


async def test_create_connection_plans_a_credential_free_action():
    statement, plan = await _plan(
        "CREATE CONNECTION IF NOT EXISTS pg_shop TYPE = postgres "
        "ENDPOINT = 'pg:5432/shop' SECRET = 'aws://nova/pg' COMMENT = 'Shop'"
    )
    assert isinstance(statement, CatalogStatement)
    assert isinstance(plan, NovaActionPlan)
    payload = plan.payload
    assert isinstance(payload, CatalogPayload)
    assert (payload.operation, payload.name, payload.connector_type) == (
        "create_connection", "pg_shop", "postgres"
    )
    assert payload.secret_ref == "aws://nova/pg" and payload.if_not_exists
    assert plan.effects.writes_metadata and not plan.requires_confirmation


async def test_foreign_catalog_refresh_and_listing_statements_plan():
    _, plan = await _plan(
        "CREATE FOREIGN CATALOG pg_x USING CONNECTION pg_shop COMMENT 'Orders' "
        "PROPERTIES ('jdbc_meta_default_cache_enable' = 'false')"
    )
    assert plan.payload.operation == "create_foreign_catalog"
    assert plan.payload.connection == "pg_shop" and plan.payload.comment == "Orders"
    assert plan.payload.properties == (("jdbc_meta_default_cache_enable", "false"),)
    assert plan.effects.changes_schema and plan.effects.external_io

    _, plan = await _plan("REFRESH CATALOG pg_x SCHEMA public TABLE orders")
    assert (plan.payload.name, plan.payload.schema, plan.payload.relation) == (
        "pg_x", "public", "orders"
    )
    _, plan = await _plan("SHOW CONNECTIONS")
    assert plan.payload.operation == "show_connections" and plan.effects.reads_data
    _, plan = await _plan("DESCRIBE CONNECTION pg_shop")
    assert (plan.payload.operation, plan.payload.name) == ("describe_connection", "pg_shop")
    _, plan = await _plan("DROP CONNECTION IF EXISTS pg_shop")
    assert plan.payload.if_exists and plan.requires_confirmation


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE CONNECTION c TYPE = postgres ENDPOINT = 'pg/db' SECRET = 'admin:hunter2'",
        "CREATE CONNECTION c TYPE = postgres ENDPOINT = 'u:p@pg/db' SECRET = 'aws://x'",
        "CREATE CONNECTION c TYPE = oracle ENDPOINT = 'pg/db' SECRET = 'aws://x'",
        "CREATE CONNECTION c TYPE = postgres ENDPOINT = 'pg/db'",
        "CREATE CONNECTION c TYPE = postgres ENDPOINT = 'pg/db' SECRET = 'aws://x' "
        "PASSWORD = 'p'",
        "CREATE CONNECTION c TYPE = postgres TYPE = mysql ENDPOINT = 'pg/db' SECRET = 'aws://x'",
        "CREATE FOREIGN CATALOG x USING CONNECTION c PROPERTIES ('password' = 'p')",
        "CREATE FOREIGN CATALOG x USING CONNECTION c PROPERTIES ('aws.s3.secret_key' = 'p')",
    ],
)
async def test_invalid_catalog_ddl_is_refused_before_planning(sql):
    with pytest.raises(SemanticError):
        await _plan(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE FOREIGN CATALOG x USING c",
        "REFRESH CATALOG x TABLE t",
        "CREATE CONNECTION c",
        "CREATE FOREIGN CATALOG x USING CONNECTION c METADATA ONLY",
    ],
)
def test_malformed_catalog_ddl_does_not_parse(sql):
    from app.sql_frontend.errors import SQLSyntaxError

    with pytest.raises(SQLSyntaxError):
        parse_statement(sql)


def test_foreign_and_connection_stay_usable_as_identifiers():
    for sql in ("SELECT foreign FROM t", "SELECT connection FROM connections"):
        assert type(parse_statement(sql).statement_context).__name__ == "QueryStatementContext"


async def test_executor_hands_the_payload_to_the_connection_service(monkeypatch):
    from app.sql_frontend.context import ExecutionContext
    from app.sql_frontend.execution.adapters import FeatureAdapters

    statement, plan = await _plan(
        "CREATE FOREIGN CATALOG pg_x USING CONNECTION pg_shop"
    )
    create = AsyncMock(return_value="pg_x")
    monkeypatch.setattr(connections.connection_service, "create_foreign_catalog", create)
    adapters = FeatureAdapters.__new__(FeatureAdapters)
    context = ExecutionContext(username="nova_admin", role="ACCOUNTADMIN", session_id="s1")
    context.statements[0] = statement
    result = await adapters.catalog(plan, context)
    assert result.error is None and result.rows == [["Catalog pg_x created"]]
    kwargs = create.await_args.kwargs
    assert kwargs["name"] == "pg_x" and kwargs["connection"] == "pg_shop"
    assert kwargs["user"]["security"].active_role == "ACCOUNTADMIN"

    create.side_effect = service.CatalogAccessDenied("not authorized")
    result = await adapters.catalog(plan, context)
    assert result.error == "not authorized"


# ── Catalog-aware GRANT/REVOKE ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("GRANT SELECT ON TABLE pg_x.public.orders TO ROLE analyst",
         ("grant_access", ("analyst", "public", "orders", "SELECT", "pg_x"))),
        ("REVOKE SELECT ON pg_x.public.orders FROM ROLE analyst",
         ("revoke_access", ("analyst", "public", "orders", "SELECT", "pg_x"))),
        ("GRANT SELECT ON TABLE analytics.sales TO ROLE analyst",
         ("grant_access", ("analyst", "analytics", "sales", "SELECT", "default_catalog"))),
    ],
)
def test_grants_name_their_catalog(sql, expected):
    operation = decode_security(parse_statement(sql))
    assert operation is not None
    assert (operation.kind, operation.identifiers) == expected


@pytest.mark.parametrize(
    "sql",
    ["GRANT SELECT ON TABLE pg_x.*.orders TO ROLE analyst",
     "GRANT SELECT ON TABLE pg_x.public.* TO ROLE analyst"],
)
def test_wildcard_catalog_grants_fail_closed(sql):
    from app.modules.access_control.service import AccessControlError

    with pytest.raises(AccessControlError):
        decode_security(parse_statement(sql))


async def test_sql_grant_compiles_a_policy_for_the_named_catalog(monkeypatch):
    from app.modules.access_control.security_context import require_security_context
    from app.sql_frontend import security as security_module

    grant = AsyncMock(return_value={})
    monkeypatch.setattr(security_module.access_control_service, "grant_access", grant)
    operation = decode_security(
        parse_statement("GRANT SELECT ON TABLE pg_x.public.orders TO ROLE analyst")
    )
    assert operation is not None
    await security_module.execute_security_operation(
        operation, require_security_context(principal="admin", active_role="ACCOUNTADMIN")
    )
    assert grant.await_args.kwargs["catalog"] == "pg_x"
    assert grant.await_args.kwargs["database"] == "public"


async def test_legacy_router_also_accepts_the_catalog(monkeypatch):
    from app.modules.access_control.security_context import require_security_context

    service_mock = AsyncMock()
    router_ = SecurityStatementRouter(service_mock)
    security = require_security_context(principal="admin", active_role="ACCOUNTADMIN")
    await router_.route(
        "GRANT SELECT ON TABLE pg_x.public.orders TO ROLE analyst", security=security
    )
    assert service_mock.grant_access.await_args.kwargs["catalog"] == "pg_x"
    await router_.route("REVOKE SELECT ON analytics.sales FROM ROLE analyst", security=security)
    assert service_mock.revoke_access.await_args.kwargs["catalog"] == "default_catalog"


def test_ranger_policy_for_a_foreign_table_targets_that_catalog_only():
    from app.integrations.ranger.compiler import compile_access_policy

    policy = compile_access_policy(
        role="analyst", catalog="pg_x", database="public", table="orders", accesses=["select"]
    )
    assert policy.resources["catalog"].values == ["pg_x"]
    assert policy.name.endswith("/access/analyst/pg_x/public/orders")


# ── REST ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/catalog")
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    return TestClient(app)


def test_rest_create_takes_write_only_credentials_and_returns_no_secret(client, monkeypatch):
    svc = _service()
    monkeypatch.setattr(connections, "write_audit_log", AsyncMock())
    monkeypatch.setattr(
        "app.modules.catalog.connections_router.connection_service", svc
    )
    response = client.post(
        "/api/v1/catalog/connections",
        json={
            "name": "pg_shop", "connector_type": "postgres", "endpoint": "pg:5432/shop",
            "credentials": {"username": "reader", "password": PASSWORD},
        },
    )
    assert response.status_code == 201, response.text
    body = response.text
    for hidden in ("s3cr3t", "reader", "pg:5432", "nova://", "endpoint"):
        assert hidden not in body
    listed = client.get("/api/v1/catalog/connections").json()
    assert listed["count"] == 1 and listed["connections"][0]["credential_source"] == "managed"

    response = client.put(
        "/api/v1/catalog/connections/pg_shop/credentials",
        json={"username": "reader2", "password": "other"},
    )
    assert response.status_code == 200 and "other" not in response.text
    assert client.get("/api/v1/catalog/connections/nope").status_code == 404
    assert client.delete("/api/v1/catalog/connections/pg_shop").status_code == 200


def test_rest_maps_refusals_to_403_and_bad_input_to_400(client, monkeypatch):
    svc = _service()
    monkeypatch.setattr(connections, "write_audit_log", AsyncMock())
    monkeypatch.setattr("app.modules.catalog.connections_router.connection_service", svc)
    bad = client.post(
        "/api/v1/catalog/connections",
        json={"name": "c", "connector_type": "postgres", "endpoint": "u:p@pg/db",
              "secret_ref": "aws://x"},
    )
    assert bad.status_code == 400
    assert client.post(
        "/api/v1/catalog/foreign-catalogs",
        json={"name": "x", "connection": "c", "properties": {"password": "p"}},
    ).status_code == 422
    client.app.dependency_overrides[get_current_user] = lambda: ANALYST
    assert client.get("/api/v1/catalog/connections").status_code == 403
    assert client.post(
        "/api/v1/catalog/foreign-catalogs", json={"name": "x", "connection": "c"}
    ).status_code == 403


# ── Unmodified consumers: QueryService and the MySQL proxy ────────────────


@pytest.mark.parametrize("proxy", [False, True])
@pytest.mark.parametrize(
    ("sql", "method"),
    [
        ("CREATE CONNECTION pg_shop TYPE = postgres ENDPOINT = 'pg:5432/shop' "
         "SECRET = 'aws://nova/pg'", "create"),
        ("CREATE FOREIGN CATALOG pg_x USING CONNECTION pg_shop", "create_foreign_catalog"),
        ("REFRESH CATALOG pg_x SCHEMA public TABLE orders", "refresh_catalog"),
        ("SHOW CONNECTIONS", "list_connections"),
    ],
)
async def test_catalog_ddl_runs_through_query_service_and_proxy(monkeypatch, proxy, sql, method):
    from app.modules.query.service import QueryService, query_service
    from app.proxy.executor import ProxyQueryExecutor
    from app.proxy.session import SessionState
    from app.sql_frontend.capabilities.starrocks import EngineCapabilities

    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "pw")
    called = AsyncMock(return_value=[])
    monkeypatch.setattr(connections.connection_service, method, called)
    repository = AsyncMock()
    if proxy:
        monkeypatch.setattr(query_service, "_repo", repository)
        monkeypatch.setattr(
            query_service, "_capability_resolver", AsyncMock(return_value=EngineCapabilities())
        )
        result = await ProxyQueryExecutor(
            SessionState(database="analytics", active_role="ACCOUNTADMIN"),
        ).execute(sql, username="nova_admin", connection=object())
    else:
        svc = QueryService(capability_resolver=AsyncMock(return_value=EngineCapabilities()))
        svc._repo = repository
        result = await svc.execute(sql, "nova_admin", "enc", role="ACCOUNTADMIN")
    assert result.error is None, result.error
    called.assert_awaited_once()
    # Nova statements never reach the engine as typed.
    repository.execute_as_user.assert_not_awaited()


async def test_a_write_credential_is_kept_apart_and_never_shown(audits):
    svc = _service()
    await _managed(svc)
    summary = await svc.set_write_credentials("pg_shop", "writer", "w-" + PASSWORD, ADMIN)
    assert summary is not None and summary.writable
    assert "w-" + PASSWORD not in summary.model_dump_json()
    assert svc.repo.write_secrets["pg_shop"] == ("writer", "w-" + PASSWORD)
    assert svc.repo.secrets["pg_shop"][0] != "writer"  # the read credential is untouched
    row = await svc.repo.get("pg_shop")
    assert (await svc.write_credentials(row)).username == "writer"
    cleared = await svc.set_write_credentials("pg_shop", None, None, ADMIN)
    assert cleared is not None and not cleared.writable


async def test_only_an_admin_sets_a_write_credential_and_only_where_writes_exist(audits):
    svc = _service()
    await _managed(svc)
    with pytest.raises(service.CatalogAccessDenied):
        await svc.set_write_credentials("pg_shop", "w", "p", ANALYST)
    await svc.create(name="sr_shop", connector_type="starrocks", endpoint="sr-fe:9031",
                     secret_ref="aws://nova/sr_shop", user=ADMIN)
    with pytest.raises(CatalogError, match="do not take a write credential"):
        await svc.set_write_credentials("sr_shop", "w", "p", ADMIN)
    with pytest.raises(CatalogError, match="both"):
        await svc.set_write_credentials("pg_shop", "w", None, ADMIN)
