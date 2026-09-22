"""Regression tests for server-side RBAC on the monitoring surface (NOVA-105).

The defect: every monitoring endpoint was gated only by ``get_current_user``.
The reads run through the **system pool** (``db.execute_system``), so StarRocks'
own grant filter never sees the caller, and ``POST /queries/kill`` runs
``KILL QUERY`` on that same root connection. Nothing stood between a caller and
another tenant's audit trail, cost history, processlist — or their running
queries. Any signed-in user could read every user's activity and kill every
user's query.

These tests drive the real routers through ``TestClient`` and assert the HTTP
response, so they fail on the pre-fix tree for the reported reason: an
unprivileged caller used to get 200 from the read endpoints and was allowed to
reach ``KILL QUERY``. The app is assembled by hand (routers + dependency
overrides) rather than via ``create_app`` so no engine, Redis or MinIO is
required, matching ``test_internal_ml_endpoint_auth.py``.

The repository/service is stubbed at the module boundary; the *authorization*
chain — ``require_active_role`` over ``get_current_user`` — is the real thing under
test, not a mock.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import deps as deps_module
from app.modules.monitoring import router as monitoring_router
from app.modules.monitoring.router import (
    KILL_ROLES,
    READ_ROLES,
    router,
)

ADMIN_USER = {
    "username": "admin",
    "session_id": "s",
    "roles": ["ACCOUNTADMIN"],
    "active_role": "ACCOUNTADMIN",
    "encrypted_password": "enc",
}
ANALYST_USER = {
    "username": "analyst",
    "session_id": "s",
    "roles": ["test_analyst"],
    "active_role": "test_analyst",
    "encrypted_password": "enc",
}
ROLELESS_USER: dict[str, Any] = {
    "username": "nobody",
    "session_id": "s",
    "roles": [],
    "active_role": None,
    "encrypted_password": "enc",
}

PREFIX = "/api/v1/monitoring"


class _SpyService:
    """Records calls so we can prove a denied request never reached KILL QUERY."""

    def __init__(self) -> None:
        self.kill_calls: list[int] = []
        self.read_calls: list[str] = []

    async def kill_query(self, connection_id: int) -> bool:
        self.kill_calls.append(connection_id)
        return True

    async def get_active_queries(self) -> list[dict[str, Any]]:
        self.read_calls.append("active")
        return []

    async def get_audit_trail(self, **kwargs) -> dict[str, Any]:
        self.read_calls.append("audit")
        return {"items": [], "total": 0}

    async def get_query_history(self, **kwargs) -> dict[str, Any]:
        self.read_calls.append("history")
        return {"items": [], "total": 0}

    async def get_query_history_stats(self, **kwargs) -> dict[str, Any]:
        self.read_calls.append("stats")
        return {
            "total": 0,
            "avg_duration_ms": 0.0,
            "error_count": 0,
            "success_count": 0,
            "error_rate": 0.0,
        }

    async def get_task_runs(self, **kwargs) -> dict[str, Any]:
        self.read_calls.append("task_runs")
        return {"items": [], "total": 0}

    async def get_tasks(self) -> list[dict[str, Any]]:
        self.read_calls.append("tasks")
        return []

    async def get_query_cost_history(self, **kwargs) -> dict[str, Any]:
        self.read_calls.append("cost")
        return {"items": [], "total": 0}

    async def get_cost_aggregation(self, **kwargs) -> list[dict[str, Any]]:
        self.read_calls.append("cost_agg")
        return []

    async def get_fe_metrics_summary(self) -> dict[str, Any]:
        self.read_calls.append("fe")
        return {}

    async def get_runtime_health(self) -> dict[str, Any]:
        self.read_calls.append("runtime_health")
        return {
            "checked_at": "2026-09-21T00:00:00Z",
            "redis": {"status": "healthy", "message": "ok"},
            "scheduler": {"status": "healthy", "message": "ok"},
            "worker": {"status": "healthy", "message": "ok", "instances": 1},
        }

    async def get_data_loads(self, **kwargs) -> dict[str, Any]:
        self.read_calls.append("loads")
        return {"items": [], "total": 0}

    async def get_load_stats(self) -> dict[str, Any]:
        self.read_calls.append("load_stats")
        return {}

    async def get_alerts(self) -> dict[str, Any]:
        self.read_calls.append("alerts")
        return {"summary": {}, "alerts": []}

    async def get_readiness(self) -> dict[str, Any]:
        self.read_calls.append("readiness")
        return {"findings": [], "counts": {}, "result": "PRODUCTION_ACCEPTANCE_PENDING"}


@pytest.fixture
def spy(monkeypatch) -> _SpyService:
    service = _SpyService()
    monkeypatch.setattr(monitoring_router.monitoring_service, "kill_query", service.kill_query)
    for name in (
        "get_active_queries",
        "get_audit_trail",
        "get_query_history",
        "get_query_history_stats",
        "get_task_runs",
        "get_tasks",
        "get_query_cost_history",
        "get_cost_aggregation",
        "get_fe_metrics_summary",
        "get_runtime_health",
        "get_data_loads",
        "get_load_stats",
        "get_alerts",
        "get_readiness",
    ):
        monkeypatch.setattr(monitoring_router.monitoring_service, name, getattr(service, name))
    return service


@pytest.fixture
def app() -> FastAPI:
    from app.core.exceptions import register_exception_handlers

    application = FastAPI()
    # ``require_active_role`` raises ``InsufficientRoleError``; without the real global
    # handler a denial surfaces as a 500 instead of the 403 the contract promises.
    register_exception_handlers(application)
    application.include_router(router, prefix=PREFIX)
    return application


def _as(client_app: FastAPI, user: dict | None) -> TestClient:
    """A TestClient whose ``get_current_user`` returns ``user`` (or 401-ish None)."""

    async def fake_current_user():
        if user is None:
            raise deps_module.HTTPException(status_code=401, detail="Invalid or expired token")
        return user

    client_app.dependency_overrides[deps_module.get_current_user] = fake_current_user
    return TestClient(client_app, raise_server_exceptions=False)


READ_ENDPOINTS = [
    ("GET", "/queries/history"),
    ("GET", "/queries/stats"),
    ("GET", "/audit"),
    ("GET", "/queries/active"),
    ("GET", "/tasks"),
    ("GET", "/tasks/runs"),
    ("GET", "/cost/history"),
    ("GET", "/cost/aggregation"),
    ("GET", "/metrics/fe"),
    ("GET", "/runtime-health"),
    ("GET", "/loads"),
    ("GET", "/loads/stats"),
    ("GET", "/alerts"),
    ("GET", "/readiness"),
]


class TestKillRequiresPrivilegedRole:
    """AC 1 + AC 3: a plain user cannot kill another user's query."""

    def test_analyst_gets_403(self, app, spy):
        with _as(app, ANALYST_USER) as client:
            resp = client.post(f"{PREFIX}/queries/kill", json={"connection_id": 42})

        assert resp.status_code == 403, resp.text
        # The load-bearing assertion: the root ``KILL QUERY`` was never issued.
        assert spy.kill_calls == [], "cross-tenant KILL QUERY was reached"

    def test_roleless_user_gets_403(self, app, spy):
        with _as(app, ROLELESS_USER) as client:
            resp = client.post(f"{PREFIX}/queries/kill", json={"connection_id": 42})

        assert resp.status_code == 403, resp.text
        assert spy.kill_calls == []

    @pytest.mark.parametrize("role", KILL_ROLES)
    def test_admin_role_succeeds(self, app, spy, role):
        admin = {**ADMIN_USER, "roles": [role], "active_role": role}
        with _as(app, admin) as client:
            resp = client.post(f"{PREFIX}/queries/kill", json={"connection_id": 7})

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"success": True}
        assert spy.kill_calls == [7]

    def test_unauthenticated_cannot_kill(self, app, spy):
        with _as(app, None) as client:
            resp = client.post(f"{PREFIX}/queries/kill", json={"connection_id": 42})

        assert resp.status_code == 401, resp.text
        assert spy.kill_calls == []

    def test_inactive_admin_role_gets_403(self, app, spy):
        user = {
            **ANALYST_USER,
            "roles": ["test_analyst", "ACCOUNTADMIN"],
            "active_role": "test_analyst",
        }
        with _as(app, user) as client:
            resp = client.post(f"{PREFIX}/queries/kill", json={"connection_id": 42})

        assert resp.status_code == 403, resp.text
        assert spy.kill_calls == []


