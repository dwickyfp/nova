"""Which securables one caller may see, decided by the engine as that caller.

A live securable is visible when the caller's own session lists it under their
active role (:mod:`.visibility`, which Ranger filters):

* a catalog when ``SHOW CATALOGS`` lists it;
* a database when ``SHOW DATABASES FROM`` its catalog lists it;
* a relation when ``SHOW FULL TABLES`` of its database lists it;
* a column when its relation is visible. Column grants and masks still apply to
  every query; listing a column name follows the relation, as in the Explorer.

A dropped securable has no engine object left to ask about, and Nova must not
evaluate Ranger policies itself, so dropped securables are shown only to an
active security-admin role. This fails closed: nobody else learns that a
dropped name existed.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.access_control.service import AccessControlError, AccessControlService

from . import visibility
from .securables import Securable

#: At most this many databases are asked about per call; the rest stay hidden.
MAX_DATABASES_CHECKED = 64


def is_security_admin(user: dict) -> bool:
    try:
        AccessControlService.require_security_admin(SecurityContext.from_session(user))
    except (AccessControlError, SecurityContextError):
        return False
    return True


class CallerView:
    """One caller's engine view, resolved lazily and reused within a request."""

    def __init__(self, user: dict) -> None:
        self.user = user
        self.admin = is_security_admin(user)
        self._catalogs: dict[str, set[str]] | None = None
        self._relations: dict[tuple[str, str], set[str]] = {}

    async def catalogs(self) -> dict[str, set[str]]:
        if self._catalogs is None:
            self._catalogs = {
                str(item["name"]): set(item.get("databases") or [])
                for item in await visibility.visible_catalogs(self.user)
            }
        return self._catalogs

    async def relations(self, catalog: str, database: str) -> set[str]:
        key = (catalog, database)
        if key not in self._relations:
            if len(self._relations) >= MAX_DATABASES_CHECKED:
                return set()
            databases = (await self.catalogs()).get(catalog)
            if databases is None or database not in databases:
                self._relations[key] = set()
            else:
                self._relations[key] = set(
                    await visibility.visible_relations(self.user, catalog, database)
                )
        return self._relations[key]

    async def can_see(self, securable: Securable) -> bool:
        if securable.dropped_at is not None:
            return self.admin
        catalogs = await self.catalogs()
        if securable.catalog_name not in catalogs:
            return False
        if securable.securable_type == "catalog":
            return True
        if securable.database_name not in catalogs[securable.catalog_name]:
            return False
        if securable.securable_type == "database":
            return True
        return securable.object_name in await self.relations(
            securable.catalog_name, securable.database_name
        )

    async def visible(self, securables: Iterable[Securable]) -> list[Securable]:
        """``securables`` the caller may see, in order."""
        return [row for row in securables if await self.can_see(row)]
