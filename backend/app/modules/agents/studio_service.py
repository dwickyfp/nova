"""Nova Studio service — identity, role/warehouse options, and preferences.

Studio is a standalone surface but uses the same session and the same StarRocks
identity. This service answers two questions the Studio Settings panel asks:

* **Who am I and what can I switch between?** Roles come from the user's
  applicable roles; warehouses (resource groups) from the live engine. Both run
  on the caller's connection, so the engine's RBAC decides what is listed.
* **What are my Studio preferences?** Small UI settings. They live in
  ``NOVA_SYSTEM.CONFIG_USER_PREFERENCES`` (Nova has no user table) under
  ``studio.*`` keys, so they cannot collide with the account flags stored there.

No credential is read or written here; preferences are UI metadata only.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.database import db
from app.modules.agents.studio_schemas import (
    StudioIdentity,
    StudioPreferences,
    StudioSettingsResponse,
)

logger = logging.getLogger(__name__)

#: Preference keys. The ``studio.`` prefix keeps them out of the reserved
#: ``nova.`` namespace used for account flags.
_KEY_PREFIX = "studio."
_PREF_KEYS = (
    "theme",
    "language",
    "preferred_name",
    "role",
    "warehouse",
    "extended_thinking",
)

_BOOL_KEYS = {"extended_thinking"}


class StudioService:
    """Identity resolution and preference storage for Nova Studio."""

    async def identity(self, user: dict) -> StudioIdentity:
        """The caller's username, switchable roles, and warehouses."""
        username = user.get("username", "")
        roles = self._roles(user)

        warehouses: list[str] = []
        try:
            from app.modules.resource_groups.service import resource_group_service

            groups = await resource_group_service.list_resource_groups(
                username=username,
                encrypted_password=user.get("encrypted_password", ""),
                session_id=user.get("session_id"),
                role=user.get("active_role") or (roles[0] if roles else None),
            )
            warehouses = sorted(
                str(g.get("name")) for g in groups if g.get("name")
            )
        except Exception:  # noqa: BLE001 - a warehouse list is advisory, not fatal
            logger.warning("Studio: could not list warehouses for %s", username)

        prefs = await self.get_preferences(username)
        return StudioIdentity(
            username=username,
            roles=roles,
            active_role=prefs.role or (roles[0] if roles else None),
            warehouses=warehouses,
            active_warehouse=prefs.warehouse or (warehouses[0] if warehouses else None),
        )

    async def settings(self, user: dict) -> StudioSettingsResponse:
        identity = await self.identity(user)
        preferences = await self.get_preferences(user.get("username", ""))
        return StudioSettingsResponse(identity=identity, preferences=preferences)

    async def get_preferences(self, username: str) -> StudioPreferences:
        """Read Studio preferences, falling back to defaults per field."""
        values: dict[str, Any] = {}
        try:
            result = await db.execute_system(
                "SELECT pref_key, pref_value FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
                "WHERE user_name = %s AND pref_key LIKE %s",
                [username, f"{_KEY_PREFIX}%"],
            )
        except Exception:  # noqa: BLE001 - defaults are safe
            logger.warning("Studio: could not read preferences for %s", username)
            return StudioPreferences()

        for row in result["rows"]:
            key = str(row[0])[len(_KEY_PREFIX) :]
            values[key] = row[1]

        return StudioPreferences(
            theme=_choice(values.get("theme"), ("light", "dark", "system"), "system"),
            language=str(values.get("language") or "en"),
            preferred_name=_str_or_none(values.get("preferred_name")),
            role=_str_or_none(values.get("role")),
            warehouse=_str_or_none(values.get("warehouse")),
            extended_thinking=_bool(values.get("extended_thinking"), True),
        )

    async def update_preferences(
        self, username: str, patch: dict[str, Any]
    ) -> StudioPreferences:
        """Persist the provided Studio preference fields (PATCH semantics)."""
        for key in _PREF_KEYS:
            if key not in patch or patch[key] is None:
                continue
            value = patch[key]
            stored = (
                "true" if value is True else "false" if value is False else str(value)
            )
            await db.execute_system(
                "INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
                "(user_name, pref_key, pref_value, updated_at) "
                "VALUES (%s, %s, %s, NOW())",
                [username, f"{_KEY_PREFIX}{key}", stored],
            )
        return await self.get_preferences(username)

    @staticmethod
    def _roles(user: dict) -> list[str]:
        roles = user.get("roles")
        if isinstance(roles, list) and roles:
            return sorted({str(r) for r in roles if r})
        active = user.get("active_role")
        return [str(active)] if active else []


def _str_or_none(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value).strip().lower() if value is not None else ""
    return text if text in allowed else default  # type: ignore[return-value]


#: Process-wide service.
studio_service = StudioService()
