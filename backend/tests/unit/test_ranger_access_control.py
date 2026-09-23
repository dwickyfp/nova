from __future__ import annotations

import json

import httpx
import pytest

from app.core.redis import SessionStore
from app.integrations.ranger.client import RangerClient
from app.integrations.ranger.compiler import (
    compile_access_policy,
    compile_mask_policy,
    compile_row_filter_policy,
)
from app.integrations.ranger.schemas import RangerRole
from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.access_control.service import AccessControlService
from app.modules.access_control.statement_router import SecurityStatementRouter
from app.modules.ml_engine.spec import MLSecurityContext
from app.proxy.session import SessionState, parse_role_statement


class _RedisRecorder:
    def __init__(self) -> None:
        self.mapping: dict = {}

    async def hset(self, _key: str, *, mapping: dict) -> None:
        self.mapping = mapping

    async def expire(self, _key: str, _ttl: int) -> None:
        return None


def test_security_context_requires_one_non_root_role() -> None:
    context = SecurityContext(principal="alice", active_role="marketing")
    assert context.active_role == "marketing"
    assert context.cache_partition() != context.switched("finance").cache_partition()

    for principal, role in (("", "marketing"), ("root", "marketing"), ("alice", "")):
        with pytest.raises(SecurityContextError):
            SecurityContext(principal=principal, active_role=role)
        with pytest.raises(SecurityContextError):
            SecurityContext(principal="alice", active_role="marketing,finance")


@pytest.mark.asyncio
async def test_session_store_requires_explicit_roles_only_in_ranger_mode(monkeypatch) -> None:
    store = SessionStore()
    recorder = _RedisRecorder()
    store._redis = recorder  # type: ignore[assignment]

    monkeypatch.setattr("app.core.redis.settings.RANGER_ENABLED", True)
    with pytest.raises(ValueError, match="explicit default role"):
        await store.create("alice", "encrypted", [])

    monkeypatch.setattr("app.core.redis.settings.RANGER_ENABLED", False)
    await store.create("legacy", "encrypted", [])
    assert recorder.mapping["default_role"] == ""
    assert recorder.mapping["active_role"] == ""


def test_proxy_role_parser_and_commit_are_fail_closed() -> None:
    assert parse_role_statement("SET ROLE marketing") == "marketing"
    assert parse_role_statement("USE ROLE 'finance'") == "finance"
    assert parse_role_statement("SET ROLE DEFAULT") == "DEFAULT"
    for statement in ("SET ROLE ALL", "SET ROLE NONE", "SET ROLE marketing, finance"):
        with pytest.raises(ValueError):
            parse_role_statement(statement)

    session = SessionState()
    session.establish_security(
        principal="alice",
        assigned_roles=("finance", "marketing"),
        default_role="marketing",
        active_role="marketing",
    )
    with pytest.raises(ValueError):
        session.commit_role("security_admin")
    assert session.active_role == "marketing"
    assert session.security_context_version == 1
    session.commit_role("finance")
    assert session.active_role == "finance"
    assert session.security_context_version == 2


