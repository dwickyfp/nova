"""User provisioning with password input outside the model transcript."""

from __future__ import annotations

import re
import logging
from typing import Any

from app.common.audit import write_audit_log
from app.common.user_flags import set_must_change_password
from app.core.config import settings
from app.modules.access_control.security_context import SecurityContext
from app.modules.access_control.service import access_control_service
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.users.service import user_service

ADMIN_ROLES = frozenset({"ACCOUNTADMIN", "SECURITYADMIN", "user_admin", "security_admin"})
logger = logging.getLogger(__name__)


def account_request(arguments: dict[str, Any]) -> tuple[str, str]:
    if set(arguments) != {"username", "role"}:
        raise ValueError("Provide only username and role. Enter a temporary password in protected input.")
    username, role = arguments["username"], arguments["role"]
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z_][\w.$-]{0,63}", username):
        raise ValueError("Invalid username.")
    if username.casefold() in {"root", "nova_admin"}:
        raise ValueError("This account is protected.")
    if not isinstance(role, str) or not re.fullmatch(r"[A-Za-z_][\w$]{0,63}", role):
        raise ValueError("Invalid role.")
    if role.upper() in {"ALL", "NONE", "DEFAULT"}:
        raise ValueError("Choose one existing role.")
    return username, role


class ProvisionUserTool:
    name = "provision_user"
    description = (
        "Create a StarRocks user with one existing role as its default and mandatory "
        "password change at first login. The approval form collects a temporary password "
        "privately; never ask for it in chat or tool arguments. Calls internal services, "
        "requires an active security-admin role and explicit approval."
    )
    parameters = {
        "type": "object",
        "properties": {"username": {"type": "string"}, "role": {"type": "string"}},
        "required": ["username", "role"],
        "additionalProperties": False,
    }
    classification = "destructive"
    requires_consent = True

    def preview(self, invocation: ToolInvocation) -> str:
        try:
            username, role = account_request(invocation.arguments)
        except ValueError as exc:
            return str(exc)
        return (f"Create user '{username}' with default role {role}.\n"
                "Enter a temporary password in protected input.\n"
                "Require password change at first login.")

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        secure_input = context.secure_input
        context.secure_input = None
        try:
            return await self._provision(invocation, context, secure_input)
        finally:
            if isinstance(secure_input, dict):
                secure_input.clear()

    async def _provision(
        self, invocation: ToolInvocation, context: Any, secure_input: dict[str, str] | None
    ) -> ToolOutcome:
        try:
            username, role = account_request(invocation.arguments)
            security = SecurityContext.from_session(context.user or {})
            if security.active_role not in ADMIN_ROLES:
                return ToolOutcome(ok=False, summary="", error="An active security-admin role is required.",
                                   error_class="AUTHORIZATION_DENIED")
            if not secure_input or set(secure_input) != {"password"} or not secure_input["password"]:
                return ToolOutcome(ok=False, summary="", error="Enter a temporary password in protected input.")
            roles = (await access_control_service.list_roles() if settings.RANGER_ENABLED
                     else await user_service.list_roles())
            if not any(r.get("name", r.get("role_name")) == role for r in roles):
                return ToolOutcome(ok=False, summary="", error="The requested role does not exist.")
            if await user_service.user_exists(username):
                return ToolOutcome(ok=False, summary="", error="This user already exists; no changes made.")
            audit = dict(event_type="assistant_tool", user_name=security.principal,
                         action=self.name, object_type="USER", object_name=username,
                         active_role=security.active_role, session_id=context.audit_session_id)
            await write_audit_log(**audit, status="PENDING")
        except Exception as exc:
            logger.warning("User provisioning validation failed (%s)", type(exc).__name__)
            return ToolOutcome(ok=False, summary="", error="User provisioning validation failed.")

        completed: list[str] = []
        try:
            # Install the policy first: a partially created account must still
            # require a password change even if a later service call fails.
            await set_must_change_password(username, required=True)
            await user_service.create_user(username, password=secure_input["password"])
            completed.append("user created")
            if settings.RANGER_ENABLED:
                await access_control_service.assign_role(security, role=role, username=username)
            else:
                await user_service.assign_role(username, role)
            completed.append("role assigned")
            await user_service.set_default_roles(username, "%", "explicit", [role])
            completed.append("default role set")
            await write_audit_log(**audit, status="SUCCESS")
        except Exception:
            try:
                await write_audit_log(**audit, status="ERROR")
            except Exception:
                pass
            return ToolOutcome(
                ok=False, summary="",
                error="Provisioning did not finish. Inspect the account before retrying; creation is not atomic.",
                data={"username": username, "completed_steps": completed, "atomic": False},
            )
        return ToolOutcome(ok=True, summary=f"Created {username} with default role {role}; password change required.",
                           data={"username": username, "role": role, "must_change_password": True})


provision_user_tool = ProvisionUserTool()
