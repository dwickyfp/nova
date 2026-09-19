"""Per-user account flags stored in ``NOVA_SYSTEM.CONFIG_USER_PREFERENCES``.

Nova has no user table (StarRocks is the source of truth, AGENTS.md rule #5), so
a per-user flag cannot live on a user row. It lives in the preferences table,
namespaced by a reserved key prefix so it can never collide with a UI preference.

The one flag v1 needs is **``must_change_password``**: an admin creates a user
with a generated password and requires a change at first login. The flag is set at
creation (or by ``ALTER USER … REQUIRE PASSWORD CHANGE``), read at login, and
cleared when the user changes their password.

This is metadata about the login flow, not a credential: no password is stored
here. The password itself stays in StarRocks.
"""

from __future__ import annotations

from app.core.database import db

#: Reserved preference key. The ``nova.`` prefix keeps it out of the user-facing
#: preference namespace, which uses plain keys.
MUST_CHANGE_PASSWORD = "nova.must_change_password"

#: Preference owner used for system-wide (non-user) markers. Reused so a future
#: flag has one obvious home.
_SYSTEM_OWNER = "__system__"


async def set_must_change_password(username: str, *, required: bool = True) -> None:
    """Set or clear the first-login password-change requirement for ``username``."""
    await db.execute_system(
        "INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
        "(user_name, pref_key, pref_value, updated_at) "
        "VALUES (%s, %s, %s, NOW())",
        [username, MUST_CHANGE_PASSWORD, "true" if required else "false"],
    )


async def is_must_change_password(username: str) -> bool:
    """True when ``username`` must change their password at next login.

    Fails closed to ``False`` on a store error: a metadata outage must not lock
    every user out of login. The flag is a convenience gate on the change-password
    screen, not an authorization boundary — StarRocks credentials are.
    """
    try:
        result = await db.execute_system(
            "SELECT pref_value FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
            "WHERE user_name = %s AND pref_key = %s",
            [username, MUST_CHANGE_PASSWORD],
        )
    except Exception:
        return False
    if not result["rows"]:
        return False
    return str(result["rows"][0][0]).lower() == "true"


async def clear_must_change_password(username: str) -> None:
    """Clear the flag after the user sets a new password."""
    await set_must_change_password(username, required=False)


__all__ = [
    "MUST_CHANGE_PASSWORD",
    "clear_must_change_password",
    "is_must_change_password",
    "set_must_change_password",
]
