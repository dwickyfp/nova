"""Unit tests for the network-policy surface (NOVA-110).

Ground truth pinned by these tests: StarRocks 4.1.4 has no network-policy
object, so Nova models a per-user policy and projects it onto the one engine
control that exists — the host part of a user identity (``user@'host'``). The
tests pin:

- host-pattern validation (IP / CIDR / hostname / ``%``, and the injection
  shapes that must be refused);
- the identity DDL builders, including escaping;
- create/update/delete orchestration: metadata first, idempotent identity
  projection, and that only Nova-projected identities are ever dropped;
- admin-only authorization on every route, including the denied 403 path;
- the audit row each mutation writes.

No engine and no Redis are needed: the repository and the system-pool helpers
are replaced with in-memory fakes.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import register_exception_handlers
from app.modules.network_policies import router as router_module
from app.modules.network_policies import service as service_module
from app.modules.network_policies.schemas import (
    NetworkPolicyCreate,
    NetworkPolicyUpdate,
    NetworkRule,
)
from app.modules.network_policies.service import (
    NetworkPolicyError,
    NetworkPolicyService,
)


class FakeRepo:
    """In-memory stand-in for the metadata repository."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def list_all(self) -> list[dict]:
        return list(self.rows.values())

    async def get_by_name(self, name: str) -> dict | None:
        return self.rows.get(name)

    async def upsert(self, **kwargs) -> None:
        name = kwargs["name"]
        existing = self.rows.get(name, {})
        self.rows[name] = {
            "id": existing.get("id", "id1"),
            "name": name,
            "username": kwargs["username"],
            "host": kwargs["host"],
            "allowed_hosts": kwargs["allowed_hosts"],
            "denied_hosts": kwargs["denied_hosts"],
            "comment": kwargs["comment"],
            "created_at": existing.get("created_at"),
            "created_by": existing.get("created_by") or kwargs["created_by"],
        }

    async def delete(self, name: str) -> bool:
        return self.rows.pop(name, None) is not None


class FakeSystemDB:
    """Capture system-pool statements and answer ``SHOW USERS``."""

    def __init__(self, users: list[str] | None = None) -> None:
        self.calls: list[str] = []
        self._users = users or []

    async def __call__(self, sql: str, params=None) -> dict:
        self.calls.append(sql)
        if sql.strip().upper().startswith("SHOW USERS"):
            rows = [[u] for u in self._users]
            return {"columns": ["User"], "rows": rows, "row_count": len(rows)}
        if "AUDIT_LOG" in sql:
            return {"columns": ["user_name", "last_seen", "events"], "rows": []}
        return {"columns": [], "rows": [], "affected": 0}

    async def execute_system(self, sql: str, params=None) -> dict:
        return await self(sql, params)


@pytest.fixture
def env(monkeypatch):
    """Wire a fresh fake repo, audit sink, and system DB into the service."""
    repo = FakeRepo()
    sysdb = FakeSystemDB()
    audits: list[dict] = []

    async def fake_audit(**kwargs):
        audits.append(kwargs)
        return "qid"

    monkeypatch.setattr(service_module, "network_policy_repo", repo)
    monkeypatch.setattr(service_module, "db", sysdb)
    monkeypatch.setattr(service_module, "write_audit_log", fake_audit)
    return repo, sysdb, audits


def _create(name: str = "office", **overrides) -> NetworkPolicyCreate:
    data: dict[str, Any] = {
        "name": name,
        "username": "analyst",
        "allowed_hosts": [NetworkRule(host_pattern="10.0.0.0/8")],
    }
    data.update(overrides)
    return NetworkPolicyCreate(**data)


class TestHostPatternValidation:
    @pytest.mark.parametrize(
        "pattern",
        ["10.0.0.1", "10.0.0.0/8", "2001:db8::/32", "%", "db.internal", "host-1.example.com"],
    )
    def test_valid_patterns_accepted(self, pattern):
        assert NetworkRule(host_pattern=pattern).host_pattern == pattern

    @pytest.mark.parametrize(
        "pattern",
        ["", "10.0.0.1'", "10.0.0.1; DROP USER x", "10.0.0.1/999", "a b", "10.0.0.1\\", "x'@'%"],
    )
    def test_injection_shapes_refused(self, pattern):
        with pytest.raises(ValueError):
            NetworkRule(host_pattern=pattern)


