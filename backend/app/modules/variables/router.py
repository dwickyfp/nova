"""Variables & settings API router.

Endpoints under ``/api/v1/variables``:
  GET  /                  → browse ``SHOW [GLOBAL] VARIABLES`` (search + page)
  POST /set               → ``SET [SESSION|GLOBAL] <name> = <value|DEFAULT>``
  GET  /password-policy   → the documented password-policy variables (global)
  POST /password-policy   → ``SET GLOBAL`` for the supplied policy fields

``SET`` runs as the caller through the shared pipeline, so the engine's
``GLOBAL`` privilege is the authority and the mutation is audited.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user

from .schemas import (
    PasswordPolicy,
    VariableListResponse,
    VariableScope,
    VariableSetRequest,
    VariableSetResponse,
)
from .service import VariableError, variable_service

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
    response_model=VariableListResponse,
    response_class=SanitizingJSONResponse,
)
async def list_variables(
    user: CurrentUser,
    scope: VariableScope = VariableScope.SESSION,
    search: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Browse session or global variables, with search and pagination."""
    try:
        page = await variable_service.list_variables(
            scope=scope,
            search=search,
            limit=limit,
            offset=offset,
            **_caller(user),
        )
    except VariableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return VariableListResponse(scope=scope, **page)


@router.post(
    "/set",
    response_model=VariableSetResponse,
    response_class=SanitizingJSONResponse,
)
async def set_variable(body: VariableSetRequest, user: CurrentUser):
    """``SET`` a session or global variable, or reset it to its default."""
    try:
        result = await variable_service.set_variable(body, **_caller(user))
    except VariableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return VariableSetResponse(**result)


@router.get("/password-policy", response_class=SanitizingJSONResponse)
async def get_password_policy(user: CurrentUser):
    """Read the documented password-policy variables from global scope."""
    try:
        policy = await variable_service.get_password_policy(**_caller(user))
    except VariableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"policy": policy}


@router.post("/password-policy", response_class=SanitizingJSONResponse)
async def set_password_policy(body: PasswordPolicy, user: CurrentUser):
    """Write the supplied password-policy fields, each as ``SET GLOBAL``."""
    try:
        result = await variable_service.set_password_policy(body, **_caller(user))
    except VariableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result