def test_ranger_policy_compiler_separates_access_scope_and_mask(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.RANGER_SERVICE_NAME", "nova_starrocks")
    access = compile_access_policy(
        role="marketing",
        catalog="default_catalog",
        database="analytics",
        table="sales",
        accesses=["SELECT"],
    ).to_api()
    assert access["policyItems"][0]["roles"] == ["marketing"]
    assert access["policyItems"][0]["accesses"] == [{"type": "select", "isAllowed": True}]
    assert access["resources"]["column"]["values"] == ["*"]

    scope = compile_row_filter_policy(
        role="marketing",
        catalog="default_catalog",
        database="analytics",
        table="sales",
        bindings=[
            ("city", "city", ["Jakarta", "Bandung"]),
            ("business_unit", "business_unit", ["Consumer"]),
        ],
    ).to_api()
    expression = scope["rowFilterPolicyItems"][0]["rowFilterInfo"]["filterExpr"]
    assert "city IN (${{GET_USER_ATTR_Q('nova_scope.marketing.city'" in expression
    assert " AND " in expression
    assert scope["policyItems"] == []

    mask = compile_mask_policy(
        role="marketing",
        catalog="default_catalog",
        database="analytics",
        table="customers",
        column="phone",
        mask_type="MASK",
    ).to_api()
    assert mask["dataMaskPolicyItems"][0]["roles"] == ["marketing"]


@pytest.mark.asyncio
async def test_ranger_client_role_upsert_is_service_scoped(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.RANGER_SERVICE_NAME", "nova_starrocks")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(404, request=request)
        return httpx.Response(201, json={"id": 7, "name": "marketing"}, request=request)

    async with httpx.AsyncClient(
        base_url="http://ranger.invalid", transport=httpx.MockTransport(handler)
    ) as http:
        result = await RangerClient(http).put_role(RangerRole(name="marketing"))

    assert result["id"] == 7
    assert all("serviceName=nova_starrocks" in str(call.url) for call in calls)
    assert "password" not in " ".join(str(call.url) for call in calls).lower()


@pytest.mark.asyncio
async def test_ranger_29_role_list_and_missing_role_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/roles/name/missing"):
            return httpx.Response(400, request=request)
        return httpx.Response(
            200,
            json={"totalCount": 1, "roles": [{"id": 7, "name": "marketing"}]},
            request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://ranger.invalid", transport=httpx.MockTransport(handler)
    ) as http:
        client = RangerClient(http)
        assert [role["name"] for role in await client.list_roles()] == ["marketing"]
        assert await client.get_role("missing") is None


@pytest.mark.asyncio
async def test_ranger_policy_lookup_handles_managed_names_with_slashes() -> None:
    name = "nova-managed/access/city_reader/default_catalog/rbac_city_demo/city_sales"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/service/public/v2/api/policy"
        return httpx.Response(
            200, json=[{"id": 19, "name": name, "policyItems": []}], request=request,
        )

    async with httpx.AsyncClient(
        base_url="http://ranger.invalid", transport=httpx.MockTransport(handler)
    ) as http:
        client = RangerClient(http)
        assert (await client.get_policy(name))["id"] == 19
        assert await client.get_policy("missing") is None


@pytest.mark.asyncio
async def test_assign_role_syncs_new_user_before_ranger_membership(monkeypatch) -> None:
    from app.modules.access_control import service as service_module

    class Ranger:
        synced = False

        async def put_user_attributes(self, username, attributes):
            assert username == "rbac_jakarta" and attributes == {}
            self.synced = True

        async def get_role(self, name):
            assert name == "rbac_city_reader"
            return {"id": 1, "name": name, "users": []}

        async def put_role(self, role):
            assert self.synced
            assert [user.name for user in role.users] == ["rbac_jakarta"]

    class DB:
        async def execute_system(self, sql):
            assert sql.startswith("GRANT rbac_city_reader TO USER")

    ranger = Ranger()
    service = AccessControlService(ranger)

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service_module, "db", DB())
    monkeypatch.setattr(service, "_audit_admin", no_audit)
    await service.assign_role(
        SecurityContext(principal="nova_admin", active_role="ACCOUNTADMIN"),
        role="rbac_city_reader", username="rbac_jakarta",
    )
    assert ranger.synced


@pytest.mark.asyncio
async def test_ranger_usersync_serializes_role_scoped_attributes() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"xuserInfoList": [{"name": "alice", "otherAttrsMap": {}}]},
                request=request,
            )
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=1, request=request)

    async with httpx.AsyncClient(
        base_url="http://ranger.invalid", transport=httpx.MockTransport(handler)
    ) as http:
        await RangerClient(http).put_user_attributes(
            "alice", {"nova_scope.marketing.city": "Bandung,Jakarta"}
        )

    attrs = json.loads(bodies[0]["vXUsers"][0]["otherAttributes"])
    assert attrs["nova_scope.marketing.city"] == "Bandung,Jakarta"
    assert bodies[0]["vXUsers"][0]["syncSource"] == "NOVA"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statement",
    [
        "GRANT SELECT ON TABLE analytics.sales TO ROLE marketing",
        "GRANT SELECT ON analytics.sales TO ROLE marketing",
    ],
)
async def test_security_statement_router_never_forwards_native_grant(
    monkeypatch, statement: str
) -> None:
    monkeypatch.setattr("app.core.config.settings.RANGER_ENABLED", True)
    calls: list[tuple[str, dict]] = []

    class Service:
        async def grant_access(self, security, **kwargs):
            calls.append((security.active_role, kwargs))
            return {"id": 3}

    router = SecurityStatementRouter(Service())
    routed = await router.route(
        statement,
        security=SecurityContext(principal="admin", active_role="ACCOUNTADMIN"),
    )
    assert routed.handled is True
    assert calls == [
        (
            "ACCOUNTADMIN",
            {
                "role": "marketing",
                "catalog": "default_catalog",
                "database": "analytics",
                "table": "sales",
                "accesses": ["SELECT"],
            },
        )
    ]


