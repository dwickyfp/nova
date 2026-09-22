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

from app.core.config import settings
from app.core.exceptions import ForbiddenSQLError
from app.core.role_gates import require_active_role
from app.modules.access_control.security_context import SecurityContext
from app.modules.access_control.service import AccessControlError, access_control_service
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
ADMIN_ROLES = ("ACCOUNTADMIN", "SECURITYADMIN", "user_admin", "security_admin")

# Built once so routes can use a module-level dependency instead of calling
# `Depends(...)` in argument defaults (ruff B008).
_admin = require_active_role(*ADMIN_ROLES)
require_admin = Depends(_admin)


def _security(user: dict) -> SecurityContext:
    return SecurityContext.from_session(user)


def _ranger_table(body: RolePrivilegeChange) -> tuple[str, str]:
    if (
        body.scope != "TABLE"
        or body.selector_mode != "specific"
        or not body.database
        or not body.object_name
        or body.with_grant_option
    ):
        raise AccessControlError(
            "This resource selector is unsupported by the Ranger capability registry"
        )
    return body.database, body.object_name


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
            granted_roles=[] if settings.RANGER_ENABLED else body.granted_roles,
            default_role_mode="none" if settings.RANGER_ENABLED else body.default_role_mode,
            default_roles=[] if settings.RANGER_ENABLED else body.default_roles,
            max_user_connections=body.max_user_connections,
            catalog=body.catalog,
            database=body.database,
            session_properties=body.session_properties,
        )
        if settings.RANGER_ENABLED:
            assigned: list[str] = []
            try:
                for role in body.granted_roles:
                    await access_control_service.assign_role(
                        _security(user), role=role, username=body.username, host=body.host
                    )
                    assigned.append(role)
                await user_service.set_default_roles(
                    body.username, body.host, body.default_role_mode, body.default_roles
                )
            except Exception:
                for role in reversed(assigned):
                    await access_control_service.revoke_role(
                        _security(user), role=role, username=body.username, host=body.host
                    )
                await user_service.drop_user(body.username, host=body.host)
                raise
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
        if not settings.RANGER_ENABLED:
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
        else:
            executed = []
            for role in body.granted_roles_add:
                await access_control_service.assign_role(
                    _security(user), role=role, username=username, host=host
                )
                executed.append(f"GRANT ROLE {role}")
            for role in body.granted_roles_remove:
                await access_control_service.revoke_role(
                    _security(user), role=role, username=username, host=host
                )
                executed.append(f"REVOKE ROLE {role}")
            has_marker_update = any(
                (
                    body.password is not None,
                    body.default_role_mode is not None,
                    body.max_user_connections is not None,
                    body.catalog is not None,
                    body.database is not None,
                    bool(body.session_properties),
                    bool(body.clear_properties),
                )
            )
            if has_marker_update:
                executed.extend(
                    await user_service.update_user(
                        username=username,
                        host=host,
                        password=body.password,
                        granted_roles_add=[],
                        granted_roles_remove=[],
                        default_role_mode=body.default_role_mode,
                        default_roles=body.default_roles,
                        max_user_connections=body.max_user_connections,
                        catalog=body.catalog,
                        database=body.database,
                        session_properties=body.session_properties,
                        clear_properties=body.clear_properties,
                    )
                )
            if not executed:
                raise ValueError("No update fields provided")
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
        if not settings.RANGER_ENABLED:
            await user_service.drop_user(username, host=host)
        else:
            detail = await user_service.get_user_detail(username, host=host)
            roles = list(detail.get("roles", []))
            revoked: list[str] = []
            try:
                for role in roles:
                    await access_control_service.revoke_role(
                        _security(user), role=role, username=username, host=host
                    )
                    revoked.append(role)
                await user_service.drop_user(username, host=host)
            except Exception:
                for role in revoked:
                    await access_control_service.assign_role(
                        _security(user), role=role, username=username, host=host
                    )
                raise
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
        if settings.RANGER_ENABLED:
            await access_control_service.assign_role(
                _security(user), role=body.role, username=username, host=body.host
            )
        else:
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
        if settings.RANGER_ENABLED:
            await access_control_service.revoke_role(
                _security(user), role=role, username=username, host=host
            )
        else:
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
        if settings.RANGER_ENABLED:
            await access_control_service.create_role(_security(user), body.role_name)
        else:
            await user_service.create_role(body.role_name)
        return {"message": f"Role '{body.role_name}' created", "role": body.role_name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/roles/{name}")
async def get_role_detail(name: str, user: dict = require_admin):
    try:
        detail = await user_service.get_role_detail(name)
        if settings.RANGER_ENABLED:
            detail["privileges"] = await access_control_service.role_privileges(name)
            detail["grants"] = [
                f"RANGER POLICY {item['POLICY_NAME']}"
                for item in detail["privileges"]
            ]
        return detail
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/roles/{name}", status_code=204)
async def drop_role(name: str, user: dict = require_admin):
    try:
        if settings.RANGER_ENABLED:
            await access_control_service.drop_role(_security(user), name)
        else:
            await user_service.drop_role(name)
    except PermissionError as e:
        raise ForbiddenSQLError(str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/roles/{name}/privileges")
async def get_role_privileges(name: str, user: dict = require_admin):
    try:
        privileges = (
            await access_control_service.role_privileges(name)
            if settings.RANGER_ENABLED
            else await user_service.get_role_privileges(name)
        )
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
        if settings.RANGER_ENABLED:
            database, table = _ranger_table(body)
            await access_control_service.grant_access(
                _security(user),
                role=name,
                catalog=body.catalog or "default_catalog",
                database=database,
                table=table,
                accesses=[body.privilege],
            )
            sql = "RANGER POLICY UPDATE"
        else:
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
        if settings.RANGER_ENABLED:
            database, table = _ranger_table(body)
            await access_control_service.revoke_access(
                _security(user),
                role=name,
                catalog=body.catalog or "default_catalog",
                database=database,
                table=table,
                accesses=[body.privilege],
            )
            sql = "RANGER POLICY UPDATE"
        else:
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
        if settings.RANGER_ENABLED and body.member_type == "user":
            await access_control_service.assign_role(
                _security(user), role=name, username=body.member_name, host=body.host
            )
        elif settings.RANGER_ENABLED:
            await access_control_service.grant_role_to_role(
                _security(user), parent_role=name, member_role=body.member_name
            )
        elif body.member_type == "user":
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
        if settings.RANGER_ENABLED and body.member_type == "user":
            await access_control_service.revoke_role(
                _security(user), role=name, username=body.member_name, host=body.host
            )
        elif settings.RANGER_ENABLED:
            await access_control_service.revoke_role_from_role(
                _security(user), parent_role=name, member_role=body.member_name
            )
        elif body.member_type == "user":
            await user_service.revoke_role_from_member_user(name, body.member_name, host=body.host)
        else:
            await user_service.revoke_role_from_role(name, body.member_name)
        return {"message": f"Member revoked from role '{name}'"}
    except PermissionError as e:
        raise ForbiddenSQLError(str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
