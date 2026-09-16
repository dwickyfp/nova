"""User and role administration API router.

Every endpoint here is a **security-administration** surface: the service layer
talks to StarRocks over ``_root_connect()`` (bypassing StarRocks RBAC entirely),
so the only thing standing between a logged-in user and ``DROP ROLE``,
``GRANT``, password resets and role membership changes is the check in this
module. Reads that expose the full user/role inventory are gated too — the
frontend hides the menu, but AGENTS.md is explicit that the frontend is not a
security boundary.

Access is granted to the StarRocks administrative roles only. Users holding
none of them get 403 from ``require_role`` before any SQL is built.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.deps import require_role
from app.core.exceptions import ForbiddenSQLError
from app.modules.users.schemas import (
    RoleCreate,
    RoleMemberChange,
    RolePrivilegeChange,
    UserAuthenticationResponse,
    UserCreate,
    UserDefaultRolesResponse,
    UserDetailResponse,
    UserListResponse,
    UserResetPasswordResponse,
    UserResponse,
    UserRoleAssign,
    UserUpdate,
)
from app.modules.users.service import user_service

router = APIRouter()

# Roles permitted to administer users and roles. ACCOUNTADMIN is the Nova super
# user; the rest are the StarRocks system roles that carry user/security
# administration privileges.
ADMIN_ROLES = ("ACCOUNTADMIN", "user_admin", "security_admin")

# Built once so routes can use a module-level dependency instead of calling
# `Depends(...)` in argument defaults (ruff B008).
_admin = require_role(*ADMIN_ROLES)
require_admin = Depends(_admin)


@router.get("/databases")
async def list_databases(user: dict = require_admin):
    try:
        databases = await user_service.list_databases()
        return {"databases": databases, "count": len(databases)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/databases/{db}/tables")
async def list_tables(db: str, user: dict = require_admin):
    try:
        tables = await user_service.list_tables(db)
        return {"database": db, "tables": tables, "count": len(tables)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=UserListResponse)
async def list_users(user: dict = require_admin):
    users = await user_service.list_users()
    return UserListResponse(
        users=[UserResponse(**entry) for entry in users],
        count=len(users),
    )


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(body: UserCreate, user: dict = require_admin):
    try:
        await user_service.create_user(
            username=body.username,
            password=body.password,
            host=body.host,
            granted_roles=body.granted_roles,
            default_role_mode=body.default_role_mode,
            default_roles=body.default_roles,
            max_user_connections=body.max_user_connections,
            catalog=body.catalog,
            database=body.database,
            session_properties=body.session_properties,
        )
        created = next(
            (
                entry
                for entry in await user_service.list_users()
                if entry["username"] == body.username and entry["host"] == body.host
            ),
            None,
        )
        if not created:
            created = {
                "username": body.username,
                "host": body.host,
                "identity": f"'{body.username}'@'{body.host}'",
                "is_protected": False,
                "roles": body.granted_roles,
                "default_roles": body.default_roles,
                "default_role_mode": body.default_role_mode,
                "auth_plugin": None,
                "auth_mode": "native_password",
                "password_enabled": True,
                "properties": {
                    "max_user_connections": "" if body.max_user_connections is None else str(body.max_user_connections),
                    "catalog": body.catalog or "",
                    "database": body.database or "",
                },
            }
        return UserResponse(**created)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{username}")
async def update_user(
    username: str,
    body: UserUpdate,
    host: str = Query("%"),
    user: dict = require_admin,
):
    try:
        executed = await user_service.update_user(
            username=username,
            host=host,
            password=body.password,
            granted_roles_add=body.granted_roles_add,
            granted_roles_remove=body.granted_roles_remove,
            default_role_mode=body.default_role_mode,
            default_roles=body.default_roles,
            max_user_connections=body.max_user_connections,
            catalog=body.catalog,
            database=body.database,
            session_properties=body.session_properties,
            clear_properties=body.clear_properties,
        )
        return {"username": username, "host": host, "updated": executed}
    except PermissionError as e:
        raise ForbiddenSQLError(str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{username}", status_code=204)
async def drop_user(
    username: str,
    host: str = Query("%"),
    user: dict = require_admin,
):
    try:
        await user_service.drop_user(username, host=host)
    except PermissionError as e:
        raise ForbiddenSQLError(str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{username}/grants")
async def get_user_grants(
    username: str,
    host: str = Query("%"),
    user: dict = require_admin,
):
    try:
        grants = await user_service.get_user_grants(username, host=host)
        return {"username": username, "host": host, "grants": grants, "count": len(grants)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{username}/detail", response_model=UserDetailResponse)
async def get_user_detail(
    username: str,
    host: str = Query("%"),
    user: dict = require_admin,
):
    try:
        return UserDetailResponse(**await user_service.get_user_detail(username, host=host))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{username}/properties")
async def get_user_properties(
    username: str,
    user: dict = require_admin,
):
    try:
        properties = await user_service.get_user_properties(username)
        return {"username": username, "properties": properties}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{username}/authentication", response_model=UserAuthenticationResponse)
async def get_user_authentication(
    username: str,
    host: str = Query("%"),
    user: dict = require_admin,
):
    try:
        return UserAuthenticationResponse(**await user_service.get_user_authentication(username, host=host))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{username}/default-roles", response_model=UserDefaultRolesResponse)
async def get_user_default_roles(
    username: str,
    host: str = Query("%"),
    user: dict = require_admin,
):
    try:
        return UserDefaultRolesResponse(**await user_service.get_user_default_roles(username, host=host))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{username}/roles", status_code=201)
async def assign_role(
    username: str,
    body: UserRoleAssign,
    user: dict = require_admin,
):
    try:
        await user_service.assign_role(username, body.role, host=body.host)
        return {"message": f"Role '{body.role}' assigned to '{username}@{body.host}'"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{username}/reset-password", response_model=UserResetPasswordResponse)
async def reset_user_password(
    username: str,
    host: str = Query("%"),
    user: dict = require_admin,
):
    try:
        password = await user_service.reset_password(username, host=host)
        return UserResetPasswordResponse(
            username=username,
            host=host,
            password=password,
            message="Password reset successfully",
        )
    except PermissionError as e:
        raise ForbiddenSQLError(str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{username}/roles/{role}", status_code=204)
async def revoke_role(
    username: str,
    role: str,
    host: str = Query("%"),
    user: dict = require_admin,
):
    try:
        await user_service.revoke_role(username, role, host=host)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/roles")
async def list_roles(user: dict = require_admin):
    roles = await user_service.list_roles()
    return {"roles": roles, "count": len(roles)}


@router.post("/roles", status_code=201)
async def create_role(body: RoleCreate, user: dict = require_admin):
    try:
        await user_service.create_role(body.role_name)
        return {"message": f"Role '{body.role_name}' created", "role": body.role_name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/roles/{name}")
async def get_role_detail(name: str, user: dict = require_admin):
    try:
        return await user_service.get_role_detail(name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/roles/{name}", status_code=204)
async def drop_role(name: str, user: dict = require_admin):
    try:
        await user_service.drop_role(name)
    except PermissionError as e:
        raise ForbiddenSQLError(str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/roles/{name}/privileges")
async def get_role_privileges(name: str, user: dict = require_admin):
    try:
        privileges = await user_service.get_role_privileges(name)
        return {"role": name, "privileges": privileges, "count": len(privileges)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/roles/{name}/privileges", status_code=201)
async def grant_privilege(
    name: str,
    body: RolePrivilegeChange,
    user: dict = require_admin,
):
    try:
        sql = await user_service.grant_privilege(
            name=name,
            privilege=body.privilege,
            scope=body.scope,
            selector_mode=body.selector_mode,
            catalog=body.catalog,
            database=body.database,
            object_name=body.object_name,
            with_grant_option=body.with_grant_option,
        )
        return {"message": f"Privilege granted to role '{name}'", "sql": sql}
    except PermissionError as e:
        raise ForbiddenSQLError(str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/roles/{name}/privileges")
async def revoke_privilege(
    name: str,
    body: RolePrivilegeChange,
    user: dict = require_admin,
):
    try:
        sql = await user_service.revoke_privilege(
            name=name,
            privilege=body.privilege,
            scope=body.scope,
            selector_mode=body.selector_mode,
            catalog=body.catalog,
            database=body.database,
            object_name=body.object_name,
        )
        return {"message": f"Privilege revoked from role '{name}'", "sql": sql}
    except PermissionError as e:
        raise ForbiddenSQLError(str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/roles/{name}/members")
async def get_role_members(name: str, user: dict = require_admin):
    try:
        members = await user_service.get_role_members(name)
        return {"role": name, "members": members}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/roles/{name}/members", status_code=201)
async def grant_role_member(
    name: str,
    body: RoleMemberChange,
    user: dict = require_admin,
):
    try:
        if body.member_type == "user":
            await user_service.grant_role_to_member_user(name, body.member_name, host=body.host)
        else:
            await user_service.grant_role_to_role(name, body.member_name)
        return {"message": f"Member granted to role '{name}'"}
    except PermissionError as e:
        raise ForbiddenSQLError(str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/roles/{name}/members")
async def revoke_role_member(
    name: str,
    body: RoleMemberChange,
    user: dict = require_admin,
):
    try:
        if body.member_type == "user":
            await user_service.revoke_role_from_member_user(name, body.member_name, host=body.host)
        else:
            await user_service.revoke_role_from_role(name, body.member_name)
        return {"message": f"Member revoked from role '{name}'"}
    except PermissionError as e:
        raise ForbiddenSQLError(str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
