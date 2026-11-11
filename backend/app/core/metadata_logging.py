"""Fixed logging policy for connections that handle control-plane credentials."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

import psycopg
from psycopg import sql

# Parameter-length limits do not redact values embedded in PostgreSQL's ERROR
# message itself. Workload sessions report sanitized categories through Nova;
# database background processes retain the operator's server logging policy.
METADATA_LOGGING_SETTINGS = MappingProxyType({
    "log_min_messages": "fatal",
    "log_error_verbosity": "terse",
    "log_min_error_statement": "panic",
    "log_statement": "none",
    "log_parameter_max_length": "0",
    "log_parameter_max_length_on_error": "0",
    "log_min_duration_statement": "-1",
    "log_min_duration_sample": "-1",
    "log_transaction_sample_rate": "0",
    "log_duration": "off",
    "debug_print_parse": "off",
    "debug_print_rewritten": "off",
    "debug_print_plan": "off",
})


async def provision_role_logging(connection: psycopg.AsyncConnection[Any], role: str) -> None:
    """Called only by the privileged identity provisioner, never by a workload."""
    statements = [sql.SQL("ALTER ROLE {} SET {} TO {}").format(
        sql.Identifier(role), sql.Identifier(name), sql.Literal(value),
    ) for name, value in METADATA_LOGGING_SETTINGS.items()]
    await connection.execute(sql.SQL("; ").join(statements))


async def logging_is_private(connection: psycopg.AsyncConnection[dict[str, Any]]) -> bool:
    cursor = await connection.execute(
        "SELECT name,setting,context,has_parameter_privilege(current_user,name,'SET') AS can_set "
        "FROM pg_catalog.pg_settings WHERE name=ANY(%s)", (list(METADATA_LOGGING_SETTINGS),),
    )
    rows = await cursor.fetchall()
    return (
        {row["name"]: row["setting"] for row in rows} == dict(METADATA_LOGGING_SETTINGS)
        and not any(row["context"] == "superuser" and row["can_set"] for row in rows)
    )
