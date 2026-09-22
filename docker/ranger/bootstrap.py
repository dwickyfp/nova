"""Idempotently provision the Ranger objects required by Nova."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = os.environ.get("RANGER_ADMIN_URL", "http://ranger-admin:6080").rstrip("/")
USERNAME = os.environ.get("RANGER_USERNAME", "admin")
PASSWORD = os.environ["RANGER_PASSWORD"]
SERVICE_NAME = os.environ.get("RANGER_SERVICE_NAME", "nova_starrocks")
SERVICE_DEF = json.loads(Path("/starrocks-service-def.json").read_text())
AUTH = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()


def request(
    method: str, path: str, payload: dict[str, Any] | None = None
) -> tuple[int, Any]:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Basic {AUTH}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return 404, None
        detail = exc.read().decode(errors="replace").strip()
        raise RuntimeError(
            f"Ranger returned HTTP {exc.code} for {method} {path}: {detail}"
        ) from exc


def upsert(
    *, get_path: str, create_path: str, update_path: str, payload: dict[str, Any]
) -> dict[str, Any]:
    _, existing = request("GET", get_path)
    if existing:
        object_id = int(existing["id"])
        _, result = request("PUT", update_path.format(id=object_id), {**payload, "id": object_id})
    else:
        _, result = request("POST", create_path, payload)
    return dict(result or {})


def put_role(name: str, users: list[str] | None = None) -> None:
    role_params = urllib.parse.urlencode({"serviceName": SERVICE_NAME})
    payload = {
        "name": name,
        "description": "Managed by Nova",
        "users": [{"name": user, "isAdmin": False} for user in (users or [])],
    }
    _, roles = request("GET", "/service/public/v2/api/roles")
    existing = next(
        (role for role in (roles or []) if role.get("name") == name),
        None,
    )
    if existing:
        role_id = int(existing["id"])
        request(
            "PUT",
            f"/service/public/v2/api/roles/{role_id}",
            {**existing, **payload, "id": role_id},
        )
    else:
        request(
            "POST",
            f"/service/public/v2/api/roles?{role_params}",
            payload,
        )


def put_policy(payload: dict[str, Any]) -> None:
    policy_name = str(payload["name"])
    managed_payload = {"service": SERVICE_NAME, "isEnabled": True, **payload}
    _, policies = request("GET", "/service/public/v2/api/policy")
    existing = next(
        (
            policy
            for policy in (policies or [])
            if policy.get("service") == SERVICE_NAME
            and policy.get("name") == policy_name
        ),
        None,
    )
    if existing:
        policy_id = int(existing["id"])
        request(
            "PUT",
            f"/service/public/v2/api/policy/{policy_id}",
            {**existing, **managed_payload, "id": policy_id},
        )
    else:
        request("POST", "/service/public/v2/api/policy", managed_payload)


def grant_accountadmin_on_default_policies() -> None:
    """Add Nova's immutable super-role to every StarRocks bootstrap policy."""
    _, policies = request("GET", "/service/public/v2/api/policy")
    service_policies = [
        policy
        for policy in (policies or [])
        if policy.get("service") == SERVICE_NAME
        and str(policy.get("name", "")).startswith("all - ")
    ]
    if not service_policies:
        raise RuntimeError("Ranger did not create the StarRocks default policies")

    for policy in service_policies:
        existing_items = [
            item
            for item in policy.get("policyItems", [])
            if "ACCOUNTADMIN" not in item.get("roles", [])
        ]
        accesses = {
            access["type"]: access
            for item in policy.get("policyItems", [])
            for access in item.get("accesses", [])
        }
        accountadmin_item = {
            "roles": ["ACCOUNTADMIN"],
            "accesses": list(accesses.values()),
            "delegateAdmin": True,
        }
        policy_id = int(policy["id"])
        request(
            "PUT",
            f"/service/public/v2/api/policy/{policy_id}",
            {**policy, "policyItems": [*existing_items, accountadmin_item]},
        )


def sync_user(username: str, attributes: dict[str, str]) -> None:
    """Seed role-scoped attributes through Ranger's supported usersync API."""
    payload = {
        "name": username,
        "firstName": username,
        "description": "Nova local acceptance principal",
        "userSource": 1,
        "status": 1,
        "isVisible": 1,
        "userRoleList": ["ROLE_USER"],
        "syncSource": "NOVA",
        "otherAttributes": json.dumps(attributes, sort_keys=True),
    }
    request(
        "POST",
        "/service/xusers/ugsync/users",
        {"totalCount": 1, "vXUsers": [payload]},
    )


