"""Every declared public API operation must have an intentional auth boundary."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient

from app.core.redis import session_store
from app.main import create_app

# These paths use engine credentials, an internal service token, or expose only
# health. Their token/password validation has separate integration coverage.
NON_SESSION_OPERATIONS = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/internal/ml/predict"),
    ("POST", "/api/v1/internal/superset/identity"),
    ("POST", "/api/v1/internal/superset/token"),
    ("POST", "/api/v1/internal/superset/audit"),
    ("POST", "/api/v1/internal/superset/delegate"),
    ("GET", "/health"),
    ("GET", "/ready"),
}
# Renewal accepts an expired signed token, so its handler owns JWT validation.
HANDLER_AUTH_OPERATIONS = {("POST", "/api/v1/auth/renew")}

application = create_app()
schema = application.openapi()
operations = {
    (method.upper(), path): operation
    for path, methods in schema["paths"].items()
    for method, operation in methods.items()
    if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}
}
session_operations = sorted(set(operations) - NON_SESSION_OPERATIONS)


async def refuse_handler(**_kwargs):
    pytest.fail("An unauthenticated request reached an application handler")


# Keep the real dependency graph, but prevent a broken gate from executing a
# handler's mutation. TestClient intentionally never starts the app lifespan.
protected_operations = set()
for route in iter_route_contexts(application.routes):
    if isinstance(route.original_route, APIRoute):
        route_operations = {(method, route.path_format) for method in route.methods or []}
        if not route_operations & HANDLER_AUTH_OPERATIONS:
            route.dependant.call = refuse_handler
            protected_operations.update(route_operations)


def test_no_operation_silently_loses_its_authentication_contract():
    without_session = {
        key
        for key, operation in operations.items()
        if {"HTTPBearer": []} not in operation.get("security", [])
    }
    assert without_session == NON_SESSION_OPERATIONS
    assert session_operations
    assert set(operations) <= protected_operations | HANDLER_AUTH_OPERATIONS


@pytest.fixture(scope="module")
def client():
    instance = TestClient(application)
    with patch.object(
        session_store, "get", AsyncMock(side_effect=AssertionError("Invalid JWT reached Redis"))
    ):
        yield instance
    instance.close()


@pytest.mark.parametrize(
    "authorization", [None, "Bearer invalid-qa-token"], ids=["missing", "invalid"]
)
@pytest.mark.parametrize(
    "method,path", session_operations, ids=[f"{m} {p}" for m, p in session_operations]
)
def test_session_api_refuses_before_running_handler(client, method, path, authorization):
    url = re.sub(r"\{[^}]+\}", "00000000-0000-4000-8000-000000000000", path)
    headers = {"Authorization": authorization} if authorization else {}
    response = client.request(method, url, headers=headers, json={})
    assert response.status_code in {401, 403}, (method, path, response.status_code)
