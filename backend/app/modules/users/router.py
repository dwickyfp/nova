"""User and role administration API router."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.deps import get_current_user
from app.core.exceptions import ForbiddenSQLError
from app.modules.users.schemas import (
    RoleCreate,
    RoleMemberChange,
    RolePrivilegeChange,
    UserAuthenticationResponse,
    UserCreate,
    UserDefaultRolesResponse,
    UserListResponse,
    UserResetPasswordResponse,
    UserResponse,
    UserRoleAssign,
    UserUpdate,
)
from app.modules.users.service import user_service

router = APIRouter()


@router.get("/databases")
async def list_databases(user: dict = Depends(get_current_user)):
    try:
        databases = await user_service.list_databases()
        return {"databases": databases, "count": len(databases)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/databases/{db}/tables")
async def list_tables(db: str, user: dict = Depends(get_current_user)):
    try:
        tables = await user_service.list_tables(db)
        return {"database": db, "tables": tables, "count": len(tables)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=UserListResponse)
async def list_users(user: dict = Depends(get_current_user)):
    users = await user_service.list_users()
    return UserListResponse(
        users=[UserResponse(**entry) for entry in users],
        count=len(users),
    )


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(body: UserCreate, user: dict = Depends(get_current_user)):
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
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
):
    try:
        grants = await user_service.get_user_grants(username, host=host)
        return {"username": username, "host": host, "grants": grants, "count": len(grants)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{username}/properties")
async def get_user_properties(
    username: str,
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
):
    try:
        return UserAuthenticationResponse(**await user_service.get_user_authentication(username, host=host))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{username}/default-roles", response_model=UserDefaultRolesResponse)
async def get_user_default_roles(
    username: str,
    host: str = Query("%"),
    user: dict = Depends(get_current_user),
):
    try:
        return UserDefaultRolesResponse(**await user_service.get_user_default_roles(username, host=host))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{username}/roles", status_code=201)
async def assign_role(
    username: str,
    body: UserRoleAssign,
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
):
    try:
        await user_service.revoke_role(username, role, host=host)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/roles")
async def list_roles(user: dict = Depends(get_current_user)):
    roles = await user_service.list_roles()
    return {"roles": roles, "count": len(roles)}


@router.post("/roles", status_code=201)
async def create_role(body: RoleCreate, user: dict = Depends(get_current_user)):
    try:
        await user_service.create_role(body.role_name)
        return {"message": f"Role '{body.role_name}' created", "role": body.role_name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/roles/{name}")
async def get_role_detail(name: str, user: dict = Depends(get_current_user)):
    try:
        return await user_service.get_role_detail(name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/roles/{name}", status_code=204)
async def drop_role(name: str, user: dict = Depends(get_current_user)):
    try:
        await user_service.drop_role(name)
    except PermissionError as e:
        raise ForbiddenSQLError(str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/roles/{name}/privileges")
async def get_role_privileges(name: str, user: dict = Depends(get_current_user)):
    try:
        privileges = await user_service.get_role_privileges(name)
        return {"role": name, "privileges": privileges, "count": len(privileges)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/roles/{name}/privileges", status_code=201)
async def grant_privilege(
    name: str,
    body: RolePrivilegeChange,
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
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
async def get_role_members(name: str, user: dict = Depends(get_current_user)):
    try:
        members = await user_service.get_role_members(name)
        return {"role": name, "members": members}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/roles/{name}/members", status_code=201)
async def grant_role_member(
    name: str,
    body: RoleMemberChange,
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
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