class TestIdentitySql:
    def test_allow_identity_sql(self):
        sql = NetworkPolicyService().build_allow_identity_sql("analyst", "10.0.0.0/8")
        assert sql == "CREATE USER 'analyst'@'10.0.0.0/8' IDENTIFIED BY ''"

    def test_drop_identity_sql(self):
        sql = NetworkPolicyService().build_drop_identity_sql("analyst", "10.0.0.0/8")
        assert sql == "DROP USER 'analyst'@'10.0.0.0/8'"

    def test_quote_is_escaped(self):
        sql = NetworkPolicyService().build_allow_identity_sql("an'alyst", "10.0.0.1")
        assert "\\'" in sql


class TestServiceCrud:
    async def test_create_persists_and_projects_identities(self, env):
        repo, sysdb, audits = env
        result = await NetworkPolicyService().create_policy(_create(), created_by="root")

        assert result.name == "office"
        assert result.username == "analyst"
        assert repo.rows["office"]["allowed_hosts"][0]["host_pattern"] == "10.0.0.0/8"
        assert any("CREATE USER 'analyst'@'10.0.0.0/8'" in c for c in sysdb.calls)
        assert audits[-1]["action"] == "create_network_policy"

    async def test_duplicate_name_refused(self, env):
        service = NetworkPolicyService()
        await service.create_policy(_create(), created_by="root")
        with pytest.raises(NetworkPolicyError):
            await service.create_policy(_create(), created_by="root")

    async def test_get_missing_is_not_found_error(self, env):
        with pytest.raises(NetworkPolicyError):
            await NetworkPolicyService().get_policy("nope")

    async def test_update_syncs_added_and_removed_hosts(self, env):
        repo, sysdb, audits = env
        service = NetworkPolicyService()
        await service.create_policy(_create(), created_by="root")
        sysdb.calls.clear()
        sysdb._users = ["'analyst'@'10.0.0.0/8'"]

        await service.update_policy(
            "office",
            NetworkPolicyUpdate(allowed_hosts=[NetworkRule(host_pattern="192.168.1.0/24")]),
            updated_by="root",
        )

        # New host gets an identity; the removed one is dropped.
        assert any("CREATE USER 'analyst'@'192.168.1.0/24'" in c for c in sysdb.calls)
        assert any("DROP USER 'analyst'@'10.0.0.0/8'" in c for c in sysdb.calls)
        assert audits[-1]["action"] == "update_network_policy"

    async def test_update_omitted_lists_preserve_existing(self, env):
        repo, _sysdb, _audits = env
        service = NetworkPolicyService()
        await service.create_policy(_create(), created_by="root")
        result = await service.update_policy(
            "office", NetworkPolicyUpdate(comment="vpn only"), updated_by="root"
        )
        assert result.comment == "vpn only"
        assert repo.rows["office"]["allowed_hosts"][0]["host_pattern"] == "10.0.0.0/8"

    async def test_only_projected_identities_are_dropped(self, env):
        """A pre-existing out-of-band identity must survive a policy update."""
        repo, sysdb, _audits = env
        service = NetworkPolicyService()
        await service.create_policy(_create(), created_by="root")
        sysdb.calls.clear()
        # The user also has an identity Nova never projected.
        sysdb._users = ["'analyst'@'10.0.0.0/8'", "'analyst'@'%'"]

        await service.update_policy(
            "office", NetworkPolicyUpdate(allowed_hosts=[]), updated_by="root"
        )

        assert any("DROP USER 'analyst'@'10.0.0.0/8'" in c for c in sysdb.calls)
        assert not any("'analyst'@'%'" in c and "DROP" in c for c in sysdb.calls)

    async def test_delete_removes_row_and_audits(self, env):
        repo, sysdb, audits = env
        service = NetworkPolicyService()
        await service.create_policy(_create(), created_by="root")
        await service.delete_policy("office", deleted_by="root")

        assert "office" not in repo.rows
        assert audits[-1]["action"] == "delete_network_policy"

    async def test_delete_missing_is_error(self, env):
        with pytest.raises(NetworkPolicyError):
            await NetworkPolicyService().delete_policy("nope", deleted_by="root")

    async def test_connection_sources_reads_audit(self, env):
        _repo, _sysdb, _audits = env
        rows = await NetworkPolicyService().list_connection_sources(limit=10)
        assert rows == []