@pytest.mark.asyncio
async def test_security_statement_router_routes_role_hierarchy(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.RANGER_ENABLED", True)
    calls: list[dict] = []

    class Service:
        async def grant_role_to_role(self, security, **kwargs):
            calls.append({"active_role": security.active_role, **kwargs})

    routed = await SecurityStatementRouter(Service()).route(
        "GRANT base_reader TO ROLE marketing",
        security=SecurityContext(principal="admin", active_role="ACCOUNTADMIN"),
    )

    assert routed.handled is True
    assert calls == [
        {
            "active_role": "ACCOUNTADMIN",
            "parent_role": "marketing",
            "member_role": "base_reader",
        }
    ]


@pytest.mark.asyncio
async def test_effective_access_resolves_scope_and_mask_from_ranger(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.RANGER_MANAGED_POLICY_PREFIX", "nova-managed")

    class Ranger:
        async def list_policies(self):
            resources = {
                "catalog": {"values": ["default_catalog"]},
                "database": {"values": ["analytics"]},
                "table": {"values": ["sales"]},
            }
            return [
                {
                    "name": "nova-managed/access/marketing/sales",
                    "policyType": 0,
                    "resources": resources,
                    "policyItems": [
                        {
                            "roles": ["marketing"],
                            "accesses": [{"type": "select", "isAllowed": True}],
                        }
                    ],
                },
                {
                    "name": "nova-managed/scope/marketing/sales",
                    "policyType": 2,
                    "resources": resources,
                    "rowFilterPolicyItems": [
                        {
                            "roles": ["marketing"],
                            "rowFilterInfo": {
                                "filterExpr": (
                                    "city IN (${{GET_USER_ATTR_Q("
                                    "'nova_scope.marketing.city', '__none__')}})"
                                )
                            },
                        }
                    ],
                },
                {
                    "name": "nova-managed/mask/marketing/sales/phone",
                    "policyType": 1,
                    "resources": {**resources, "column": {"values": ["phone"]}},
                    "dataMaskPolicyItems": [
                        {
                            "roles": ["marketing"],
                            "dataMaskInfo": {"dataMaskType": "MASK"},
                        }
                    ],
                },
            ]

        async def get_user_attributes(self, principal):
            assert principal == "alice"
            return {"nova_scope.marketing.city": "Jakarta"}

    result = await AccessControlService(Ranger()).effective_access(
        principal="alice", active_role="marketing", resource="analytics.sales"
    )

    assert result["object_access"] == ["SELECT"]
    assert result["row_restrictions"] == ["city IN ('Jakarta')"]
    assert result["column_restrictions"] == {"phone": "MASK"}


def test_ml_cache_scope_includes_principal_role_and_epoch() -> None:
    marketing = MLSecurityContext(
        username="alice", password="secret", role="marketing", security_context_version=1
    )
    finance = MLSecurityContext(
        username="alice", password="secret", role="finance", security_context_version=2
    )
    bob = MLSecurityContext(
        username="bob", password="secret", role="marketing", security_context_version=1
    )
    assert len({marketing.scope_key, finance.scope_key, bob.scope_key}) == 3
