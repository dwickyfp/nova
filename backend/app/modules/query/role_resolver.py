"""Automatic role fallback for permission-denied statements.

A Nova user can be granted several roles (``ROLE_A``, ``ROLE_B``) and the
session activates exactly one of them. When a statement fails because the
*active* role lacks a privilege, the user may still hold another granted role
that has it. Instead of making the user switch roles and re-run manually, this
module finds a granted role that can access the statement's target object and
returns it so the caller can retry the statement under that role.

Guarantees:

* **Fallback only, never override.** The caller tries the session's active role
  first. This module runs only after the engine refused the statement for lack
  of privilege.
* **Granted roles only.** Candidate roles come from the caller's own
  ``applicable_roles``; a role the user does not hold is never considered.
* **Least privilege.** When several granted roles can access the object, the one
  holding the fewest privileges on it wins, which keeps the retry as narrow as
  possible. The session's active role is never chosen — it already failed.
* **The session is untouched.** This changes the role used for one execution,
  not ``active_role``. The UI keeps showing the role the user selected.
* **Destructive statements are excluded** by the caller before it gets here; this
  module has no opinion on statement kind, it only resolves a role.
"""

from __future__ import annotations

import logging
import re

from app.core.database import db

logger = logging.getLogger(__name__)

#: Engine messages that mean "you lack a privilege", as opposed to a syntax or
#: object-not-found error. StarRocks embeds the numeric code ``5203`` and spells
#: the condition out in prose; both forms are checked because the phrasing
#: varies by version and privilege type.
_PERMISSION_ERROR_MARKERS = (
    "access denied",
    "permission denied",
    "denied",
    "does not have privilege",
    "no privilege",
    "not authorized",
    "5203",
)

#: Privileges that allow reading/writing an object. A role that holds none of
#: these on the target object cannot run the statement.
_WRITE_PRIVILEGES = {"INSERT", "UPDATE", "DELETE", "SELECT", "ALTER", "CREATE", "DROP"}


def is_permission_error(error: str | None) -> bool:
    """True when an engine error means the active role lacked a privilege."""
    if not error:
        return False
    lowered = error.lower()
    return any(marker in lowered for marker in _PERMISSION_ERROR_MARKERS)


#: ``db.table`` / ``db.schema.table`` / ``db.default.table`` — the leading object
#: a statement touches. Deliberately loose: this only needs to identify the
#: *database* to check a role's grants against, not to fully parse the SQL.
_QUALIFIED_TARGET = re.compile(
    r"\b(?:FROM|INTO|TABLE|UPDATE|JOIN|DESC(?:RIBE)?|DELETE\s+FROM)\s+"
    r"`?(?P<db>[A-Za-z_][A-Za-z0-9_]*)`?\s*\.\s*`?(?P<obj>[A-Za-z_][A-Za-z0-9_]*)`?",
    re.IGNORECASE,
)


def extract_target_database(sql: str) -> str | None:
    """Best-effort database name the statement touches.

    Returns ``None`` when no qualified name is present — an unqualified
    statement resolves against the connection's current database, so there is
    nothing to look up and the caller skips fallback.
    """
    match = _QUALIFIED_TARGET.search(sql)
    return match.group("db") if match else None


async def _granted_roles(username: str) -> list[str]:
    """Roles granted to ``username`` (``information_schema.applicable_roles``)."""
    result = await db.execute_system(
        "SELECT ROLE_NAME FROM information_schema.applicable_roles WHERE GRANTEE = %s",
        [username],
    )
    return sorted({str(row[0]) for row in result["rows"] if row and row[0]})


async def _role_privileges_on_database(role: str, database: str) -> list[str]:
    """Privilege types ``role`` holds on ``database`` (or on everything)."""
    result = await db.execute_system(
        "SELECT PRIVILEGE_TYPE, OBJECT_DATABASE FROM sys.grants_to_roles "
        "WHERE GRANTEE = %s",
        [role],
    )
    privileges: list[str] = []
    for row in result["rows"]:
        if not row:
            continue
        privilege = str(row[0]).upper() if row[0] else ""
        object_database = str(row[1]) if len(row) > 1 and row[1] else ""
        # ``OBJECT_DATABASE`` is ``*`` (or empty on some versions) for a global
        # grant; either form covers every database.
        if object_database in ("*", "", database):
            privileges.append(privilege)
    return privileges


async def resolve_fallback_role(
    *,
    username: str,
    sql: str,
    active_role: str | None,
) -> str | None:
    """A granted role — other than ``active_role`` — that can run ``sql``.

    Prefers the role with the fewest privileges on the target database (least
    privilege). Returns ``None`` when the statement names no qualified object,
    the user has no other applicable role, or no granted role holds a relevant
    privilege on the target.
    """
    database = extract_target_database(sql)
    if not database:
        return None

    try:
        candidates = await _granted_roles(username)
    except Exception:
        logger.exception("role fallback: could not list applicable roles")
        return None

    scored: list[tuple[int, str]] = []
    for role in candidates:
        if role == active_role:
            continue
        try:
            privileges = await _role_privileges_on_database(role, database)
        except Exception:
            logger.exception("role fallback: could not read grants for %s", role)
            continue
        relevant = {p for p in privileges if p in _WRITE_PRIVILEGES}
        if relevant:
            scored.append((len(relevant), role))

    if not scored:
        return None
    # Fewest privileges first; name breaks ties for determinism.
    scored.sort(key=lambda item: (item[0], item[1]))
    return scored[0][1]
