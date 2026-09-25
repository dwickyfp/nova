"""Typed Ranger access tools for Nove, without an HTTP or API-route bridge."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.common.audit import write_audit_log
from app.common.identifiers import InvalidIdentifierError, check_identifier
from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.access_control.service import (
    AccessControlError,
    AccessControlService,
    access_control_service,
)
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

_MAX_GRANTS = 16
_ALLOWED_ACCESS = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE", "ALTER", "USAGE"})
_VERIFY_DELAYS_SECONDS = (0.0, 0.1, 0.2)

_GRANT_SCHEMA = {
    "type": "object",
    "properties": {
        "resource": {
            "type": "string",
            "description": (
                "Database, database.table, or catalog.database.table. "
                "Database access uses the database name alone."
            ),
        },
        "access": {"type": "string", "enum": sorted(_ALLOWED_ACCESS)},
    },
    "required": ["resource", "access"],
    "additionalProperties": False,
}

_PARAMETERS = {
    "type": "object",
    "properties": {
        "role": {"type": "string", "description": "The exact existing role to inspect or grant."},
        "grants": {
            "type": "array",
            "items": _GRANT_SCHEMA,
            "minItems": 1,
            "maxItems": _MAX_GRANTS,
        },
    },
    "required": ["role", "grants"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class RequestedAccess:
    catalog: str
    database: str
    table: str
    access: str

    @property
    def resource(self) -> str:
        return (
            f"{self.database}.{self.table}"
            if self.catalog == "default_catalog"
            else (f"{self.catalog}.{self.database}.{self.table}")
        )


def _parse_request(arguments: dict[str, Any]) -> tuple[str, list[RequestedAccess]]:
    role = arguments.get("role")
    if not isinstance(role, str):
        raise ValueError("An existing role is required.")
    try:
        role = check_identifier(role, field="role")
    except InvalidIdentifierError as exc:
        raise ValueError("Invalid role name.") from exc
    raw = arguments.get("grants")
    if not isinstance(raw, list) or not 1 <= len(raw) <= _MAX_GRANTS:
        raise ValueError(f"Provide 1 to {_MAX_GRANTS} access entries.")
    grants: list[RequestedAccess] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"resource", "access"}:
            raise ValueError("Each entry needs one resource and one access type.")
        resource, access = item["resource"], item["access"]
        if not isinstance(resource, str) or not isinstance(access, str):
            raise ValueError("Resource and access must be strings.")
        parts = resource.split(".")
        if len(parts) == 1:
            catalog, database, table = "default_catalog", parts[0], "*"
        elif len(parts) == 2:
            catalog, database, table = "default_catalog", *parts
        elif len(parts) == 3:
            catalog, database, table = parts
        else:
            raise ValueError("Resource must name a database or table.")
        try:
            catalog = check_identifier(catalog, field="catalog")
            database = check_identifier(database, field="database")
            if table != "*":
                table = check_identifier(table, field="table")
        except InvalidIdentifierError as exc:
            raise ValueError("Invalid resource name.") from exc
        access = access.upper()
        if access not in _ALLOWED_ACCESS:
            raise ValueError("Unsupported Ranger access type.")
        if table == "*" and access != "USAGE":
            raise ValueError("Database-wide entries must request USAGE.")
        if table != "*" and access == "USAGE":
            raise ValueError("USAGE must target a database.")
        grant = RequestedAccess(catalog, database, table, access)
        if grant not in grants:
            grants.append(grant)
    return role, grants


def _security(context: Any) -> SecurityContext:
    user = getattr(context, "user", None)
    if not isinstance(user, dict):
        raise SecurityContextError("No authenticated Nova session is available.")
    security = SecurityContext.from_session(user)
    AccessControlService.require_security_admin(security)
    return security


async def _existing_role(role: str) -> bool:
    roles = await access_control_service.list_roles()
    return any(item.get("name") == role for item in roles if isinstance(item, dict))


async def _verify_access(
    *, principal: str, role: str, grants: list[RequestedAccess]
) -> list[dict[str, Any]] | None:
    """Read Ranger policy state after a grant; never retry the write itself."""
    latest: list[dict[str, Any]] | None = None
    for delay in _VERIFY_DELAYS_SECONDS:
        if delay:
            await asyncio.sleep(delay)
        checked: list[dict[str, Any]] = []
        try:
            for grant in grants:
                effective = await access_control_service.effective_access(
                    principal=principal,
                    active_role=role,
                    resource=grant.resource,
                )
                available = effective.get("object_access")
                if not isinstance(available, list):
                    raise ValueError("Ranger returned no access list")
                rights = {str(item).upper() for item in available}
                checked.append(
                    {
                        "resource": grant.resource,
                        "access": grant.access,
                        "granted": grant.access in rights or "ALL" in rights,
                    }
                )
        except Exception:
            latest = None
            continue
        latest = checked
        if all(item["granted"] for item in checked):
            return checked
    return latest


async def _audit(context: Any, action: str, role: str, status: str) -> str:
    user = context.user
    return await write_audit_log(
        event_type="assistant_access_action",
        user_name=user["username"],
        action=action,
        object_type="ROLE_ACCESS",
        object_name=role,
        status=status,
        session_id=getattr(context, "audit_session_id", None),
        active_role=user.get("active_role"),
        security_context_version=user.get("security_context_version"),
    )


def _preview(invocation: ToolInvocation, verb: str) -> str:
    try:
        role, grants = _parse_request(invocation.arguments)
    except ValueError as exc:
        return f"Invalid role access request: {exc}"
    lines = [f"{verb} role {role} access:"]
    lines.extend(f"{grant.access} on {grant.resource}" for grant in grants)
    return "\n".join(lines)


class InspectRoleAccessTool:
    name = "inspect_role_access"
    description = (
        "Check the exact existing role's effective Ranger access on up to 16 "
        "database or table resources. Use before granting access. No API route is exposed."
    )
    parameters = _PARAMETERS
    classification: ToolClassification = "read_only"
    requires_consent = True

    def preview(self, invocation: ToolInvocation) -> str:
        return _preview(invocation, "Check")

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        try:
            role, grants = _parse_request(invocation.arguments)
            security = _security(context)
        except (ValueError, SecurityContextError, AccessControlError) as exc:
            return ToolOutcome(ok=False, summary="", error=str(exc))
        results = []
        try:
            if not await _existing_role(role):
                return ToolOutcome(ok=False, summary="", error=f"Role {role} does not exist.")
            for grant in grants:
                effective = await access_control_service.effective_access(
                    principal=security.principal,
                    active_role=role,
                    resource=grant.resource,
                )
                available = set(effective.get("object_access") or [])
                results.append(
                    {
                        "resource": grant.resource,
                        "access": grant.access,
                        "granted": grant.access in available or "ALL" in available,
                    }
                )
            audit_id = await _audit(context, "INSPECT", role, "SUCCESS")
        except Exception:
            await _audit(context, "INSPECT", role, "FAILED")
            return ToolOutcome(ok=False, summary="", error="Role access could not be inspected.")
        return ToolOutcome(
            ok=True,
            summary=f"Inspected {len(results)} Ranger access entries for role {role}.",
            data={"role": role, "access": results},
            evidence={"audit_id": audit_id},
        )


class GrantRoleAccessTool:
    name = "grant_role_access"
    description = (
        "Grant missing Ranger database USAGE or table access to the exact existing "
        "role through Nova's access-control service. Never creates another role. "
        "Requires explicit approval; one approval covers only the previewed entries. "
        "Reports success only after every requested policy is verified by readback."
    )
    parameters = _PARAMETERS
    classification: ToolClassification = "destructive"
    requires_consent = True

    def preview(self, invocation: ToolInvocation) -> str:
        return _preview(invocation, "Grant")

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        try:
            role, grants = _parse_request(invocation.arguments)
            security = _security(context)
        except (ValueError, SecurityContextError, AccessControlError) as exc:
            return ToolOutcome(ok=False, summary="", error=str(exc))
        try:
            if not await _existing_role(role):
                return ToolOutcome(ok=False, summary="", error=f"Role {role} does not exist.")
        except Exception:
            return ToolOutcome(ok=False, summary="", error="The role could not be checked.")
        await _audit(context, "GRANT", role, "PENDING")
        applied: list[dict[str, str]] = []
        try:
            for grant in grants:
                await access_control_service.grant_access(
                    security,
                    role=role,
                    catalog=grant.catalog,
                    database=grant.database,
                    table=grant.table,
                    accesses=[grant.access],
                )
                applied.append({"resource": grant.resource, "access": grant.access})
        except Exception:
            await _audit(context, "GRANT", role, "FAILED")
            return ToolOutcome(
                ok=False,
                summary="",
                error=(
                    f"Submitted {len(applied)} of {len(grants)} grants. "
                    "Check current Ranger access before retrying."
                ),
                data={"applied": applied, "status": "PARTIAL"},
            )
        verified = await _verify_access(
            principal=security.principal, role=role, grants=grants
        )
        if verified is None or not all(item["granted"] for item in verified):
            audit_id = await _audit(context, "GRANT", role, "UNVERIFIED")
            missing = (
                [
                    {"resource": item["resource"], "access": item["access"]}
                    for item in verified
                    if not item["granted"]
                ]
                if verified is not None
                else applied
            )
            detail = (
                "The requested Ranger access was submitted but could not be "
                "verified. Inspect the role's access before attempting another change."
            )
            return ToolOutcome(
                ok=False,
                summary="",
                error=detail,
                error_class="POSTCONDITION_UNVERIFIED",
                safe_detail=detail,
                data={
                    "role": role,
                    "applied": applied,
                    "unverified": missing,
                    "status": "UNVERIFIED",
                },
                evidence={"audit_id": audit_id},
            )
        audit_id = await _audit(context, "GRANT", role, "SUCCESS")
        return ToolOutcome(
            ok=True,
            summary=(
                f"Verified {len(applied)} Ranger access grants for role {role}. "
                "Enforcement may still be propagating."
            ),
            data={
                "role": role,
                "applied": applied,
                "verified": verified,
                "status": "VERIFIED",
                "verification_source": "Ranger policy readback",
            },
            evidence={"audit_id": audit_id},
        )


inspect_role_access_tool = InspectRoleAccessTool()
grant_role_access_tool = GrantRoleAccessTool()
