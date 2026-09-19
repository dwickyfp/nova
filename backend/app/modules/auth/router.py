"""Auth API router — login, logout, setup, session info."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.common.user_flags import is_must_change_password
from app.core.deps import get_current_user
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    SessionInfo,
    SetupRequest,
    SetupResponse,
    SwitchRoleRequest,
)
from app.modules.auth.service import auth_service

router = APIRouter()

#: ``Annotated`` dependency alias (the tree's convention; a ``Depends()`` in an
#: argument default trips ruff B008).
CurrentUser = Annotated[dict, Depends(get_current_user)]


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """Authenticate against StarRocks and create a session.

    Returns JWT token + user info. Status may be SETUP_REQUIRED on first login.
    """
    result = await auth_service.login(req.username, req.password)
    return LoginResponse(**result)


@router.post("/setup", response_model=SetupResponse)
async def setup(
    req: SetupRequest,
    user: CurrentUser,
):
    """First-login setup: change the nova_admin password.

    Only works before setup is marked complete.
    """
    result = await auth_service.setup(
        username=user["username"],
        session_id=user["session_id"],
        new_password=req.new_password,
        confirm_password=req.confirm_password,
    )
    return SetupResponse(**result)


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    user: CurrentUser,
):
    """Change password for the authenticated user."""
    result = await auth_service.change_password(
        username=user["username"],
        current_password=req.current_password,
        new_password=req.new_password,
        confirm_password=req.confirm_password,
    )
    return result


@router.post("/logout")
async def logout(user: CurrentUser):
    """Delete the current session."""
    await auth_service.logout(user["session_id"])
    return {"status": "LOGGED_OUT"}


@router.get("/me", response_model=SessionInfo)
async def get_me(user: CurrentUser):
    """Get current session info.

    ``must_change_password`` is read here too, so a reload after a forced-change
    login cannot skip the requirement by going straight to a route.
    """
    return SessionInfo(
        username=user["username"],
        roles=user["roles"],
        active_role=user.get("active_role"),
        session_id=user["session_id"],
        must_change_password=await is_must_change_password(user["username"]),
    )


@router.post("/switch-role")
async def switch_role(
    req: SwitchRoleRequest,
    user: CurrentUser,
):
    """Switch active role in the current session."""
    result = await auth_service.switch_role(user["session_id"], req.role)
    return {
        "username": user["username"],
        "roles": result["roles"],
        "active_role": result["active_role"],
        "session_id": user["session_id"],
    }
