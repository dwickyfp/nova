"""Auth API router — login, logout, setup, session info."""

from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.core.redis import session_store
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    SessionInfo,
    SetupRequest,
    SetupResponse,
)
from app.modules.auth.service import auth_service

router = APIRouter()


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
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
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
async def logout(user: dict = Depends(get_current_user)):
    """Delete the current session."""
    await auth_service.logout(user["session_id"])
    return {"status": "LOGGED_OUT"}


@router.get("/me", response_model=SessionInfo)
async def get_me(user: dict = Depends(get_current_user)):
    """Get current session info."""
    return SessionInfo(
        username=user["username"],
        roles=user["roles"],
        session_id=user["session_id"],
    )
