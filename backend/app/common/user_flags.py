"""Per-user account flags stored in the preference metadata (PostgreSQL).

Nova has no user table (StarRocks is the source of truth, AGENTS.md rule #5), so
a per-user flag cannot live on a user row. It lives in the preferences table,
namespaced by a reserved key prefix so it can never collide with a UI preference.

The one flag v1 needs is **``must_change_password``**: an admin creates a user
with a generated password and requires a change at first login. The flag is set at
creation (or by ``ALTER USER … REQUIRE PASSWORD CHANGE``), read at login, and
cleared when the user changes their password.

Every call names its actor. Setting the flag requires an active account or
security administration role (enforced by PostgreSQL RLS); the user may only
clear their own flag, after StarRocks accepted the new password.

This is metadata about the login flow, not a credential: no password is stored
here. The password itself stays in StarRocks.
"""

from __future__ import annotations

from app.common.preference_store import read_value, write_values
from app.modules.access_control.security_context import SecurityContext

#: Reserved preference key. The ``nova.`` prefix keeps it out of the user-facing
#: preference namespace, which uses plain keys.
MUST_CHANGE_PASSWORD = "nova.must_change_password"


async def set_must_change_password(
    username: str, *, required: bool = True, actor: SecurityContext,
) -> None:
    """Set or clear the first-login password-change requirement for ``username``."""
    await write_values(actor, username, {MUST_CHANGE_PASSWORD: "true" if required else "false"})


async def is_must_change_password(actor: SecurityContext) -> bool:
    """True when the actor's principal must change their password at next login.

    A metadata outage must not bypass the recorded password-change requirement.
    The metadata transaction translates dependency failure to service unavailable.
    """
    value = await read_value(actor, actor.principal, MUST_CHANGE_PASSWORD)
    return str(value).lower() == "true"


async def clear_must_change_password(actor: SecurityContext) -> None:
    """Clear the actor's own flag after their new password was accepted.

    An absent flag already means no change is required, so no row is created.
    """
    value = await read_value(actor, actor.principal, MUST_CHANGE_PASSWORD)
    if value is None or value.lower() == "false":
        return
    await set_must_change_password(actor.principal, required=False, actor=actor)


__all__ = [
    "MUST_CHANGE_PASSWORD",
    "clear_must_change_password",
    "is_must_change_password",
    "set_must_change_password",
]
