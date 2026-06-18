"""User Management API router — user and role CRUD endpoints.

Endpoints under /api/v1/users:
  GET    /users                       → list all users with roles
  POST   /users                      → create user
  DELETE /users/{username}           → drop user (guard rails)
  GET    /users/{username}/grants    → get grants for user
  POST   /users/{username}/roles     → assign role to user
  DELETE /users/{username}/roles/{role} → revoke role from user
  GET    /roles                      → list all roles
  POST   /roles                     → create role
  DELETE /roles/{name}              → drop role (guard: ACCOUNTADMIN)
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user
from app.core.exceptions import ForbiddenSQLError
from app.modules.users.schemas import (
    UserCreate,
    UserListResponse,
    UserResponse,
    UserRoleAssign,
)
from app.modules.users.service import user_service

router = APIRouter()


# ── Users ────────────────────────────────────────────────────────


@router.get("/users", response_model=UserListResponse)
async def list_users(
    user: dict = Depends(get_current_user),
):
    """List all StarRocks users with their granted roles."""
    users = await user_service.list_users()
    user_responses = [UserResponse(**u) for u in users]
    return UserListResponse(users=user_responses, count=len(user_responses))


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    body: UserCreate,
    user: dict = Depends(get_current_user),
):
    """Create a new StarRocks user. Optionally assign an initial role."""
    try:
        await user_service.create_user(body.username, body.password)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Assign initial role if provided
    if body.role:
        try:
            await user_service.assign_role(body.username, body.role)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"User created but role assignment failed: {e}",
            )

    # Return the created user with roles
    roles = [body.role] if body.role else []
    return UserResponse(username=body.username, roles=roles)


@router.delete("/users/{username}", status_code=204)
async def drop_user(
    username: str,
    user: dict = Depends(get_current_user),
):
    """Drop a StarRocks user. Guard rails prevent dropping root/nova_admin."""
    try:
        await user_service.drop_user(username)
    except PermissionError as e:
        raise ForbiddenSQLError(str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/users/{username}/grants")
async def get_user_grants(
    username: str,
    user: dict = Depends(get_current_user),
):
    """Get SHOW GRANTS output for a specific user."""
    try:
        grants = await user_service.get_user_grants(username)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"username": username, "grants": grants, "count": len(grants)}


@router.post("/users/{username}/roles", status_code=201)
async def assign_role(
    username: str,
    body: UserRoleAssign,
    user: dict = Depends(get_current_user),
):
    """Assign a role to a user."""
    try:
        await user_service.assign_role(username, body.role)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": f"Role '{body.role}' assigned to '{username}'"}


@router.delete("/users/{username}/roles/{role}", status_code=204)
async def revoke_role(
    username: str,
    role: str,
    user: dict = Depends(get_current_user),
):
    """Revoke a role from a user."""
    try:
        await user_service.revoke_role(username, role)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Roles ────────────────────────────────────────────────────────


@router.get("/roles")
async def list_roles(
    user: dict = Depends(get_current_user),
):
    """List all StarRocks roles."""
    roles = await user_service.list_roles()
    return {"roles": roles, "count": len(roles)}


@router.post("/roles", status_code=201)
async def create_role(
    body: UserRoleAssign,
    user: dict = Depends(get_current_user),
):
    """Create a new StarRocks role. Uses UserRoleAssign with role field as name."""
    try:
        await user_service.create_role(body.role)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": f"Role '{body.role}' created"}


@router.delete("/roles/{name}", status_code=204)
async def drop_role(
    name: str,
    user: dict = Depends(get_current_user),
):
    """Drop a StarRocks role. ACCOUNTADMIN is protected and cannot be dropped."""
    try:
        await user_service.drop_role(name)
    except PermissionError as e:
        raise ForbiddenSQLError(str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
