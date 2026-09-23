"""Authorize access to a stage before its storage connection is resolved."""

from __future__ import annotations

import re

import asyncmy

from app.core.config import settings
from app.core.database import db


class StageAccessDenied(ValueError):
    pass


_GRANT = re.compile(r"^\s*GRANT\s+(.+?)\s+ON\s+(.+?)\s+TO\s+", re.IGNORECASE)
_ROLE_GRANT = re.compile(r"^\s*GRANT\s+(.+?)\s+TO\s+ROLE\s+", re.IGNORECASE)


def _grant_rows_allow(rows: list, database: str, schema: str, action: str) -> bool:
    wanted = {"read": {"SELECT", "INSERT", "ALL", "ALL PRIVILEGES"},
              "write": {"INSERT", "ALL", "ALL PRIVILEGES"},
              "delete": {"ALL", "ALL PRIVILEGES"}}[action]
    database = database.casefold()
    schema = schema.casefold()
    for row in rows:
        statement = str(row[-1] if isinstance(row, (list, tuple)) else row)
        match = _GRANT.match(statement)
        if not match:
            continue
        privileges = {part.strip().upper() for part in match.group(1).split(",")}
        if not privileges & wanted:
            continue
        scope = re.sub(r"[`\"']", "", match.group(2)).strip().casefold()
        if scope in {"*.*", "table *.*", "all tables in all databases"}:
            return True
        if scope in {
            f"table {database}.*", f"{database}.*",
            f"all tables in database {database}",
            f"table {database}.{schema}.*", f"{database}.{schema}.*",
        }:
            return True
    return False


async def check_stage_access(
    stage: dict,
    *,
    action: str,
    username: str,
    password: str = "",
    active_role: str | None = None,
    connection: asyncmy.Connection | None = None,
) -> None:
    """Fail closed unless the active StarRocks principal can access the stage scope."""
    if action not in {"read", "write", "delete"}:
        raise ValueError("Unknown stage action")
    database = str(stage["database_name"])
    schema = str(stage["schema_name"])
    if settings.RANGER_ENABLED:
        if not active_role:
            raise StageAccessDenied("Stage access requires an active role")
        from app.modules.access_control.service import access_control_service

        effective = await access_control_service.effective_access(
            principal=username, active_role=active_role, resource=f"{database}.{schema}"
        )
        allowed = {str(item).upper() for item in effective["object_access"]}
        required = {"read": {"SELECT", "INSERT", "ALL"},
                    "write": {"INSERT", "ALL"}, "delete": {"ALL"}}[action]
        if not allowed & required:
            raise StageAccessDenied("Stage access denied")
        return

    async def read_grants(conn: asyncmy.Connection) -> list:
        async with conn.cursor() as cur:
            if active_role:
                from app.common.identifiers import check_identifier

                await cur.execute(f"SET ROLE {check_identifier(active_role, field='role')}")
            await cur.execute("SHOW GRANTS")
            return list(await cur.fetchall())

    try:
        if connection is None:
            async with db.user_conn(username, password) as conn:
                grants = await read_grants(conn)
        else:
            grants = await read_grants(connection)
    except Exception:
        raise StageAccessDenied("Stage access denied") from None

    if _grant_rows_allow(grants, database, schema, action):
        return
    if active_role:
        from app.common.identifiers import check_identifier

        pending = [active_role]
        visited: set[str] = set()
        while pending:
            role = pending.pop()
            if role.casefold() in visited:
                continue
            visited.add(role.casefold())
            role_sql = f"SHOW GRANTS FOR ROLE {check_identifier(role, field='role')}"
            try:
                role_rows = (await db.execute_system(role_sql))["rows"]
            except Exception:
                raise StageAccessDenied("Stage access denied") from None
            if _grant_rows_allow(role_rows, database, schema, action):
                return
            for row in role_rows:
                statement = str(row[-1] if isinstance(row, (list, tuple)) else row)
                match = _ROLE_GRANT.match(statement)
                if match:
                    pending.extend(re.findall(r"[A-Za-z_][\w$]*", match.group(1)))
    raise StageAccessDenied("Stage access denied")