class TestReadEndpointsRequirePrivilegedRole:
    """AC 2 + AC 3: audit/cost/loads/active-queries are not world-readable."""

    @pytest.mark.parametrize(("method", "path"), READ_ENDPOINTS)
    def test_analyst_gets_403(self, app, spy, method, path):
        with _as(app, ANALYST_USER) as client:
            resp = client.request(method, f"{PREFIX}{path}")

        assert resp.status_code == 403, resp.text

    @pytest.mark.parametrize(("method", "path"), READ_ENDPOINTS)
    def test_roleless_gets_403(self, app, spy, method, path):
        with _as(app, ROLELESS_USER) as client:
            resp = client.request(method, f"{PREFIX}{path}")

        assert resp.status_code == 403, resp.text

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/queries/history"),
            ("GET", "/audit"),
            ("GET", "/queries/active"),
        ],
    )
    def test_admin_is_allowed(self, app, spy, method, path):
        with _as(app, ADMIN_USER) as client:
            resp = client.request(method, f"{PREFIX}{path}")

        assert resp.status_code == 200, resp.text

    @pytest.mark.parametrize(("method", "path"), READ_ENDPOINTS)
    def test_unauthenticated_read_is_401(self, app, spy, method, path):
        with _as(app, None) as client:
            resp = client.request(method, f"{PREFIX}{path}")

        assert resp.status_code == 401, resp.text


