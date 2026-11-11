"""The session's active role is the single source of truth for SQL execution.

The bottom-left account switcher (``POST /auth/switch-role``) sets a session
``active_role``; the workspace used to ship a *second*, per-tab role picker that
was sent in the request body and forwarded straight to ``SET ROLE``. The two
could diverge: the UI showed one role as active while the engine ran the
statement under another, and a body ``role`` was never checked against the roles
the user had actually been granted.

These tests pin the fix at the router boundary:

* the role handed to the engine is ``user["active_role"]``, never the body;
* a body ``role`` cannot elevate (it is ignored entirely);
* a session with no ``active_role`` fails closed;
* a forged ``active_role`` that is not in the granted set fails closed.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import register_exception_handlers

EXECUTE_ENDPOINT = "/api/v1/query/execute"
SELECT_SQL = "SELECT 1 AS x"


class RecordingEngine:
    """Repository stub that records the ``role`` each statement executes with."""

    def __init__(self):
        self.roles: list[str | None] = []

    async def execute_as_user(self, sql, *, role=None, **kwargs):
        self.roles.append(role)
        from app.modules.query.repository import QueryResult

        return QueryResult(columns=["x"], rows=[[1]], row_count=1, executed_sql=sql)


def _client(monkeypatch, *, roles, active_role):
    import app.modules.query.service as service_module
    from app.core import deps as deps_module
    from app.modules.query.service import query_service

    engine = RecordingEngine()

    monkeypatch.setattr(query_service, "_repo", engine)
    monkeypatch.setattr(service_module, "decrypt_password", lambda value: "pw")

    async def no_audit(**kwargs):
        return None

    monkeypatch.setattr(service_module, "write_audit_log", no_audit)

    async def fake_current_user():
        return {
            "username": "analyst",
            "session_id": "sess-active-role",
            "roles": roles,
            "active_role": active_role,
            "encrypted_password": "enc",
        }

    app = FastAPI()
    register_exception_handlers(app)
    from app.modules.query.router import router as query_router

    app.include_router(query_router, prefix="/api/v1/query")
    app.dependency_overrides[deps_module.get_current_user] = fake_current_user
    return TestClient(app, raise_server_exceptions=False), engine


@pytest.fixture
def make_client(monkeypatch):
    def _make(*, roles, active_role):
        return _client(monkeypatch, roles=roles, active_role=active_role)

    return _make


def test_engine_runs_under_session_active_role(make_client):
    client, engine = make_client(roles=["analyst", "ACCOUNTADMIN"], active_role="analyst")

    client.post(EXECUTE_ENDPOINT, json={"sql": SELECT_SQL})

    assert engine.roles == ["analyst"]


def test_body_role_cannot_override_session_role(make_client):
    """A request body claiming a stronger role must not reach the engine."""
    client, engine = make_client(roles=["analyst"], active_role="analyst")

    client.post(EXECUTE_ENDPOINT, json={"sql": SELECT_SQL, "role": "ACCOUNTADMIN"})

    assert engine.roles == ["analyst"], "the body role overrode the session role"


def test_body_role_cannot_request_an_ungranted_role(make_client):
    """Even a bare-identifier body role that was never granted is ignored."""
    client, engine = make_client(roles=["analyst"], active_role="analyst")

    client.post(EXECUTE_ENDPOINT, json={"sql": SELECT_SQL, "role": "SUPER_SECRET"})

    assert engine.roles == ["analyst"]


def test_missing_active_role_fails_closed(make_client, monkeypatch):

    client, engine = make_client(roles=["analyst", "ACCOUNTADMIN"], active_role=None)

    response = client.post(EXECUTE_ENDPOINT, json={"sql": SELECT_SQL})

    assert response.status_code == 403
    assert engine.roles == []


def test_forged_active_role_not_in_grants_fails_closed(make_client, monkeypatch):
    """A corrupted session cannot choose another granted role by ordering."""

    client, engine = make_client(roles=["analyst"], active_role="ACCOUNTADMIN")

    response = client.post(EXECUTE_ENDPOINT, json={"sql": SELECT_SQL})

    assert response.status_code == 403
    assert engine.roles == []


def test_use_role_inside_a_script_is_refused_before_anything_runs(make_client):
    """``USE ROLE`` cannot ride in a script: it was read as one long role name."""
    client, engine = make_client(roles=["analyst", "finance"], active_role="analyst")

    response = client.post(
        EXECUTE_ENDPOINT, json={"sql": "USE ROLE finance; SELECT CURRENT_ROLE()"}
    )

    assert response.status_code == 400
    assert "on its own" in response.json()["detail"]
    assert engine.roles == []


def test_set_role_later_in_a_script_is_refused_not_sent_to_the_engine(make_client):
    client, engine = make_client(roles=["analyst", "finance"], active_role="analyst")

    response = client.post(EXECUTE_ENDPOINT, json={"sql": "SELECT 1; SET ROLE finance"})

    assert response.status_code == 400
    assert engine.roles == []


def test_a_refused_role_switch_is_forbidden_not_a_signed_out_session(make_client, monkeypatch):
    """A 401 makes the console sign the user out; an ungranted role is a 403."""
    from app.modules.auth.exceptions import InvalidCredentialsError
    from app.modules.auth.service import auth_service

    async def refuse(session_id, role):
        raise InvalidCredentialsError("Role is not granted to this user")

    monkeypatch.setattr(auth_service, "switch_role", refuse)
    client, _engine = make_client(roles=["analyst"], active_role="analyst")

    response = client.post(EXECUTE_ENDPOINT, json={"sql": "USE ROLE finance"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Role is not granted to this user"


def test_a_single_use_role_statement_still_switches(make_client, monkeypatch):
    from app.modules.auth.service import auth_service

    async def switch(session_id, role):
        return {"active_role": role, "security_context_version": 2}

    monkeypatch.setattr(auth_service, "switch_role", switch)
    client, engine = make_client(roles=["analyst", "finance"], active_role="analyst")

    response = client.post(EXECUTE_ENDPOINT, json={"sql": "-- switch\nUSE ROLE finance;"})

    assert response.status_code == 200
    assert response.json()[0]["rows"] == [["finance", 2]]
    assert engine.roles == []