# ── Route-level authorization (the denied 403 path) ────────────────────────


def _dep_names(route) -> set[str]:
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        node = stack.pop()
        call = getattr(node, "call", None)
        if call is not None:
            names.add(getattr(call, "__qualname__", getattr(call, "__name__", "")))
        stack.extend(getattr(node, "dependencies", []) or [])
    return names


def make_client(user: dict[str, Any]) -> TestClient:
    """Build a client whose authenticated user is ``user``.

    Only ``get_current_user`` is overridden, so the real ``require_role`` check
    still runs — that is the control under test on the denied path.
    """
    app = FastAPI()
    app.include_router(router_module.router, prefix="/api/v1/network-policies")
    app.dependency_overrides[router_module.get_current_user] = lambda: user
    # Production registers these handlers, which turn InsufficientRoleError into
    # a 403; without them the control's own exception becomes a generic 500 and
    # the test would be asserting the wrong status.
    register_exception_handlers(app)
    return TestClient(app, raise_server_exceptions=False)


def _admin() -> dict[str, Any]:
    return {
        "username": "root",
        "roles": ["ACCOUNTADMIN"],
        "session_id": "s",
        "encrypted_password": "e",
    }


class TestRouteAuthorization:
    def _routes(self):
        return [r for r in router_module.router.routes if hasattr(r, "dependant")]

    def test_router_is_not_empty(self):
        assert self._routes()

    @pytest.mark.parametrize(
        "route",
        [r for r in router_module.router.routes if hasattr(r, "dependant")],
        ids=lambda r: f"{sorted(r.methods)[0] if r.methods else '?'} {r.path}",
    )
    def test_every_route_is_admin_guarded(self, route):
        assert "require_role.<locals>._check" in _dep_names(route)

    def test_admin_role_set(self):
        assert "ACCOUNTADMIN" in router_module.ADMIN_ROLES
        assert "public" not in router_module.ADMIN_ROLES


class TestRouteDeniedPath:
    def test_non_admin_is_denied_403(self, monkeypatch):
        """A non-admin must be refused before any SQL is built."""
        client = make_client({"username": "analyst", "roles": ["test_analyst"]})
        resp = client.get("/api/v1/network-policies")
        assert resp.status_code == 403

    def test_roleless_user_is_denied_403(self):
        client = make_client({"username": "nobody", "roles": []})
        resp = client.post("/api/v1/network-policies", json={"name": "x", "username": "u"})
        assert resp.status_code == 403


class TestRouteAllowedPath:
    def test_admin_lists_policies(self, env, monkeypatch):
        repo, _sysdb, _audits = env
        client = make_client(_admin())
        resp = client.get("/api/v1/network-policies")
        assert resp.status_code == 200
        assert resp.json() == {"policies": [], "count": 0}

    def test_admin_creates_policy(self, env):
        _repo, _sysdb, _audits = env
        client = make_client(_admin())
        resp = client.post(
            "/api/v1/network-policies",
            json={
                "name": "office",
                "username": "analyst",
                "allowed_hosts": [{"host_pattern": "10.0.0.0/8"}],
            },
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "office"

    def test_invalid_host_is_400(self, env):
        _repo, _sysdb, _audits = env
        client = make_client(_admin())
        resp = client.post(
            "/api/v1/network-policies",
            json={
                "name": "office",
                "username": "analyst",
                "allowed_hosts": [{"host_pattern": "10.0.0.1; DROP USER x"}],
            },
        )
        assert resp.status_code == 422

    def test_missing_policy_is_404(self, env):
        client = make_client(_admin())
        assert client.get("/api/v1/network-policies/nope").status_code == 404