class TestRoleSets:
    """The chosen role set is the repo's existing admin set, not a new one."""

    def test_read_roles_is_the_users_admin_set(self):
        from app.modules.users.router import ADMIN_ROLES

        assert READ_ROLES == ADMIN_ROLES

    def test_kill_roles_does_not_include_plain_analyst_roles(self):
        assert "test_analyst" not in KILL_ROLES

    def test_admin_roles_include_ranger_securityadmin(self):
        assert set(READ_ROLES) == {
            "ACCOUNTADMIN",
            "SECURITYADMIN",
            "user_admin",
            "security_admin",
        }


class TestEveryMonitoringEndpointIsGuarded:
    """Structural backstop: no route may regress to bare ``get_current_user``."""

    @staticmethod
    def _routes():
        return [r for r in router.routes if hasattr(r, "dependant")]

    def test_router_is_not_empty(self):
        assert self._routes()

    @pytest.mark.parametrize(
        "route",
        [r for r in router.routes if hasattr(r, "dependant")],
        ids=lambda r: f"{sorted(r.methods)[0] if r.methods else '?'} {r.path}",
    )
    def test_route_uses_require_active_role(self, route):
        dep_names = _dependency_names(route.dependant)
        assert "require_active_role.<locals>._check" in dep_names, (
            f"{route.path} is not guarded by require_active_role — only {dep_names}"
        )

    def test_kill_route_is_present_and_guarded(self):
        kill = [
            r
            for r in self._routes()
            if r.path == "/queries/kill" and "POST" in (r.methods or set())
        ]
        assert kill, "POST /queries/kill route not found"
        assert "require_active_role.<locals>._check" in _dependency_names(
            kill[0].dependant
        )


def _dependency_names(dependant) -> set[str]:
    """Collect the qualified names of a route's dependency callables."""
    names: set[str] = set()
    stack = [dependant]
    while stack:
        node = stack.pop()
        call = getattr(node, "call", None)
        if call is not None:
            names.add(f"{getattr(call, '__qualname__', getattr(call, '__name__', ''))}")
        stack.extend(getattr(node, "dependencies", []) or [])
    return names
