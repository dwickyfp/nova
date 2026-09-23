"""Central management service for Ranger-backed Nova authorization."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from app.common.audit import write_audit_log
from app.common.identifiers import check_identifier
from app.core.config import settings
from app.core.database import db
from app.integrations.ranger.client import RangerClient, RangerError, ranger_client
from app.integrations.ranger.compiler import (
    compile_access_policy,
    compile_mask_policy,
    compile_row_filter_policy,
    role_scope_attribute,
)
from app.integrations.ranger.schemas import RangerRole, RangerRoleMember

from .domain import ProjectionState, RoleProjection
from .security_context import SecurityContext


class AccessControlError(RuntimeError):
    pass


class AccessControlService:
    """The only write path for marker roles and Ranger authorization state."""

    SECURITY_ADMIN_ROLES = frozenset({"ACCOUNTADMIN", "SECURITYADMIN", "security_admin"})

    def __init__(self, ranger: RangerClient | None = None) -> None:
        self._ranger = ranger or ranger_client

    @staticmethod
    def require_security_admin(security: SecurityContext) -> None:
        if security.active_role not in AccessControlService.SECURITY_ADMIN_ROLES:
            raise AccessControlError(
                "The active role is not authorized to administer access control"
            )

    @staticmethod
    async def _audit_admin(
        security: SecurityContext,
        *,
        action: str,
        object_type: str,
        object_name: str,
        ranger_ids: list[int | str] | None = None,
    ) -> None:
        await write_audit_log(
            event_type="security_admin",
            user_name=security.principal,
            action=action,
            object_type=object_type,
            object_name=object_name,
            status="SUCCESS",
            session_id=security.session_id,
            active_role=security.active_role,
            security_context_version=security.security_context_version,
            decision="ALLOW",
            ranger_policy_ids=json.dumps(ranger_ids or []),
        )

    async def _record_projection(
        self, name: str, state: ProjectionState, message: str | None = None
    ) -> None:
        await db.execute_system(
            """
            INSERT INTO NOVA_SYSTEM.CONFIG_SECURITY_ROLE_PROJECTIONS
                (role_name, state, marker_exists, ranger_exists, message, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                name,
                state.value,
                state in {ProjectionState.ACTIVE, ProjectionState.PROPAGATING},
                state in {ProjectionState.ACTIVE, ProjectionState.PROPAGATING},
                message,
                datetime.now(UTC).replace(tzinfo=None),
            ],
        )

    async def create_role(
        self, security: SecurityContext, name: str, description: str = ""
    ) -> RoleProjection:
        self.require_security_admin(security)
        safe_name = check_identifier(name, field="role")
        if safe_name.upper() == "ACCOUNTADMIN":
            raise AccessControlError("ACCOUNTADMIN is immutable")
        await self._record_projection(name, ProjectionState.PROVISIONING)
        ranger_created = False
        try:
            await self._ranger.put_role(
                RangerRole(name=name, description=description or "Managed by Nova")
            )
            ranger_created = True
            await db.execute_system(f"CREATE ROLE IF NOT EXISTS {safe_name}")
            await self._record_projection(name, ProjectionState.ACTIVE)
            await self._audit_admin(
                security, action="CREATE_ROLE", object_type="ROLE", object_name=name
            )
            return RoleProjection(name, ProjectionState.ACTIVE, True, True)
        except Exception as exc:
            if ranger_created:
                try:
                    await self._ranger.delete_role(name)
                except Exception:
                    await self._record_projection(name, ProjectionState.DRIFTED, str(exc))
                    raise AccessControlError(
                        "Role provisioning partially failed and requires reconciliation"
                    ) from exc
            await self._record_projection(name, ProjectionState.ERROR, str(exc))
            raise AccessControlError("Role provisioning failed") from exc

    async def drop_role(self, security: SecurityContext, name: str) -> None:
        self.require_security_admin(security)
        if name.upper() == "ACCOUNTADMIN":
            raise AccessControlError("ACCOUNTADMIN is immutable")
        safe_name = check_identifier(name, field="role")
        await self._record_projection(name, ProjectionState.DELETING)
        ranger_role = await self._ranger.get_role(name)
        await self._ranger.delete_role(name)
        try:
            await db.execute_system(f"DROP ROLE IF EXISTS {safe_name}")
        except Exception as exc:
            if ranger_role:
                try:
                    await self._ranger.put_role(RangerRole.model_validate(ranger_role))
                except Exception:
                    await self._record_projection(name, ProjectionState.DRIFTED, str(exc))
                    raise AccessControlError(
                        "Role deletion partially failed and requires reconciliation"
                    ) from exc
            await self._record_projection(name, ProjectionState.ERROR, str(exc))
            raise AccessControlError("Role deletion failed") from exc
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_SECURITY_ROLE_PROJECTIONS WHERE role_name = %s",
            [name],
        )
        await self._audit_admin(security, action="DROP_ROLE", object_type="ROLE", object_name=name)

    async def assign_role(
        self, security: SecurityContext, *, role: str, username: str, host: str = "%"
    ) -> None:
        self.require_security_admin(security)
        safe_role = check_identifier(role, field="role")
        safe_user = username.replace("'", "''")
        safe_host = host.replace("'", "''")
        await self._ranger.put_user_attributes(username, {})
        ranger_role = await self._ranger.get_role(role)
        if not ranger_role:
            raise AccessControlError("Role projection is not healthy")
        users = [dict(item) for item in ranger_role.get("users", [])]
        if not any(item.get("name") == username for item in users):
            users.append(RangerRoleMember(name=username).model_dump(by_alias=True))
        updated = RangerRole.model_validate({**ranger_role, "users": users})
        await self._ranger.put_role(updated)
        try:
            await db.execute_system(f"GRANT {safe_role} TO USER '{safe_user}'@'{safe_host}'")
        except Exception as exc:
            updated.users = [member for member in updated.users if member.name != username]
            await self._ranger.put_role(updated)
            raise AccessControlError("Role membership provisioning failed") from exc
        await self._audit_admin(
            security,
            action="GRANT_ROLE",
            object_type="ROLE_MEMBERSHIP",
            object_name=f"{role}:{username}",
        )

    async def revoke_role(
        self, security: SecurityContext, *, role: str, username: str, host: str = "%"
    ) -> None:
        self.require_security_admin(security)
        if role.upper() == "ACCOUNTADMIN":
            raise AccessControlError("ACCOUNTADMIN membership cannot be revoked here")
        safe_role = check_identifier(role, field="role")
        safe_user = username.replace("'", "''")
        safe_host = host.replace("'", "''")
        ranger_role = await self._ranger.get_role(role)
        if not ranger_role:
            raise AccessControlError("Role projection is not healthy")
        updated = RangerRole.model_validate(ranger_role)
        updated.users = [member for member in updated.users if member.name != username]
        await self._ranger.put_role(updated)
        try:
            await db.execute_system(f"REVOKE {safe_role} FROM USER '{safe_user}'@'{safe_host}'")
        except Exception as exc:
            await self._ranger.put_role(RangerRole.model_validate(ranger_role))
            raise AccessControlError("Role membership revocation failed") from exc
        await self._audit_admin(
            security,
            action="REVOKE_ROLE",
            object_type="ROLE_MEMBERSHIP",
            object_name=f"{role}:{username}",
        )

    async def grant_role_to_role(
        self, security: SecurityContext, *, parent_role: str, member_role: str
    ) -> None:
        self.require_security_admin(security)
        if "ACCOUNTADMIN" in {parent_role.upper(), member_role.upper()}:
            raise AccessControlError("ACCOUNTADMIN hierarchy is immutable")
        safe_parent = check_identifier(parent_role, field="parent role")
        safe_member = check_identifier(member_role, field="member role")
        parent = await self._ranger.get_role(parent_role)
        member = await self._ranger.get_role(member_role)
        if not parent or not member:
            raise AccessControlError("Both role projections must be healthy")
        previous = RangerRole.model_validate(parent)
        updated = previous.model_copy(deep=True)
        if not any(item.name == member_role for item in updated.roles):
            updated.roles.append(RangerRoleMember(name=member_role))
        await self._ranger.put_role(updated)
        try:
            await db.execute_system(f"GRANT {safe_member} TO ROLE {safe_parent}")
        except Exception as exc:
            await self._ranger.put_role(previous)
            raise AccessControlError("Role hierarchy provisioning failed") from exc
        await self._audit_admin(
            security,
            action="GRANT_ROLE_TO_ROLE",
            object_type="ROLE_HIERARCHY",
            object_name=f"{member_role}:{parent_role}",
        )

    async def revoke_role_from_role(
        self, security: SecurityContext, *, parent_role: str, member_role: str
    ) -> None:
        self.require_security_admin(security)
        if "ACCOUNTADMIN" in {parent_role.upper(), member_role.upper()}:
            raise AccessControlError("ACCOUNTADMIN hierarchy is immutable")
        safe_parent = check_identifier(parent_role, field="parent role")
        safe_member = check_identifier(member_role, field="member role")
        parent = await self._ranger.get_role(parent_role)
        if not parent:
            raise AccessControlError("Parent role projection is not healthy")
        previous = RangerRole.model_validate(parent)
        updated = previous.model_copy(deep=True)
        updated.roles = [item for item in updated.roles if item.name != member_role]
        await self._ranger.put_role(updated)
        try:
            await db.execute_system(f"REVOKE {safe_member} FROM ROLE {safe_parent}")
        except Exception as exc:
            await self._ranger.put_role(previous)
            raise AccessControlError("Role hierarchy revocation failed") from exc
        await self._audit_admin(
            security,
            action="REVOKE_ROLE_FROM_ROLE",
            object_type="ROLE_HIERARCHY",
            object_name=f"{member_role}:{parent_role}",
        )

    async def grant_access(
        self,
        security: SecurityContext,
        *,
        role: str,
        catalog: str,
        database: str,
        table: str,
        accesses: list[str],
    ) -> dict:
        self.require_security_admin(security)
        policy = compile_access_policy(
            role=role,
            catalog=catalog,
            database=database,
            table=table,
            accesses=accesses,
        )
        existing = await self._ranger.get_policy(policy.name)
        if existing:
            previous = {
                str(access.get("type", "")).lower()
                for item in existing.get("policyItems", [])
                for access in item.get("accesses", [])
                if access.get("isAllowed", True)
            }
            policy = compile_access_policy(
                role=role,
                catalog=catalog,
                database=database,
                table=table,
                accesses=sorted(previous | {value.lower() for value in accesses}),
            )
        result = await self._ranger.put_policy(policy)
        await self._record_policy_state(policy.name, result, ProjectionState.PROPAGATING)
        await self._audit_admin(
            security,
            action="GRANT_ACCESS",
            object_type="RANGER_POLICY",
            object_name=policy.name,
            ranger_ids=[result["id"]] if result.get("id") is not None else [],
        )
        return result

    async def revoke_access(
        self,
        security: SecurityContext,
        *,
        role: str,
        catalog: str,
        database: str,
        table: str,
        accesses: list[str],
    ) -> None:
        self.require_security_admin(security)
        desired = compile_access_policy(
            role=role,
            catalog=catalog,
            database=database,
            table=table,
            accesses=[],
        )
        existing = await self._ranger.get_policy(desired.name)
        if not existing:
            return
        removed = {value.lower() for value in accesses}
        remaining = {
            str(access.get("type", "")).lower()
            for item in existing.get("policyItems", [])
            for access in item.get("accesses", [])
            if access.get("isAllowed", True)
        } - removed
        if not remaining:
            await self._ranger.delete_policy(int(existing["id"]))
            await self._audit_admin(
                security,
                action="REVOKE_ACCESS",
                object_type="RANGER_POLICY",
                object_name=desired.name,
                ranger_ids=[existing["id"]],
            )
            return
        policy = compile_access_policy(
            role=role,
            catalog=catalog,
            database=database,
            table=table,
            accesses=sorted(remaining),
        )
        result = await self._ranger.put_policy(policy)
        await self._record_policy_state(policy.name, result, ProjectionState.PROPAGATING)
        await self._audit_admin(
            security,
            action="REVOKE_ACCESS",
            object_type="RANGER_POLICY",
            object_name=policy.name,
            ranger_ids=[result["id"]] if result.get("id") is not None else [],
        )

    async def put_data_scope(
        self,
        security: SecurityContext,
        *,
        principal: str,
        role: str,
        catalog: str,
        database: str,
        table: str,
        bindings: list[tuple[str, str, list[str]]],
    ) -> dict:
        self.require_security_admin(security)
        await self._validate_scope(principal, role, catalog, database, table, bindings)
        attributes: dict[str, str] = {}
        for dimension, _, values in bindings:
            if not values:
                raise AccessControlError("A data scope assignment cannot be empty")
            attributes[role_scope_attribute(role, dimension)] = ",".join(sorted(set(values)))
        await self._ranger.put_user_attributes(principal, attributes)
        policy = compile_row_filter_policy(
            role=role,
            catalog=catalog,
            database=database,
            table=table,
            bindings=bindings,
        )
        result = await self._ranger.put_policy(policy)
        now = datetime.now(UTC).replace(tzinfo=None)
        for dimension, _, values in bindings:
            assignment_id = hashlib.sha256(
                f"{principal}\x1f{role}\x1f{dimension}".encode()
            ).hexdigest()
            await db.execute_system(
                """
                INSERT INTO NOVA_SYSTEM.CONFIG_DATA_SCOPE_ASSIGNMENTS
                    (assignment_id, principal, role_name, dimension_key,
                     scope_values_json, state, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    assignment_id,
                    principal,
                    role,
                    dimension,
                    json.dumps(sorted(set(values))),
                    ProjectionState.PROPAGATING.value,
                    now,
                ],
            )
        await self._record_policy_state(policy.name, result, ProjectionState.PROPAGATING)
        await self._audit_admin(
            security,
            action="PUT_DATA_SCOPE",
            object_type="RANGER_ROW_FILTER",
            object_name=policy.name,
            ranger_ids=[result["id"]] if result.get("id") is not None else [],
        )
        return result

    async def _validate_scope(
        self, principal: str, role: str, catalog: str, database: str, table: str,
        bindings: list[tuple[str, str, list[str]]],
    ) -> None:
        from app.common.identifiers import InvalidIdentifierError
        from app.modules.users.service import user_service

        try:
            for field, value in (
                ("principal", principal), ("role", role), ("catalog", catalog),
                ("database", database), ("table", table),
            ):
                check_identifier(value, field=field)
            dimensions: set[str] = set()
            if not bindings:
                raise AccessControlError("A data scope requires at least one binding")
            for dimension, column, values in bindings:
                check_identifier(dimension, field="dimension")
                check_identifier(column, field="column")
                if dimension in dimensions:
                    raise AccessControlError("Each scope dimension must have one binding")
                dimensions.add(dimension)
                if not values or any(
                    not isinstance(value, str) or not value or len(value) > 1024
                    or value == "__nova_no_scope__" or any(ord(c) < 32 for c in value)
                    for value in values
                ):
                    raise AccessControlError(
                        "Scope values must be nonempty text without control characters"
                    )
        except InvalidIdentifierError as exc:
            raise AccessControlError("Scope identifiers must be plain names") from exc
        users = await user_service.list_users()
        matches = [user for user in users if user.get("username") == principal]
        if not matches:
            raise AccessControlError("Scope principal does not exist")
        if not await self._ranger.get_role(role):
            raise AccessControlError("Scope role does not exist in Ranger")
        if not all(role in user.get("roles", []) for user in matches):
            raise AccessControlError("Scope role must be assigned to the principal")
        metadata = await db.execute_system(
            f"SELECT COLUMN_NAME FROM `{catalog}`.information_schema.columns "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
            [database, table],
        )
        columns = {str(row[0]) for row in metadata["rows"]}
        if not columns or any(column not in columns for _, column, _ in bindings):
            raise AccessControlError("Scope table or column does not exist")

    async def put_mask(
        self,
        security: SecurityContext,
        *,
        role: str,
        catalog: str,
        database: str,
        table: str,
        column: str,
        mask_type: str,
        value_expression: str | None = None,
    ) -> dict:
        self.require_security_admin(security)
        policy = compile_mask_policy(
            role=role,
            catalog=catalog,
            database=database,
            table=table,
            column=column,
            mask_type=mask_type,
            value_expression=value_expression,
        )
        result = await self._ranger.put_policy(policy)
        await self._record_policy_state(policy.name, result, ProjectionState.PROPAGATING)
        await self._audit_admin(
            security,
            action="PUT_MASK",
            object_type="RANGER_MASK_POLICY",
            object_name=policy.name,
            ranger_ids=[result["id"]] if result.get("id") is not None else [],
        )
        return result

    async def _record_policy_state(
        self, name: str, ranger_result: dict, state: ProjectionState
    ) -> None:
        desired_json = json.dumps(ranger_result, sort_keys=True, default=str)
        await db.execute_system(
            """
            INSERT INTO NOVA_SYSTEM.CONFIG_SECURITY_POLICIES
                (policy_name, ranger_id, ranger_guid, state, desired_hash, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                name,
                ranger_result.get("id"),
                ranger_result.get("guid"),
                state.value,
                hashlib.sha256(desired_json.encode()).hexdigest(),
                datetime.now(UTC).replace(tzinfo=None),
            ],
        )

    async def list_managed_policies(self) -> list[dict]:
        prefix = f"{settings.RANGER_MANAGED_POLICY_PREFIX}/"
        return [
            policy
            for policy in await self._ranger.list_policies()
            if str(policy.get("name", "")).startswith(prefix)
        ]

    async def list_roles(self) -> list[dict]:
        return await self._ranger.list_roles()

    async def role_privileges(self, role: str) -> list[dict]:
        privileges: list[dict] = []
        for policy in await self.list_managed_policies():
            if int(policy.get("policyType", 0)) != 0:
                continue
            resources = policy.get("resources", {})
            for item in policy.get("policyItems", []):
                if role not in item.get("roles", []):
                    continue
                for access in item.get("accesses", []):
                    if not access.get("isAllowed", True):
                        continue
                    privileges.append(
                        {
                            "GRANTEE": role,
                            "OBJECT_CATALOG": ",".join(
                                resources.get("catalog", {}).get("values", [])
                            ),
                            "OBJECT_DATABASE": ",".join(
                                resources.get("database", {}).get("values", [])
                            ),
                            "OBJECT_NAME": ",".join(
                                resources.get("table", {}).get("values", [])
                            ),
                            "OBJECT_TYPE": "TABLE",
                            "PRIVILEGE_TYPE": str(access.get("type", "")).upper(),
                            "IS_GRANTABLE": "NO",
                            "POLICY_NAME": policy.get("name"),
                            "AUTHORIZATION_PROVIDER": "Ranger",
                        }
                    )
        return privileges

    async def effective_access(self, *, principal: str, active_role: str, resource: str) -> dict:
        parts = resource.split(".")
        if len(parts) == 2:
            catalog, database, table = "default_catalog", parts[0], parts[1]
        elif len(parts) == 3:
            catalog, database, table = parts
        else:
            raise AccessControlError("Resource must be database.table or catalog.database.table")
        policies = await self.list_managed_policies()
        matching = [
            policy
            for policy in policies
            if active_role
            in {
                role
                for key in ("policyItems", "rowFilterPolicyItems", "dataMaskPolicyItems")
                for item in policy.get(key, [])
                for role in item.get("roles", [])
            }
            and self._resource_matches(policy, catalog, database, table)
        ]
        attributes = await self._ranger.get_user_attributes(principal)
        object_access: set[str] = set()
        row_restrictions: list[str] = []
        column_restrictions: dict[str, str] = {}
        for policy in matching:
            for item in policy.get("policyItems", []):
                if active_role in item.get("roles", []):
                    object_access.update(
                        str(access.get("type", "")).upper()
                        for access in item.get("accesses", [])
                        if access.get("isAllowed", True)
                    )
            for item in policy.get("rowFilterPolicyItems", []):
                if active_role not in item.get("roles", []):
                    continue
                expression = str(item.get("rowFilterInfo", {}).get("filterExpr", ""))
                expression = re.sub(
                    r"\$\{\{GET_USER_ATTR_Q\('([^']+)',\s*'[^']*'\)\}\}",
                    lambda match: self._quoted_scope_values(attributes.get(match.group(1))),
                    expression,
                )
                row_restrictions.append(expression)
            for item in policy.get("dataMaskPolicyItems", []):
                if active_role not in item.get("roles", []):
                    continue
                mask_type = str(item.get("dataMaskInfo", {}).get("dataMaskType", "MASK"))
                for column in policy.get("resources", {}).get("column", {}).get("values", []):
                    column_restrictions[str(column)] = mask_type
        return {
            "principal": principal,
            "active_role": active_role,
            "resource": resource,
            "authorization_provider": "Ranger",
            "policies": matching,
            "policy_health": "ACTIVE" if matching else "NO_MATCH",
            "object_access": sorted(object_access),
            "row_restrictions": row_restrictions,
            "column_restrictions": column_restrictions,
        }

    @staticmethod
    def _resource_matches(policy: dict, catalog: str, database: str, table: str) -> bool:
        resources = policy.get("resources", {})
        for key, expected in (("catalog", catalog), ("database", database), ("table", table)):
            values = {str(value) for value in resources.get(key, {}).get("values", [])}
            if values and "*" not in values and expected not in values:
                return False
        return True

    @staticmethod
    def _quoted_scope_values(raw: str | None) -> str:
        if not raw:
            return "'<missing>'"
        escaped = [value.strip().replace("'", "''") for value in raw.split(",")]
        return ",".join(f"'{value}'" for value in escaped)

    async def health(self) -> dict:
        if not settings.RANGER_ENABLED:
            return {"enabled": False, "healthy": False, "reason": "Ranger is disabled"}
        try:
            health = await self._ranger.health()
        except RangerError as exc:
            return {"enabled": True, "healthy": False, "reason": str(exc)}
        pending = await db.execute_system(
            "SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_SECURITY_POLICIES "
            "WHERE state IN ('PENDING', 'PROPAGATING')"
        )
        drifted = await db.execute_system(
            "SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_SECURITY_ROLE_PROJECTIONS "
            "WHERE state = 'DRIFTED'"
        )
        return {
            "enabled": True,
            "healthy": bool(health.get("reachable") and health.get("service_exists")),
            **health,
            "pending_propagation": int(pending["rows"][0][0]) if pending["rows"] else 0,
            "drift_count": int(drifted["rows"][0][0]) if drifted["rows"] else 0,
        }


access_control_service = AccessControlService()
