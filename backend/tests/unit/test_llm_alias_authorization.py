from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.core.exceptions import register_exception_handlers
from app.modules.llm_functions import router as aliases
from app.modules.users.router import ADMIN_ROLES

ALIAS = {
    "id": "fixture-alias",
    "alias_name": "fixture-complete",
    "function_type": "complete",
    "provider_id": "fixture-provider",
}
MUTATIONS = [
    ("POST", "/aliases", ALIAS, "create_alias", ALIAS, 201),
    ("PUT", "/aliases/fixture-alias", {"is_default": True}, "update_alias", ALIAS, 200),
    ("DELETE", "/aliases/fixture-alias", None, "delete_alias", True, 204),
    (
        "POST", "/aliases/register-udfs", None, "register_all_udfs",
        {"success": True, "registered": 0, "failed": 0, "details": []}, 200,
    ),
]


@pytest.mark.parametrize("operation", MUTATIONS, ids=[item[3] for item in MUTATIONS])
@pytest.mark.parametrize("active_role", ["ANALYST", None, *ADMIN_ROLES])
def test_global_alias_mutations_require_an_active_admin(monkeypatch, operation, active_role):
    method, path, body, service_method, result, success_status = operation
    operation_mock = AsyncMock(return_value=result)
    monkeypatch.setattr(aliases.llm_function_service, service_method, operation_mock)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(aliases.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "username": "fixture-user", "roles": [*ADMIN_ROLES, "ANALYST"],
        "assigned_roles": [*ADMIN_ROLES, "ANALYST"], "active_role": active_role,
    }
    with TestClient(app) as client:
        response = client.request(method, path, json=body)
    if active_role in ADMIN_ROLES:
        assert response.status_code == success_status
        operation_mock.assert_awaited_once()
    else:
        assert response.status_code == 403
        operation_mock.assert_not_awaited()


def test_authenticated_non_admin_can_read_aliases_and_registration_status(monkeypatch):
    monkeypatch.setattr(
        aliases.llm_function_service, "list_aliases", AsyncMock(return_value=[ALIAS])
    )
    monkeypatch.setattr(aliases.llm_function_service, "get_udf_status", AsyncMock(return_value=[]))
    app = FastAPI()
    app.include_router(aliases.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "username": "fixture-user", "roles": ["ANALYST"], "active_role": "ANALYST",
    }
    with TestClient(app) as client:
        response = client.get("/aliases")
        assert response.status_code == 200
        assert response.json()["aliases"][0]["id"] == ALIAS["id"]
        assert client.get("/aliases/udf-status").status_code == 200
