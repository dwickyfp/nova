"""Ranger-backed Access Control API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from app.core.deps import get_current_user

from .schemas import (
    AccessPolicyRequest,
    DataScopeRequest,
    EffectiveAccessRequest,
    MaskPolicyRequest,
    RoleCreateRequest,
    RoleMemberRequest,
)
from .security_context import SecurityContext, SecurityContextError
from .service import AccessControlError, access_control_service

router = APIRouter()
CurrentUser = Annotated[dict, Depends(get_current_user)]


def _security(user: dict) -> SecurityContext:
    try:
        return SecurityContext.from_session(user)
    except SecurityContextError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/health")
async def health(user: CurrentUser):
    access_control_service.require_security_admin(_security(user))
    return await access_control_service.health()


@router.get("/policies")
async def list_policies(user: CurrentUser):
    access_control_service.require_security_admin(_security(user))
    policies = await access_control_service.list_managed_policies()
    return {"policies": policies, "count": len(policies), "provider": "Ranger"}


@router.get("/roles")
async def list_roles(user: CurrentUser):
    access_control_service.require_security_admin(_security(user))
    roles = await access_control_service.list_roles()
    return {"roles": roles, "count": len(roles), "provider": "Ranger"}


@router.post("/roles", status_code=201)
async def create_role(body: RoleCreateRequest, user: CurrentUser):
    try:
        projection = await access_control_service.create_role(
            _security(user), body.name, body.description
        )
        return {
            "name": projection.name,
            "status": projection.state.value,
            "marker_exists": projection.marker_exists,
            "ranger_exists": projection.ranger_exists,
        }
    except AccessControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/roles/{name}", status_code=204)
async def drop_role(name: str, user: CurrentUser):
    try:
        await access_control_service.drop_role(_security(user), name)
    except AccessControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/roles/{role}/members", status_code=201)
async def assign_role(role: str, body: RoleMemberRequest, user: CurrentUser):
    try:
        await access_control_service.assign_role(
            _security(user), role=role, username=body.username, host=body.host
        )
    except AccessControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"role": role, "username": body.username, "status": "ACTIVE"}


@router.delete("/roles/{role}/members/{username}", status_code=204)
async def revoke_role(role: str, username: str, user: CurrentUser, host: str = "%"):
    try:
        await access_control_service.revoke_role(
            _security(user), role=role, username=username, host=host
        )
    except AccessControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/data-access", status_code=201)
async def grant_access(body: AccessPolicyRequest, user: CurrentUser):
    try:
        policy = await access_control_service.grant_access(_security(user), **body.model_dump())
    except AccessControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"policy": policy, "provider": "Ranger", "status": "PROPAGATING"}


@router.post("/data-scopes", status_code=201)
async def put_data_scope(body: DataScopeRequest, user: CurrentUser):
    bindings = [(binding.dimension, binding.column, binding.values) for binding in body.bindings]
    try:
        policy = await access_control_service.put_data_scope(
            _security(user),
            principal=body.principal,
            role=body.role,
            catalog=body.catalog,
            database=body.database,
            table=body.table,
            bindings=bindings,
        )
    except AccessControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"policy": policy, "provider": "Ranger", "status": "PROPAGATING"}


@router.post("/masks", status_code=201)
async def put_mask(body: MaskPolicyRequest, user: CurrentUser):
    try:
        policy = await access_control_service.put_mask(_security(user), **body.model_dump())
    except AccessControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"policy": policy, "provider": "Ranger", "status": "PROPAGATING"}


@router.post("/effective-access")
async def effective_access(body: EffectiveAccessRequest, user: CurrentUser):
    access_control_service.require_security_admin(_security(user))
    return await access_control_service.effective_access(**body.model_dump())
