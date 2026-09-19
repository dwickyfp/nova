"""Automatic role fallback for permission-denied statements.

When the session's active role lacks a privilege but the user holds another
granted role that has it, the query runs once under that role and the result
carries a warning. These tests pin the safety envelope:

* only the connection-opening (HTTP) path falls back — never the proxy;
* destructive statements never fall back;
* non-permission errors never trigger a lookup;
* the session role is not consulted as a fallback candidate;
* the warning names the role actually used.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import StarRocksError, register_exception_handlers
from app.modules.query.repository import QueryResult

EXECUTE_ENDPOINT = "/api/v1/query/execute"
SELECT_SQL = "SELECT 1 AS x"


class RoleAwareEngine:
    """Repository stub: records the role each statement ran under and enforces
    a per-role allowlist, raising an access-denied ``StarRocksError`` otherwise.
    """

    def __init__(self, *, allowed_roles=(), object_database="db_b"):
        self.allowed_roles = set(allowed_roles)
        self.object_database = object_database
        self.roles: list[str | None] = []
        self.calls: list[str] = []

    async def execute_as_user(self, sql, *, role=None, **kwargs):
        self.roles.append(role)
        self.calls.append(sql)
        if role in self.allowed_roles or not self.allowed_roles:
            return QueryResult(columns=["x"], rows=[[1]], row_count=1, executed_sql=sql)
        raise StarRocksError(
            f"SQL error: Access denied; you do not have privilege on "
            f"{self.object_database}.t"
        )


def _client(monkeypatch, *, roles, active_role, engine):
    import app.modules.query.service as service_module
    from app.core import deps as deps_module
    from app.modules.query.service import query_service

    monkeypatch.setattr(query_service, "_repo", engine)
    monkeypatch.setattr(service_module, "decrypt_password", lambda value: "pw")

    async def no_audit(**kwargs):
        return None

    monkeypatch.setattr(service_module, "write_audit_log", no_audit)

    async def fake_current_user():
        return {
            "username": "analyst",
            "session_id": "sess-fallback",
            "roles": roles,
            "active_role": active_role,
            "encrypted_password": "enc",
        }

    app = FastAPI()
    register_exception_handlers(app)
    from app.modules.query.router import router as query_router

    app.include_router(query_router, prefix="/api/v1/query")
    app.dependency_overrides[deps_module.get_current_user] = fake_current_user
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def grant_roles(monkeypatch):
    """Stub the resolver's two catalog reads."""

    def _install(*, applicable: list[str], role_grants: dict[str, list[tuple[str, str]]]):
        import app.modules.query.role_resolver as resolver

        async def fake_applicable(username):
            return sorted(applicable)

        async def fake_privileges(role, database):
            rows = role_grants.get(role, [])
            return [
                privilege.upper()
                for privilege, object_db in rows
                if object_db in ("*", "", database)
            ]

        monkeypatch.setattr(resolver, "_granted_roles", fake_applicable)
        monkeypatch.setattr(resolver, "_role_privileges_on_database", fake_privileges)

    return _install


def test_permission_error_retries_under_fallback_role(monkeypatch, grant_roles):
    engine = RoleAwareEngine(allowed_roles={"ROLE_B"})
    grant_roles(
        applicable=["ROLE_A", "ROLE_B"],
        role_grants={"ROLE_A": [], "ROLE_B": [("INSERT", "db_b")]},
    )
    client = _client(monkeypatch, roles=["ROLE_A", "ROLE_B"], active_role="ROLE_A", engine=engine)

    payload = client.post(
        EXECUTE_ENDPOINT, json={"sql": f"INSERT INTO db_b.t VALUES (1)"}
    ).json()

    assert payload[0]["success"] is True
    assert engine.roles == ["ROLE_A", "ROLE_B"], "must try active role, then fall back"
    assert any("ROLE_B" in w for w in payload[0]["warnings"])


def test_no_fallback_when_active_role_succeeds(monkeypatch, grant_roles):
    engine = RoleAwareEngine(allowed_roles={"ROLE_A"})
    grant_roles(
        applicable=["ROLE_A", "ROLE_B"],
        role_grants={"ROLE_A": [("INSERT", "db_b")], "ROLE_B": [("INSERT", "db_b")]},
    )
    client = _client(monkeypatch, roles=["ROLE_A", "ROLE_B"], active_role="ROLE_A", engine=engine)

    payload = client.post(EXECUTE_ENDPOINT, json={"sql": "INSERT INTO db_b.t VALUES (1)"}).json()

    assert payload[0]["success"] is True
    assert engine.roles == ["ROLE_A"]
    assert payload[0]["warnings"] == []


