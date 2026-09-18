"""Resource Groups API router — warehouse CRUD + classifier configuration.

Endpoints under ``/api/v1/resource-groups``:
  GET    /                       → list resource groups
  POST   /                       → CREATE RESOURCE GROUP … WITH (…)
  PATCH  /{name}                 → ALTER RESOURCE GROUP … SET (…)
  DELETE /{name}                 → DROP RESOURCE GROUP
  POST   /{name}/classifiers     → ALTER RESOURCE GROUP … ADD (…)
  DELETE /{name}/classifiers     → ALTER RESOURCE GROUP … DROP (…)
  GET    /usage                  → SHOW USAGE RESOURCE GROUPS

Quota enforcement is the engine's — the usage endpoint reports the engine's own
counts, never a Nova-side simulation (roadmap #16).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user

from .schemas import (
    ClassifierSpec,
    ResourceGroupAlter,
    ResourceGroupCreate,
    ResourceGroupListResponse,
    ResourceGroupResponse,
)
from .service import ResourceGroupError, resource_group_service

router = APIRouter()

CurrentUser = Annotated[dict, Depends(get_current_user)]


def _caller(user: dict) -> dict:
    return {
        "username": user["username"],
        "encrypted_password": user["encrypted_password"],
        "session_id": user.get("session_id"),
        "role": user.get("active_role"),
    }


@router.get(
    "",
    response_model=ResourceGroupListResponse,
    response_class=SanitizingJSONResponse,
)
async def list_resource_groups(user: CurrentUser):
    """List resource groups visible to the caller."""
    try:
        groups = await resource_group_service.list_resource_groups(**_caller(user))
    except ResourceGroupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResourceGroupListResponse(
        resource_groups=[ResourceGroupResponse(**g) for g in groups],
        count=len(groups),
    )


@router.get("/usage", response_class=SanitizingJSONResponse)
async def resource_group_usage(user: CurrentUser):
    """Engine-reported running/queued counts per resource group."""
    try:
        usage = await resource_group_service.usage(**_caller(user))
    except ResourceGroupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"usage": usage, "count": len(usage)}


@router.post(
    "",
    response_model=ResourceGroupResponse,
    status_code=201,
    response_class=SanitizingJSONResponse,
)
async def create_resource_group(body: ResourceGroupCreate, user: CurrentUser):
    """Create a resource group. Runs as the caller."""
    try:
        created = await resource_group_service.create_resource_group(
            body, **_caller(user)
        )
    except ResourceGroupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResourceGroupResponse(**created)


@router.patch(
    "/{name}",
    response_model=ResourceGroupResponse,
    response_class=SanitizingJSONResponse,
)
async def alter_resource_group(name: str, body: ResourceGroupAlter, user: CurrentUser):
    """Set one or more attributes on a resource group."""
    try:
        updated = await resource_group_service.alter_resource_group(
            name, properties=body.properties, **_caller(user)
        )
    except ResourceGroupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResourceGroupResponse(**updated)


@router.delete("/{name}", response_class=SanitizingJSONResponse)
async def drop_resource_group(name: str, user: CurrentUser):
    """Drop a resource group."""
    try:
        await resource_group_service.drop_resource_group(name, **_caller(user))
    except ResourceGroupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "message": f"Resource group '{name}' dropped"}


@router.post("/{name}/classifiers", response_class=SanitizingJSONResponse)
async def add_classifier(name: str, body: ClassifierSpec, user: CurrentUser):
    """Add a classifier rule to a resource group."""
    try:
        await resource_group_service.add_classifier(name, body, **_caller(user))
    except ResourceGroupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "resource_group": name, "classifier": body.model_dump()}


@router.delete("/{name}/classifiers", response_class=SanitizingJSONResponse)
async def drop_classifier(name: str, body: ClassifierSpec, user: CurrentUser):
    """Remove a classifier rule from a resource group."""
    try:
        await resource_group_service.drop_classifier(name, body, **_caller(user))
    except ResourceGroupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "resource_group": name, "classifier": body.model_dump()}
