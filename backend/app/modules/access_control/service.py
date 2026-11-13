"""Central management service for Ranger-backed Nova authorization."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from typing import Literal

from app.common.audit import write_audit_log
from app.common.identifiers import check_identifier
from app.common.sql_guard import CredentialsRedactionError, redact_sql_credentials
from app.core.config import settings
from app.core.database import db
from app.core.metadata_runtime import metadata_db
from app.integrations.ranger.client import (
    RangerClient,
    RangerConflictError,
    RangerError,
    ranger_client,
)
from app.integrations.ranger.compiler import (
    compile_access_policy,
    compile_bi_metadata_policy,
    compile_mask_policy,
    compile_row_filter_policy,
    role_scope_attribute,
)
from app.integrations.ranger.schemas import MaskType, RangerPolicy, RangerRole, RangerRoleMember

from .domain import AccessRequest, ProjectionState, RoleProjection
from .metadata_repository import AccessControlMetadataRepository
from .security_context import SecurityContext

#: Longest projection message the store keeps.
_MESSAGE_LIMIT = 2048


def _stored_message(exc: BaseException | str | None) -> str | None:
    """Ranger/StarRocks error text safe to persist: credentials redacted, bounded."""
    if exc is None:
        return None
    try:
        return redact_sql_credentials(str(exc))[:_MESSAGE_LIMIT] or None
    except CredentialsRedactionError:
        return "The provider error was withheld because it held credentials."


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
        # Every managed access change retires cached News access proofs and
        # cached catalog visibility at once.
        from app.modules.catalog import visibility as catalog_visibility
        from app.modules.intelligence.newsroom_cache import proof_cache

        await proof_cache.bump()
        await catalog_visibility.bump()
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

    @staticmethod
    async def _record_projection(
        security: SecurityContext,
        name: str,
        state: ProjectionState,
        message: BaseException | str | None = None,
    ) -> None:
        """Record how far a role projection got, from the revision read."""
        async with metadata_db().transaction(security) as conn:
            repo = AccessControlMetadataRepository(conn, security)
            current = await repo.get_role_projection(name)
            await repo.put_role_projection(
                name,
                state,
                message=_stored_message(message),
                expected_revision=current["metadata_revision"] if current else None,
            )

    @staticmethod
    async def _forget_projection(security: SecurityContext, name: str) -> None:
        async with metadata_db().transaction(security) as conn:
            repo = AccessControlMetadataRepository(conn, security)
            current = await repo.get_role_projection(name)
            if current is not None:
                await repo.delete_role_projection(
                    name, expected_revision=current["metadata_revision"]
                )

    async def create_role(
        self, security: SecurityContext, name: str, description: str = ""
    ) -> RoleProjection:
        self.require_security_admin(security)
        safe_name = check_identifier(name, field="role")
        if safe_name.upper() == "ACCOUNTADMIN":
            raise AccessControlError("ACCOUNTADMIN is immutable")
        await self._record_projection(security, name, ProjectionState.PROVISIONING)
        ranger_created = False
        try:
            await self._ranger.put_role(
                RangerRole(name=name, description=description or "Managed by Nova")
            )
            ranger_created = True
            await db.execute_system(f"CREATE ROLE IF NOT EXISTS {safe_name}")
            await self._record_projection(security, name, ProjectionState.ACTIVE)
            await self._audit_admin(
                security, action="CREATE_ROLE", object_type="ROLE", object_name=name
            )
            return RoleProjection(name, ProjectionState.ACTIVE, True, True)
        except Exception as exc:
            if ranger_created:
                try:
                    await self._ranger.delete_role(name)
                except Exception:
                    await self._record_projection(security, name, ProjectionState.DRIFTED, exc)
                    raise AccessControlError(
                        "Role provisioning partially failed and requires reconciliation"
                    ) from exc
            await self._record_projection(security, name, ProjectionState.ERROR, exc)
            raise AccessControlError("Role provisioning failed") from exc

    async def drop_role(self, security: SecurityContext, name: str) -> None:
        self.require_security_admin(security)
        if name.upper() == "ACCOUNTADMIN":
            raise AccessControlError("ACCOUNTADMIN is immutable")
        safe_name = check_identifier(name, field="role")
        await self._record_projection(security, name, ProjectionState.DELETING)
        ranger_role = await self._ranger.get_role(name)
        await self._ranger.delete_role(name)
        try:
            await db.execute_system(f"DROP ROLE IF EXISTS {safe_name}")
        except Exception as exc:
            if ranger_role:
                try:
                    await self._ranger.put_role(RangerRole.model_validate(ranger_role))
                except Exception:
                    await self._record_projection(security, name, ProjectionState.DRIFTED, exc)
                    raise AccessControlError(
                        "Role deletion partially failed and requires reconciliation"
                    ) from exc
            await self._record_projection(security, name, ProjectionState.ERROR, exc)
            raise AccessControlError("Role deletion failed") from exc
        await self._forget_projection(security, name)
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
        resource_type: Literal["database", "table", "materialized_view"] | None = None,
    ) -> dict:
        self.require_security_admin(security)
        policy = compile_access_policy(
            role=role,
            catalog=catalog,
            database=database,
            table=table,
            accesses=accesses,
            resource_type=resource_type,
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
                resource_type=resource_type,
            )
        result = await self._apply_policy(security, policy)
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
        resource_type: Literal["database", "table", "materialized_view"] | None = None,
    ) -> None:
        self.require_security_admin(security)
        desired = compile_access_policy(
            role=role,
            catalog=catalog,
            database=database,
            table=table,
            accesses=accesses if resource_type is not None else [],
            resource_type=resource_type,
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
            resource_type=resource_type,
        )
        result = await self._apply_policy(security, policy)
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
        policy = compile_row_filter_policy(
            role=role,
            catalog=catalog,
            database=database,
            table=table,
            bindings=bindings,
        )
        # Nova's intent is committed before Ranger changes. An outcome that is
        # not confirmed stays PENDING and is reported for verification.
        await self._record_data_scope(
            security, principal, role, bindings, policy, None, ProjectionState.PENDING
        )
        await self._ranger.put_user_attributes(principal, attributes)
        try:
            result = await self._ranger.put_policy(policy)
        except RangerConflictError:
            await self._record_data_scope(
                security, principal, role, bindings, policy, None, ProjectionState.ERROR
            )
            raise
        await self._record_data_scope(
            security, principal, role, bindings, policy, result, ProjectionState.PROPAGATING
        )
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
        mask_type: MaskType,
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
        result = await self._apply_policy(security, policy)
        await self._audit_admin(
            security,
            action="PUT_MASK",
            object_type="RANGER_MASK_POLICY",
            object_name=policy.name,
            ranger_ids=[result["id"]] if result.get("id") is not None else [],
        )
        return result

    async def put_bi_metadata_access(self, security: SecurityContext, roles: list[str]) -> dict:
        """Grant table reflection to exactly ``roles`` (the BI author roles).

        Replaces the managed policy's role list, so a role removed from the BI
        authors loses reflection in the same change. ``ACCOUNTADMIN`` always
        keeps it.
        """
        self.require_security_admin(security)
        policy = compile_bi_metadata_policy(roles=[*roles, "ACCOUNTADMIN"])
        result = await self._apply_policy(security, policy)
        await self._audit_admin(
            security,
            action="PUT_BI_METADATA_ACCESS",
            object_type="RANGER_POLICY",
            object_name=policy.name,
            ranger_ids=[result["id"]] if result.get("id") is not None else [],
        )
        return result

    @staticmethod
    async def _put_policy_state(
        repo: AccessControlMetadataRepository,
        policy: RangerPolicy,
        result: dict | None,
        state: ProjectionState,
    ) -> None:
        current = await repo.get_policy_state(policy.name)
        if result is None:
            # The intent: the definition Nova is about to send, keeping the
            # Ranger identity already known for this managed policy.
            result = {
                **policy.to_api(),
                "id": current["ranger_id"] if current else None,
                "guid": current["ranger_guid"] if current else None,
            }
        await repo.put_policy_state(
            policy.name,
            result,
            state,
            expected_revision=current["metadata_revision"] if current else None,
        )

    async def _record_policy_state(
        self,
        security: SecurityContext,
        policy: RangerPolicy,
        result: dict | None,
        state: ProjectionState,
    ) -> None:
        async with metadata_db().transaction(security) as conn:
            repo = AccessControlMetadataRepository(conn, security)
            await self._put_policy_state(repo, policy, result, state)

    async def _record_data_scope(
        self,
        security: SecurityContext,
        principal: str,
        role: str,
        bindings: list[tuple[str, str, list[str]]],
        policy: RangerPolicy,
        result: dict | None,
        state: ProjectionState,
    ) -> None:
        """Record the scope assignments and their row-filter policy together."""
        async with metadata_db().transaction(security) as conn:
            repo = AccessControlMetadataRepository(conn, security)
            for dimension, _, values in bindings:
                current = await repo.get_data_scope(principal, role, dimension)
                await repo.put_data_scope(
                    principal,
                    role,
                    dimension,
                    values,
                    state=state,
                    expected_revision=current["metadata_revision"] if current else None,
                )
            await self._put_policy_state(repo, policy, result, state)

    async def _apply_policy(self, security: SecurityContext, policy: RangerPolicy) -> dict:
        """Write one managed policy to Ranger: intent first, then the outcome.

        A refusal Ranger confirms is recorded as ``ERROR``; any other failure
        leaves the intent ``PENDING`` for verification rather than a replay.
        """
        await self._record_policy_state(security, policy, None, ProjectionState.PENDING)
        try:
            result = await self._ranger.put_policy(policy)
        except RangerConflictError:
            await self._record_policy_state(security, policy, None, ProjectionState.ERROR)
            raise
        await self._record_policy_state(security, policy, result, ProjectionState.PROPAGATING)
        return result

    async def list_managed_policies(self) -> list[dict]:
        prefix = f"{settings.RANGER_MANAGED_POLICY_PREFIX}/"
        return [
            policy
            for policy in await self._ranger.list_policies()
            if str(policy.get("name", "")).startswith(prefix)
        ]

    async def list_roles(self) -> list[dict]:
        return await self._ranger.list_roles()

    async def available_roles(
        self, security: SecurityContext, *, connection: object | None = None
    ) -> list[str]:
        """Roles this session can switch to, as ``USE ROLE`` decides.

        A held connection (the MySQL proxy) is asked for its grants, as role
        activation does; a web session uses the roles recorded for it at sign-in,
        which the role switch checks first. Either way a role must also exist in
        Ranger, because activation refuses one that does not.
        """
        assigned: set[str] = set()
        if connection is not None:
            from .role_activation import role_activation_service

            assignments = await role_activation_service.assignments(connection)  # type: ignore[arg-type]
            assigned.update(assignments.assigned_roles)
        elif security.session_id:
            from app.core.redis import session_store

            session = await session_store.get(security.session_id) or {}
            assigned.update(session.get("assigned_roles") or [])
        assigned.add(security.active_role)
        healthy = {str(role.get("name")) for role in await self.list_roles()}
        return sorted(assigned & healthy)

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
        # Ranger's bootstrap policies also authorize roles such as ACCOUNTADMIN.
        # Restricting this preview to Nova-managed policies makes real grants
        # look like gaps in Agent Verify Access.
        policies = await self._ranger.list_policies()
        matching = [
            policy
            for policy in policies
            if policy.get("service", settings.RANGER_SERVICE_NAME) == settings.RANGER_SERVICE_NAME
            and policy.get("isEnabled", True)
            and active_role
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
        column_restriction_fingerprints: dict[str, str] = {}
        object_access_supported = self._object_access_comparison_supported(
            policies, catalog, database, table,
        )
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
                # Show the engine's FIND_IN_SET scope match as the IN list it means.
                expression = re.sub(
                    r"FIND_IN_SET\(\s*([^,()]+?)\s*,\s*"
                    r"(\$\{\{GET_USER_ATTR_Q\('[^']+',\s*'[^']*'\)\}\})\s*\)\s*>\s*0",
                    r"\1 IN (\2)",
                    expression,
                )
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
                    column_restriction_fingerprints[str(column)] = hashlib.sha256(
                        json.dumps(item.get("dataMaskInfo", {}), sort_keys=True).encode()
                    ).hexdigest()
        return {
            "principal": principal,
            "active_role": active_role,
            "resource": resource,
            "authorization_provider": "Ranger",
            "policies": matching,
            "policy_health": (
                "UNSUPPORTED" if not object_access_supported
                else "ACTIVE" if matching else "NO_MATCH"
            ),
            "object_access": sorted(object_access) if object_access_supported else [],
            "object_access_comparison_supported": object_access_supported,
            "row_restrictions": row_restrictions,
            "column_restrictions": column_restrictions,
            "column_restriction_fingerprints": column_restriction_fingerprints,
            "security_effects_fingerprint": self._security_effects_fingerprint(
                policies, catalog, database, table,
            ),
            "security_effects_comparison_supported": self._security_effects_comparison_supported(
                policies, catalog, database, table,
            ),
        }

    async def role_allows(
        self, *, principal: str, active_role: str, requests: list[AccessRequest]
    ) -> list[bool]:
        """Whether Ranger policies allow each request for the caller's active role.

        Every Ranger policy counts, including bootstrap policies, because they
        authorize roles such as ACCOUNTADMIN. Anything this preview cannot
        evaluate (conditions, schedules, column-scoped items) never becomes an
        allow, and any matching deny item refuses the request.
        """
        if not principal or not active_role:
            return [False for _ in requests]
        policies = [
            policy
            for policy in await self._ranger.list_policies()
            if policy.get("service", settings.RANGER_SERVICE_NAME) == settings.RANGER_SERVICE_NAME
            and policy.get("isEnabled", True)
            and int(policy.get("policyType", 0)) == 0
        ]
        return [
            self._policies_allow(policies, principal, active_role, request)
            for request in requests
        ]

    @classmethod
    def _policies_allow(
        cls, policies: list[dict], principal: str, active_role: str, request: AccessRequest
    ) -> bool:
        wanted = request.access.lower()
        allowed = False
        for policy in policies:
            if not cls._request_matches(policy, request):
                continue
            for item in policy.get("denyPolicyItems", []):
                if cls._item_grants(item, principal, active_role, wanted, deny=True):
                    return False
            if policy.get("conditions") or policy.get("validitySchedules"):
                continue
            allowed = allowed or any(
                cls._item_grants(item, principal, active_role, wanted, deny=False)
                for item in policy.get("policyItems", [])
            )
        return allowed

    @staticmethod
    def _item_grants(
        item: dict, principal: str, active_role: str, wanted: str, *, deny: bool
    ) -> bool:
        if item.get("conditions") and not deny:
            return False
        grantee = (
            active_role in item.get("roles", [])
            or principal in item.get("users", [])
            or "public" in item.get("groups", [])
        )
        return grantee and any(
            str(access.get("type", "")).lower() in {wanted, "all"}
            and access.get("isAllowed", True)
            for access in item.get("accesses", [])
        )

    @staticmethod
    def _request_matches(policy: dict, request: AccessRequest) -> bool:
        expected = {"catalog": request.catalog}
        if request.database is not None:
            expected["database"] = request.database
        if request.table is not None:
            expected["table"] = request.table
        resources = dict(policy.get("resources", {}))
        if request.table is not None and resources.get("column", {}).get("values") == ["*"]:
            resources.pop("column")
        if set(resources) != set(expected):
            return False
        for key, value in expected.items():
            resource = resources[key]
            matched = any(
                fnmatch.fnmatchcase(value.lower(), str(pattern).lower())
                for pattern in resource.get("values", [])
            )
            if matched == bool(resource.get("isExcludes")):
                return False
        return True

    @classmethod
    def _security_effects_fingerprint(
        cls, policies: list[dict], catalog: str, database: str, table: str,
    ) -> str:
        definitions = []
        for policy in policies:
            if (policy.get("service", settings.RANGER_SERVICE_NAME) != settings.RANGER_SERVICE_NAME
                    or not policy.get("isEnabled", True)
                    or int(policy.get("policyType", 0)) not in {1, 2}
                    or not cls._resource_matches(policy, catalog, database, table)):
                continue
            definition = {key: policy.get(key) for key in (
                "policyType", "policyPriority", "conditions", "validitySchedules",
                "rowFilterPolicyItems", "dataMaskPolicyItems", "denyPolicyItems",
                "denyExceptions", "allowExceptions",
            )}
            definition["column"] = policy.get("resources", {}).get("column")
            definitions.append(json.dumps(definition, sort_keys=True))
        return hashlib.sha256(json.dumps(sorted(definitions)).encode()).hexdigest()

    @classmethod
    def _security_effects_comparison_supported(
        cls, policies: list[dict], catalog: str, database: str, table: str,
    ) -> bool:
        for policy in policies:
            if (policy.get("service", settings.RANGER_SERVICE_NAME) != settings.RANGER_SERVICE_NAME
                    or not policy.get("isEnabled", True)
                    or int(policy.get("policyType", 0)) not in {1, 2}):
                continue
            resources = policy.get("resources", {})
            if any(
                resource.get("isExcludes") or resource.get("isRecursive")
                or any(value != "*" and any(character in str(value) for character in "*?[]")
                       for value in resource.get("values", []))
                for resource in resources.values()
            ):
                return False
            if not cls._resource_matches(policy, catalog, database, table):
                continue
            if (policy.get("conditions") or policy.get("validitySchedules")
                    or policy.get("policyPriority", 0) != 0):
                return False
            for key in ("rowFilterPolicyItems", "dataMaskPolicyItems"):
                for item in policy.get(key, []):
                    if item.get("users") or item.get("groups") or item.get("conditions"):
                        return False
        return True

    @staticmethod
    def _resource_matches(policy: dict, catalog: str, database: str, table: str) -> bool:
        resources = policy.get("resources", {})
        if not resources or set(resources) - {"catalog", "database", "table", "column"}:
            return False
        for key, expected in (("catalog", catalog), ("database", database), ("table", table)):
            values = {str(value) for value in resources.get(key, {}).get("values", [])}
            if values and "*" not in values and expected not in values:
                return False
        return True

    @classmethod
    def _object_access_comparison_supported(
        cls,
        policies: list[dict],
        catalog: str,
        database: str,
        table: str,
    ) -> bool:
        for policy in policies:
            if (
                policy.get("service", settings.RANGER_SERVICE_NAME) != settings.RANGER_SERVICE_NAME
                or not policy.get("isEnabled", True)
                or int(policy.get("policyType", 0)) != 0
            ):
                continue
            resources = policy.get("resources", {})
            if not resources or set(resources) - {"catalog", "database", "table", "column"}:
                continue
            # The preview cannot evaluate Ranger exclusions, conditions, or
            # deny precedence. Consumers must not turn those into an allow.
            if any(
                resource.get("isExcludes")
                or resource.get("isRecursive")
                or any(
                    value != "*" and any(character in str(value) for character in "*?[]")
                    for value in resource.get("values", [])
                )
                for resource in resources.values()
            ):
                return False
            if not cls._resource_matches(policy, catalog, database, table):
                continue
            if resources.get("column", {}).get("values", ["*"]) != ["*"]:
                return False
            if any(
                policy.get(key)
                for key in (
                    "conditions",
                    "validitySchedules",
                    "policyPriority",
                    "isDenyAllElse",
                    "denyPolicyItems",
                    "denyExceptions",
                    "allowExceptions",
                )
            ) or any(item.get("conditions") for item in policy.get("policyItems", [])):
                return False
        return True

    @staticmethod
    def _quoted_scope_values(raw: str | None) -> str:
        if not raw:
            return "'<missing>'"
        escaped = [value.strip().replace("'", "''") for value in raw.split(",")]
        return ",".join(f"'{value}'" for value in escaped)

    async def health(self, security: SecurityContext) -> dict:
        try:
            health = await self._ranger.health()
        except RangerError as exc:
            return {"healthy": False, "reason": str(exc)}
        async with metadata_db().transaction(security, read_only=True) as conn:
            counts = await AccessControlMetadataRepository(conn, security).health_counts()
        return {
            "healthy": bool(health.get("reachable") and health.get("service_exists")),
            **health,
            "pending_propagation": counts["pending_propagation"],
            "drift_count": counts["drift_count"],
        }


access_control_service = AccessControlService()