def main() -> None:
    for attempt in range(60):
        try:
            status, _ = request("GET", "/service/plugins/definitions/name/starrocks")
            if status in {200, 404}:
                break
        except (OSError, RuntimeError):
            pass
        if attempt == 59:
            raise RuntimeError("Ranger Admin did not become ready within 5 minutes")
        time.sleep(5)

    definition = upsert(
        get_path="/service/plugins/definitions/name/starrocks",
        create_path="/service/plugins/definitions",
        update_path="/service/plugins/definitions/{id}",
        payload=SERVICE_DEF,
    )
    if definition.get("name") != "starrocks":
        raise RuntimeError("StarRocks Ranger service definition verification failed")

    service = upsert(
        get_path=f"/service/plugins/services/name/{urllib.parse.quote(SERVICE_NAME)}",
        create_path="/service/plugins/services",
        update_path="/service/plugins/services/{id}",
        payload={
            "name": SERVICE_NAME,
            "type": "starrocks",
            "displayName": "Nova StarRocks",
            "isEnabled": True,
            "configs": {
                "username": "root",
                "password": "",
                "jdbc.driverClassName": "com.mysql.cj.jdbc.Driver",
                "jdbc.url": "jdbc:mysql://starrocks-fe:9030",
            },
        },
    )
    if service.get("name") != SERVICE_NAME:
        raise RuntimeError("Nova Ranger service verification failed")

    sync_user("alice", {
        "nova_scope.marketing.city": "Jakarta",
        "nova_scope.regional_manager.city": "Jakarta,Bandung",
    })
    sync_user("bob", {"nova_scope.marketing.city": "Bandung"})

    put_role("ACCOUNTADMIN")
    put_role("SECURITYADMIN")
    put_role("marketing", ["alice", "bob"])
    put_role("finance", ["alice"])
    put_role("regional_manager", ["alice"])

    grant_accountadmin_on_default_policies()

    put_policy({
        "name": "nova-managed/control-plane/role-activation-metadata",
        "description": "Allow Nova sessions to validate their role markers",
        "resources": {
            "catalog": {
                "values": ["default_catalog"],
                "isRecursive": False,
                "isExcludes": False,
            },
            "database": {
                "values": ["information_schema"],
                "isRecursive": False,
                "isExcludes": False,
            },
            "table": {
                "values": ["applicable_roles"],
                "isRecursive": False,
                "isExcludes": False,
            },
            "column": {
                "values": ["*"],
                "isRecursive": False,
                "isExcludes": False,
            },
        },
        "policyItems": [{
            "roles": [
                "ACCOUNTADMIN",
                "SECURITYADMIN",
                "marketing",
                "finance",
                "regional_manager",
            ],
            "accesses": [{"type": "select", "isAllowed": True}],
            "delegateAdmin": False,
        }],
    })

    table_resources = {
        "catalog": {"values": ["default_catalog"], "isRecursive": False, "isExcludes": False},
        "database": {"values": ["analytics"], "isRecursive": False, "isExcludes": False},
        "table": {"values": ["sales"], "isRecursive": False, "isExcludes": False},
    }
    put_policy({
        "name": "nova-managed/access/marketing/default_catalog/analytics/sales",
        "description": "Nova local acceptance access policy",
        "resources": {
            **table_resources,
            "column": {"values": ["*"], "isRecursive": False, "isExcludes": False},
        },
        "policyItems": [{
            "roles": ["marketing", "regional_manager"],
            "accesses": [{"type": "select", "isAllowed": True}],
            "delegateAdmin": False,
        }],
    })
    put_policy({
        "name": "nova-managed/scope/marketing/default_catalog/analytics/sales",
        "description": "Nova local acceptance row-filter policy",
        "policyType": 2,
        "resources": table_resources,
        "rowFilterPolicyItems": [
            {
                "roles": [role],
                "accesses": [{"type": "select", "isAllowed": True}],
                "rowFilterInfo": {
                    "filterExpr": (
                        "FIND_IN_SET(city, "
                        f"${{{{GET_USER_ATTR_Q('nova_scope.{role}.city', "
                        f"'__nova_no_scope__')}}}}) > 0"
                    )
                },
            }
            for role in ("marketing", "regional_manager")
        ],
    })

    customer_resources = {
        "catalog": {"values": ["default_catalog"], "isRecursive": False, "isExcludes": False},
        "database": {"values": ["analytics"], "isRecursive": False, "isExcludes": False},
        "table": {"values": ["customers"], "isRecursive": False, "isExcludes": False},
    }
    put_policy({
        "name": "nova-managed/access/marketing/default_catalog/analytics/customers",
        "description": "Nova local acceptance customer access",
        "resources": {
            **customer_resources,
            "column": {"values": ["*"], "isRecursive": False, "isExcludes": False},
        },
        "policyItems": [{
            "roles": ["marketing", "regional_manager"],
            "accesses": [{"type": "select", "isAllowed": True}],
            "delegateAdmin": False,
        }],
    })
    put_policy({
        "name": "nova-managed/mask/marketing/default_catalog/analytics/customers/phone",
        "description": "Nova local acceptance phone mask",
        "policyType": 1,
        "resources": {
            **customer_resources,
            "column": {"values": ["phone"], "isRecursive": False, "isExcludes": False},
        },
        "dataMaskPolicyItems": [{
            "roles": ["marketing"],
            "accesses": [{"type": "select", "isAllowed": True}],
            "dataMaskInfo": {"dataMaskType": "MASK"},
        }],
    })

    print(f"Ranger bootstrap complete for service {SERVICE_NAME}")


if __name__ == "__main__":
    main()