def test_destructive_statement_never_falls_back(monkeypatch, grant_roles):
    """A destructive statement is refused by the guard before the engine, so it
    can never be auto-retried under a stronger role."""
    engine = RoleAwareEngine(allowed_roles={"ROLE_B"})
    grant_roles(
        applicable=["ROLE_A", "ROLE_B"],
        role_grants={"ROLE_A": [], "ROLE_B": [("DROP", "db_b")]},
    )
    client = _client(monkeypatch, roles=["ROLE_A", "ROLE_B"], active_role="ROLE_A", engine=engine)

    payload = client.post(EXECUTE_ENDPOINT, json={"sql": "DROP TABLE db_b.t"}).json()

    assert payload[0]["success"] is False
    assert engine.roles == [], "a destructive statement must never reach the engine via fallback"


def test_destructive_with_confirmation_is_not_retried(monkeypatch, grant_roles):
    """Even when the caller confirms a destructive statement, the active role's
    refusal is final — no fallback to a role that holds DROP."""
    engine = RoleAwareEngine(allowed_roles={"ROLE_B"})
    grant_roles(
        applicable=["ROLE_A", "ROLE_B"],
        role_grants={"ROLE_A": [], "ROLE_B": [("DROP", "db_b")]},
    )
    client = _client(monkeypatch, roles=["ROLE_A", "ROLE_B"], active_role="ROLE_A", engine=engine)

    payload = client.post(
        EXECUTE_ENDPOINT,
        json={"sql": "DROP TABLE db_b.t", "confirm_destructive": True},
    ).json()

    assert payload[0]["success"] is False
    assert engine.roles == ["ROLE_A"], "a confirmed destructive statement must not be retried"


def test_non_permission_error_never_falls_back(monkeypatch, grant_roles):
    class SyntaxEngine(RoleAwareEngine):
        async def execute_as_user(self, sql, *, role=None, **kwargs):
            self.roles.append(role)
            raise StarRocksError("SQL error: Syntax error near 'FROMM'")

    engine = SyntaxEngine(allowed_roles={"ROLE_B"})
    grant_roles(
        applicable=["ROLE_A", "ROLE_B"],
        role_grants={"ROLE_B": [("INSERT", "db_b")]},
    )
    client = _client(monkeypatch, roles=["ROLE_A", "ROLE_B"], active_role="ROLE_A", engine=engine)

    payload = client.post(EXECUTE_ENDPOINT, json={"sql": "SELECT * FROMM db_b.t"}).json()

    assert payload[0]["success"] is False
    assert engine.roles == ["ROLE_A"]


def test_no_fallback_when_no_role_can_access_object(monkeypatch, grant_roles):
    engine = RoleAwareEngine(allowed_roles={"ROLE_B"})
    grant_roles(
        applicable=["ROLE_A", "ROLE_B"],
        role_grants={"ROLE_A": [], "ROLE_B": []},
    )
    client = _client(monkeypatch, roles=["ROLE_A", "ROLE_B"], active_role="ROLE_A", engine=engine)

    payload = client.post(EXECUTE_ENDPOINT, json={"sql": "INSERT INTO db_b.t VALUES (1)"}).json()

    assert payload[0]["success"] is False
    assert engine.roles == ["ROLE_A"]


def test_least_privilege_role_wins(monkeypatch, grant_roles):
    engine = RoleAwareEngine(allowed_roles={"ROLE_B", "ROLE_C"})
    grant_roles(
        applicable=["ROLE_A", "ROLE_B", "ROLE_C"],
        role_grants={
            "ROLE_A": [],
            "ROLE_B": [("INSERT", "db_b")],
            "ROLE_C": [("INSERT", "db_b"), ("DROP", "db_b"), ("ALTER", "db_b")],
        },
    )
    client = _client(monkeypatch, roles=["ROLE_A", "ROLE_B", "ROLE_C"], active_role="ROLE_A", engine=engine)

    client.post(EXECUTE_ENDPOINT, json={"sql": "INSERT INTO db_b.t VALUES (1)"})

    assert engine.roles == ["ROLE_A", "ROLE_B"], "fewest-privilege role must win"
