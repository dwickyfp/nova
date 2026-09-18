"""Data governance API router — masking policies + row access policies.

Endpoints under ``/api/v1/governance``:

  Masking policies
    GET    /masking-policies                          → list
    POST   /masking-policies                          → CREATE MASKING POLICY
    PATCH  /masking-policies/{name}                   → ALTER … SET BODY
    DELETE /masking-policies/{name}                   → DROP
    POST   /masking-policies/bindings                 → ALTER TABLE … SET/DROP MASKING POLICY

  Row access policies
    GET    /row-access-policies                       → list
    POST   /row-access-policies                       → CREATE ROW ACCESS POLICY
    PATCH  /row-access-policies/{name}                → ALTER … SET BODY
    DELETE /row-access-policies/{name}                → DROP
    POST   /row-access-policies/bindings              → ALTER TABLE … ADD ROW ACCESS POLICY
    DELETE /row-access-policies/bindings              → ALTER TABLE … DROP ROW ACCESS POLICY

Every endpoint runs as the caller through the shared SQL pipeline, so the
engine's ``SECURITY`` privilege is the authority. Responses use
``SanitizingJSONResponse`` as the last line of defence (AGENTS.md §2).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user

from .schemas import (
    ColumnBindingRequest,
    MaskingPolicyAlter,
    MaskingPolicyCreate,
    MaskingPolicyListResponse,
    MaskingPolicyResponse,
    RowAccessPolicyAlter,
    RowAccessPolicyCreate,
    RowAccessPolicyListResponse,
    RowAccessPolicyResponse,
    TableBindingRequest,
)
from .service import GovernanceError, governance_service

router = APIRouter()

CurrentUser = Annotated[dict, Depends(get_current_user)]


def _caller(user: dict) -> dict:
    return {
        "username": user["username"],
        "encrypted_password": user["encrypted_password"],
        "session_id": user.get("session_id"),
        "role": user.get("active_role"),
    }


# ── Masking policies ────────────────────────────────────────────


@router.get(
    "/masking-policies",
    response_model=MaskingPolicyListResponse,
    response_class=SanitizingJSONResponse,
)
async def list_masking_policies(user: CurrentUser):
    """List masking policies visible to the caller."""
    try:
        policies = await governance_service.list_masking_policies(**_caller(user))
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MaskingPolicyListResponse(
        policies=[MaskingPolicyResponse(**p) for p in policies],
        count=len(policies),
    )


@router.post(
    "/masking-policies",
    response_model=MaskingPolicyResponse,
    status_code=201,
    response_class=SanitizingJSONResponse,
)
async def create_masking_policy(body: MaskingPolicyCreate, user: CurrentUser):
    """Create a masking policy. Runs as the caller."""
    try:
        created = await governance_service.create_masking_policy(
            name=body.name,
            column_type=body.column_type,
            body=body.body,
            comment=body.comment,
            **_caller(user),
        )
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MaskingPolicyResponse(**created)


@router.patch(
    "/masking-policies/{name}",
    response_model=MaskingPolicyResponse,
    response_class=SanitizingJSONResponse,
)
async def alter_masking_policy(name: str, body: MaskingPolicyAlter, user: CurrentUser):
    """Replace a masking policy's body."""
    try:
        updated = await governance_service.alter_masking_policy(
            name, body=body.body, **_caller(user)
        )
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MaskingPolicyResponse(**updated)


@router.delete("/masking-policies/{name}", response_class=SanitizingJSONResponse)
async def drop_masking_policy(name: str, user: CurrentUser):
    """Drop a masking policy."""
    try:
        await governance_service.drop_masking_policy(name, **_caller(user))
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "message": f"Masking policy '{name}' dropped"}


@router.post("/masking-policies/bindings", response_class=SanitizingJSONResponse)
async def bind_masking_policy(body: ColumnBindingRequest, user: CurrentUser):
    """Bind a masking policy to a column, or unbind when ``policy_name`` is null."""
    try:
        binding = await governance_service.bind_masking_policy(
            database=body.database,
            table=body.table,
            column=body.column,
            policy_name=body.policy_name,
            **_caller(user),
        )
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "binding": binding}


# ── Row access policies ─────────────────────────────────────────


@router.get(
    "/row-access-policies",
    response_model=RowAccessPolicyListResponse,
    response_class=SanitizingJSONResponse,
)
async def list_row_access_policies(user: CurrentUser):
    """List row access policies visible to the caller."""
    try:
        policies = await governance_service.list_row_access_policies(**_caller(user))
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RowAccessPolicyListResponse(
        policies=[RowAccessPolicyResponse(**p) for p in policies],
        count=len(policies),
    )


@router.post(
    "/row-access-policies",
    response_model=RowAccessPolicyResponse,
    status_code=201,
    response_class=SanitizingJSONResponse,
)
async def create_row_access_policy(body: RowAccessPolicyCreate, user: CurrentUser):
    """Create a row access policy. Runs as the caller."""
    try:
        created = await governance_service.create_row_access_policy(
            name=body.name,
            argument_name=body.argument_name,
            argument_type=body.argument_type,
            body=body.body,
            comment=body.comment,
            **_caller(user),
        )
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RowAccessPolicyResponse(**created)


@router.patch(
    "/row-access-policies/{name}",
    response_model=RowAccessPolicyResponse,
    response_class=SanitizingJSONResponse,
)
async def alter_row_access_policy(
    name: str, body: RowAccessPolicyAlter, user: CurrentUser
):
    """Replace a row access policy's body."""
    try:
        updated = await governance_service.alter_row_access_policy(
            name, body=body.body, **_caller(user)
        )
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RowAccessPolicyResponse(**updated)


@router.delete("/row-access-policies/{name}", response_class=SanitizingJSONResponse)
async def drop_row_access_policy(name: str, user: CurrentUser):
    """Drop a row access policy."""
    try:
        await governance_service.drop_row_access_policy(name, **_caller(user))
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "message": f"Row access policy '{name}' dropped"}


@router.post("/row-access-policies/bindings", response_class=SanitizingJSONResponse)
async def bind_row_access_policy(body: TableBindingRequest, user: CurrentUser):
    """Bind a row access policy to a table column."""
    try:
        binding = await governance_service.bind_row_access_policy(
            database=body.database,
            table=body.table,
            column=body.column,
            policy_name=body.policy_name,
            **_caller(user),
        )
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "binding": binding}


@router.delete("/row-access-policies/bindings", response_class=SanitizingJSONResponse)
async def unbind_row_access_policy(
    database: str,
    table: str,
    policy_name: str,
    user: CurrentUser,
):
    """Remove a row access policy binding from a table."""
    try:
        binding = await governance_service.unbind_row_access_policy(
            database=database,
            table=table,
            policy_name=policy_name,
            **_caller(user),
        )
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "binding": binding}
