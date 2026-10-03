from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.modules.access_control.security_context import SecurityContext
from app.modules.access_control.service import AccessControlError, access_control_service
from app.modules.query.repository import QueryResult
from app.sql_frontend.antlr_utils import walk_nodes as _walk_nodes
from app.sql_frontend.parser import ParsedStatement


@dataclass(frozen=True, slots=True)
class SecurityOperation:
    kind: str
    identifiers: tuple[str, ...] = ()


def _value(parsed: ParsedStatement, node: Any) -> str:
    value = parsed.normalized_sql[node.start.start : node.stop.stop + 1]
    if value[:1] in {"'", '"', "`"}:
        return value[1:-1].replace(value[0] * 2, value[0])
    return value


def decode_security(parsed: ParsedStatement) -> SecurityOperation | None:
    root = parsed.statement_context
    kind = type(root).__name__
    nodes = list(_walk_nodes(root))
    identifiers = tuple(
        _value(parsed, node) for node in nodes if type(node).__name__ == "IdentifierOrStringContext"
    )
    words = [token.text.upper() for token in parsed.visible_tokens]
    if (
        kind in {"CreateRoleStatementContext", "DropRoleStatementContext"}
        and len(identifiers) == 1
        and "IF" not in words
        and "COMMENT" not in words
    ):
        return SecurityOperation(
            "create_role" if words[0] == "CREATE" else "drop_role", identifiers
        )
    grant_ops = {
        "GrantRoleToUserContext": "assign_role",
        "RevokeRoleFromUserContext": "revoke_role",
        "GrantRoleToRoleContext": "grant_role_to_role",
        "RevokeRoleFromRoleContext": "revoke_role_from_role",
    }
    if (
        kind in grant_ops
        and len(identifiers) == 2
        and not any(
            type(node).__name__ in {"UserWithHostContext", "UserWithHostAndBlanketContext"}
            for node in nodes
        )
    ):
        return SecurityOperation(grant_ops[kind], identifiers)
    if kind in {
        "GrantOnTableBriefContext",
        "GrantOnPrimaryObjContext",
        "RevokeOnTableBriefContext",
        "RevokeOnPrimaryObjContext",
    }:
        objects = [node for node in nodes if type(node).__name__ == "PrivObjectNameContext"]
        privileges = [node for node in nodes if type(node).__name__ == "PrivilegeTypeContext"]
        clauses = [node for node in nodes if type(node).__name__ == "GrantRevokeClauseContext"]
        if len(objects) == len(privileges) == len(clauses) == 1:
            clause = clauses[0]
            privilege = _value(parsed, privileges[0]).upper()
            object_tokens = [
                token
                for token in parsed.visible_tokens
                if objects[0].start.start <= token.start <= objects[0].stop.stop
            ]
            if (
                len(object_tokens) == 3
                and object_tokens[1].text == "."
                and all(token.text != "*" for token in object_tokens)
                and privilege in {"SELECT", "INSERT", "UPDATE", "DELETE", "ALTER"}
                and clause.start.text.upper() == "ROLE"
                and "OPTION" not in words
                and ("TABLE" in words or kind.endswith("TableBriefContext"))
            ):
                database, table = (
                    token.text.strip("`'") for token in (object_tokens[0], object_tokens[2])
                )
                return SecurityOperation(
                    "grant_access" if words[0] == "GRANT" else "revoke_access",
                    (identifiers[-1], database, table, privilege),
                )
    if kind == "ShowRolesStatementContext" and len(words) == 2:
        return SecurityOperation("list_roles")
    if kind == "NovaSecurityShowStatementContext":
        return SecurityOperation("list_roles" if words[1] == "AVAILABLE" else "current_access")
    if kind == "ShowGrantsStatementContext":
        if words[:4] == ["SHOW", "GRANTS", "FOR", "ROLE"] and len(identifiers) == 1:
            return SecurityOperation("role_grants", identifiers)
        return None
    raise AccessControlError(
        "Security statement is unsupported in strict Ranger mode; no native grant was sent"
    )


async def execute_security_operation(
    operation: SecurityOperation, security: SecurityContext, original_sql: str = ""
) -> QueryResult:
    identifiers = operation.identifiers
    service = access_control_service
    if operation.kind == "create_role":
        await service.create_role(security, identifiers[0])
    elif operation.kind == "drop_role":
        await service.drop_role(security, identifiers[0])
    elif operation.kind == "assign_role":
        await service.assign_role(security, role=identifiers[0], username=identifiers[1])
    elif operation.kind == "revoke_role":
        await service.revoke_role(security, role=identifiers[0], username=identifiers[1])
    elif operation.kind == "grant_role_to_role":
        await service.grant_role_to_role(
            security, parent_role=identifiers[1], member_role=identifiers[0]
        )
    elif operation.kind == "revoke_role_from_role":
        await service.revoke_role_from_role(
            security, parent_role=identifiers[1], member_role=identifiers[0]
        )
    elif operation.kind in {"grant_access", "revoke_access"}:
        method = service.grant_access if operation.kind == "grant_access" else service.revoke_access
        await method(
            security,
            role=identifiers[0],
            catalog="default_catalog",
            database=identifiers[1],
            table=identifiers[2],
            accesses=[identifiers[3]],
        )
    elif operation.kind == "list_roles":
        roles = await service.list_roles()
        rows = [[item.get("name"), "Ranger"] for item in roles]
        return QueryResult(
            columns=["Role", "Authorization"],
            rows=rows,
            row_count=len(rows),
            original_sql=original_sql,
        )
    elif operation.kind == "current_access":
        return QueryResult(
            columns=["Principal", "Active Role", "Authorization", "Policy Health"],
            rows=[[security.principal, security.active_role, "Ranger", "Active"]],
            row_count=1,
            original_sql=original_sql,
        )
    elif operation.kind == "role_grants":
        policies = await service.list_managed_policies()
        role = identifiers[0]
        rows = [
            [role, policy.get("name"), "Ranger", "ACTIVE"]
            for policy in policies
            if any(role in item.get("roles", []) for item in policy.get("policyItems", []))
        ]
        return QueryResult(
            columns=["Role", "Policy", "Authorization", "PolicyHealth"],
            rows=rows,
            row_count=len(rows),
            original_sql=original_sql,
        )
    action = operation.kind.replace("_", " ").upper()
    labels = {
        "assign_role": "GRANT ROLE",
        "revoke_role": "REVOKE ROLE",
        "grant_role_to_role": "GRANT ROLE TO ROLE",
        "revoke_role_from_role": "REVOKE ROLE FROM ROLE",
        "grant_access": "GRANT",
        "revoke_access": "REVOKE",
    }
    role = (
        identifiers[1]
        if operation.kind.endswith("_to_role") or operation.kind.endswith("_from_role")
        else identifiers[0]
    )
    return QueryResult(
        columns=["Action", "Role", "Authorization"],
        rows=[[labels.get(operation.kind, action), role, "Ranger"]],
        row_count=1,
        original_sql=original_sql,
    )


async def execute_security(parsed: ParsedStatement, security: SecurityContext) -> QueryResult:
    operation = decode_security(parsed)
    if operation is None:
        raise AccessControlError("Statement has no managed security operation")
    return await execute_security_operation(operation, security, parsed.original_sql)
