"""HTTP surface of the Superset bridge.

``router`` is public (Nova JWT): it opens a BI session for the signed-in user
and lets ``ACCOUNTADMIN`` choose which roles author in Superset.
``internal_router`` is called only by Superset's own services: it trades a BI
handle for the caller's identity, or for a data token for the MySQL proxy.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.common.audit import write_audit_log
from app.core.deps import get_current_user
from app.core.role_gates import require_active_role
from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.access_control.service import AccessControlError, access_control_service

from . import tokens
from .access import (
    ADMIN_ROLE,
    author_roles,
    replace_author_roles,
    require_superset_service,
    superset_role_for,
    validate_role_name,
)

logger = logging.getLogger(__name__)

#: Where Superset is served. The BI cookie is scoped to it and nothing else.
BI_PATH = "/bi"
BI_COOKIE = "nova_bi"

router = APIRouter()
internal_router = APIRouter(dependencies=[Depends(require_superset_service)])
CurrentUser = Annotated[dict, Depends(get_current_user)]
AccountAdmin = Annotated[dict, Depends(require_active_role(ADMIN_ROLE))]


class BiSessionResponse(BaseModel):
    path: str
    superset_role: str


class AuthorRoles(BaseModel):
    roles: list[str] = Field(default_factory=list, max_length=256)


class HandleRequest(BaseModel):
    handle: str = Field(min_length=16, max_length=256)


class DelegateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)


class DelegateResponse(BaseModel):
    handle: str
    username: str
    active_role: str
    superset_role: str
    expires_in: int


class AuditRequest(HandleRequest):
    kind: Literal["BI_CHANGE", "BI_EXPORT"]
    action: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.]+$")
    objects: list[str] = Field(default_factory=list, max_length=8)


class IdentityResponse(BaseModel):
    username: str
    active_role: str
    superset_role: str
    security_context_version: int
    delegated: bool = False


class DataTokenResponse(BaseModel):
    username: str
    token: str
    expires_in: int


def _secure(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded.split(",")[0].strip() == "https"


@router.post("/session", response_model=BiSessionResponse)
async def open_bi_session(request: Request, response: Response, user: CurrentUser):
    """Issue the HttpOnly ``/bi`` cookie that signs this session into Superset."""
    try:
        handle = await tokens.issue_handle(user["session_id"])
    except tokens.BiCredentialError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    active_role = user.get("active_role") or ""
    superset_role = await superset_role_for(active_role)
    response.set_cookie(
        BI_COOKIE,
        handle,
        path=BI_PATH,
        httponly=True,
        secure=_secure(request),
        samesite="strict",
    )
    await write_audit_log(
        event_type="bi_session",
        user_name=user["username"],
        action="OPEN_BI_SESSION",
        object_type="SUPERSET",
        object_name=superset_role,
        status="SUCCESS",
        session_id=user["session_id"],
        active_role=active_role,
        security_context_version=user.get("security_context_version"),
    )
    return BiSessionResponse(path=f"{BI_PATH}/", superset_role=superset_role)


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def close_bi_session(
    response: Response,
    user: CurrentUser,
    nova_bi: Annotated[str | None, Cookie()] = None,
):
    del user
    if nova_bi:
        await tokens.revoke_handle(nova_bi)
    response.delete_cookie(BI_COOKIE, path=BI_PATH)


@router.get("/author-roles", response_model=AuthorRoles)
async def get_author_roles(user: AccountAdmin):
    del user
    return AuthorRoles(roles=sorted(await author_roles(fresh=True)))


@router.put("/author-roles", response_model=AuthorRoles)
async def put_author_roles(body: AuthorRoles, user: AccountAdmin):
    """Replace the BI author roles and their table-reflection grant together.

    The Ranger policy is written first: if it fails, the author list is left
    unchanged rather than naming authors who cannot build datasets.
    """
    try:
        roles = sorted({validate_role_name(role) for role in body.roles})
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    try:
        security = SecurityContext.from_session(user)
        await access_control_service.put_bi_metadata_access(security, roles)
    except (SecurityContextError, AccessControlError) as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    roles = await replace_author_roles(roles, changed_by=user["username"])
    await write_audit_log(
        event_type="bi_author_roles",
        user_name=user["username"],
        action="SET_BI_AUTHOR_ROLES",
        object_type="SUPERSET",
        object_name=",".join(roles) or "-",
        status="SUCCESS",
        session_id=user["session_id"],
        active_role=user.get("active_role"),
        security_context_version=user.get("security_context_version"),
    )
    return AuthorRoles(roles=roles)


async def _session_or_401(handle: str) -> tokens.BiSession:
    try:
        return await tokens.session_for_handle(handle)
    except tokens.BiCredentialError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


@internal_router.post("/identity", response_model=IdentityResponse)
async def identity(body: HandleRequest):
    """Who the BI handle belongs to right now, and their Superset role."""
    bi_session = await _session_or_401(body.handle)
    return IdentityResponse(
        username=bi_session.username,
        active_role=bi_session.active_role,
        superset_role=await superset_role_for(bi_session.active_role),
        security_context_version=bi_session.security_context_version,
        delegated=bi_session.delegated,
    )


@internal_router.post("/token", response_model=DataTokenResponse)
async def data_token(body: HandleRequest):
    """A short-lived proxy password bound to the handle's session and role."""
    bi_session = await _session_or_401(body.handle)
    issued = await tokens.mint_data_token(bi_session)
    return DataTokenResponse(
        username=issued.username, token=issued.token, expires_in=issued.expires_in
    )


@internal_router.post("/audit", status_code=status.HTTP_200_OK)
async def audit(body: AuditRequest):
    """Record a governed Superset action for the handle's principal (S12).

    Only the action name and object ids are stored: Superset sends no SQL,
    rows or parameters on this path.
    """
    bi_session = await _session_or_401(body.handle)
    objects = ",".join(item[:64] for item in body.objects if item)[:512]
    await write_audit_log(
        event_type="bi_action",
        user_name=bi_session.username,
        action=body.kind,
        object_type="SUPERSET",
        object_name=f"{body.action}{':' if objects else ''}{objects}",
        status="SUCCESS",
        session_id=bi_session.session_id,
        active_role=bi_session.active_role,
        security_context_version=bi_session.security_context_version,
    )
    return {"recorded": True}


@internal_router.post("/delegate", response_model=DelegateResponse)
async def delegate(body: DelegateRequest):
    """A BI session for one background run as ``username`` (S5).

    Granted only when the worker account can impersonate the owner right now;
    the session runs under the owner's current default role. Refusals are
    audited like grants.
    """
    from app.core.config import settings

    from .delegation import DelegationError, authorize

    try:
        role = await authorize(body.username)
    except DelegationError as exc:
        await write_audit_log(
            event_type="bi_delegation",
            user_name=body.username[:128],
            action="DELEGATE_BI_SESSION",
            object_type="SUPERSET",
            object_name=body.username[:128],
            status="DENIED",
            error_message=str(exc)[:500],
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    handle = await tokens.issue_delegated_handle(body.username, role)
    await write_audit_log(
        event_type="bi_delegation",
        user_name=body.username,
        action="DELEGATE_BI_SESSION",
        object_type="SUPERSET",
        object_name=body.username,
        status="SUCCESS",
        active_role=role,
    )
    return DelegateResponse(
        handle=handle,
        username=body.username,
        active_role=role,
        superset_role=await superset_role_for(role),
        expires_in=settings.SUPERSET_DELEGATION_TTL_SECONDS,
    )
