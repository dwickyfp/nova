"""Network policy API router.

Endpoints:
  GET    /network-policies                 → list Nova-managed policies
  GET    /network-policies/{name}          → policy detail
  POST   /network-policies                 → create + project allowed identities
  PATCH  /network-policies/{name}          → replace rules, sync identities
  DELETE /network-policies/{name}          → drop policy and its projected identities
  GET    /network-policies/connection-sources → recent login sources (audit read)

Access is gated to the StarRocks administrative roles, exactly like the
user/role admin surface: these operations create and drop *user identities*, so
holding a non-admin role must yield 403 before any SQL is built. The frontend
merely hides the menu; AGENTS.md is explicit that the frontend is not a
security boundary.

``policy_name`` is a path segment and is re-validated in the service before it
reaches a statement — a path segment is not trusted just because the create body
was validated.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user, require_role

# `get_current_user` is re-exported so tests can override the authenticated user
# while leaving the real `require_role` check in place.
__all__ = ["router", "ADMIN_ROLES", "require_admin", "get_current_user"]

from .schemas import (
    ConnectionSource,
    NetworkPolicyCreate,
    NetworkPolicyListResponse,
    NetworkPolicyResponse,
    NetworkPolicyUpdate,
)
from .service import NetworkPolicyError, network_policy_service

router = APIRouter()

# Same admin set as the user/role surface: these endpoints manage identities.
ADMIN_ROLES = ("ACCOUNTADMIN", "user_admin", "security_admin")

_admin = require_role(*ADMIN_ROLES)
require_admin = Depends(_admin)


@router.get(
    "/connection-sources",
    response_model=list[ConnectionSource],
    response_class=SanitizingJSONResponse,
)
async def list_connection_sources(limit: int = 50, user: dict = require_admin):
    """Recent successful login sources, read from the audit log."""
    rows = await network_policy_service.list_connection_sources(limit=limit)
    return [ConnectionSource(**row) for row in rows]


@router.get(
    "",
    response_model=NetworkPolicyListResponse,
    response_class=SanitizingJSONResponse,
)
async def list_network_policies(user: dict = require_admin):
    policies = await network_policy_service.list_policies()
    return NetworkPolicyListResponse(policies=policies, count=len(policies))


@router.get(
    "/{name}",
    response_model=NetworkPolicyResponse,
    response_class=SanitizingJSONResponse,
)
async def get_network_policy(name: str, user: dict = require_admin):
    try:
        return await network_policy_service.get_policy(name)
    except NetworkPolicyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "",
    response_model=NetworkPolicyResponse,
    status_code=201,
    response_class=SanitizingJSONResponse,
)
async def create_network_policy(body: NetworkPolicyCreate, user: dict = require_admin):
    try:
        return await network_policy_service.create_policy(body, created_by=user["username"])
    except NetworkPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch(
    "/{name}",
    response_model=NetworkPolicyResponse,
    response_class=SanitizingJSONResponse,
)
async def update_network_policy(name: str, body: NetworkPolicyUpdate, user: dict = require_admin):
    try:
        return await network_policy_service.update_policy(name, body, updated_by=user["username"])
    except NetworkPolicyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete(
    "/{name}",
    response_class=SanitizingJSONResponse,
)
async def delete_network_policy(name: str, user: dict = require_admin):
    try:
        await network_policy_service.delete_policy(name, deleted_by=user["username"])
    except NetworkPolicyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "message": f"Network policy '{name}' deleted"}
