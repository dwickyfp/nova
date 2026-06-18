"""NOVA_SYSTEM database initialization.

The init-nova.sql creates tables with flat naming:
  CONFIG_STAGES, CONFIG_USER_PREFERENCES, CONFIG_PINNED_QUERIES, AUDIT_LOG, etc.

All persistent state lives in StarRocks NOVA_SYSTEM — no SQLite, no PostgreSQL.
"""

from app.core.database import db


async def init_nova_system() -> None:
    """Verify NOVA_SYSTEM exists and setup marker is present.

    The actual DDL is handled by init-nova.sql in Docker.
    This just ensures the setup_complete preference exists.
    """
    try:
        result = await db.execute_system(
            "SELECT pref_value FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
            "WHERE user_name = '__system__' AND pref_key = 'setup_complete'"
        )
        if not result["rows"]:
            await db.execute_system(
                "INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
                "(user_name, pref_key, pref_value, updated_at) "
                "VALUES ('__system__', 'setup_complete', 'false', NOW())"
            )
    except Exception:
        # Tables may not exist yet if init hasn't run
        pass


async def is_setup_complete() -> bool:
    """Check if the initial admin setup has been completed."""
    try:
        result = await db.execute_system(
            "SELECT pref_value FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
            "WHERE user_name = '__system__' AND pref_key = 'setup_complete'"
        )
        return bool(result["rows"]) and result["rows"][0][0] == "true"
    except Exception:
        return False


async def mark_setup_complete() -> None:
    """Mark the initial setup as complete."""
    await db.execute_system(
        "INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
        "(user_name, pref_key, pref_value, updated_at) "
        "VALUES ('__system__', 'setup_complete', 'true', NOW())"
    )
