from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, TypeVar

import asyncmy.errors

from app.core.database import db
from app.core.exceptions import WorkspaceNotReadyError

T = TypeVar("T")

#: Engine errors that mean "NOVA_SYSTEM / the workspace table is not usable
#: yet", as opposed to a fault in the statement itself. ``ProgrammingError``
#: covers StarRocks' "table is not found" (error 1064 surfaces as
#: ``ProgrammingError`` in this driver — see NOVA-137's captured traceback),
#: ``OperationalError``/``InterfaceError`` cover the engine being down or the
#: pool not initialised. All are bootstrap-state failures the user can act on,
#: so they are translated to a typed 503 rather than a bare 500.
_NOT_READY_ERRORS = (
    asyncmy.errors.ProgrammingError,
    asyncmy.errors.OperationalError,
    asyncmy.errors.InterfaceError,
)

_WORKSPACE_ENTRY_COLUMNS = (
    "id, user_name, parent_path, name, entry_type, object_key, "
    "size_bytes, etag, created_at, updated_at"
)

_SELECT_ENTRIES = f"""
    SELECT {_WORKSPACE_ENTRY_COLUMNS}
    FROM NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
    WHERE user_name = %s AND is_deleted = false
    ORDER BY parent_path, entry_type, name
"""

_SELECT_ENTRY = f"""
    SELECT {_WORKSPACE_ENTRY_COLUMNS}
    FROM NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
    WHERE id = %s AND user_name = %s AND is_deleted = false
"""

_INSERT_ENTRY = """
    INSERT INTO NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
    (id, user_name, parent_path, name, entry_type, object_key,
     size_bytes, etag, created_at, updated_at, is_deleted)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), false)
"""

_UPDATE_ENTRY = """
    INSERT INTO NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
    (id, user_name, parent_path, name, entry_type, object_key,
     size_bytes, etag, created_at, updated_at, is_deleted)
    SELECT id, user_name, %s, %s, entry_type, %s, %s, %s, created_at, NOW(), false
    FROM NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
    WHERE id = %s AND user_name = %s
"""

_SOFT_DELETE_ENTRY = """
    INSERT INTO NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
    (id, user_name, parent_path, name, entry_type, object_key,
     size_bytes, etag, created_at, updated_at, deleted_at, is_deleted)
    SELECT id, user_name, parent_path, name, entry_type, object_key,
           size_bytes, etag, created_at, NOW(), NOW(), true
    FROM NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
    WHERE id = %s AND user_name = %s
"""

_SET_PREFERENCE = """
    INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES
    (user_name, pref_key, pref_value, updated_at)
    VALUES (%s, %s, %s, NOW())
"""

_GET_PREFERENCES = """
    SELECT pref_key, pref_value
    FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES
    WHERE user_name = %s AND pref_key IN ({placeholders})
"""

_VERSION_COLUMNS = "id, entry_id, user_name, version, object_key, size_bytes, etag, created_at"

#: The next version number for an entry. ``MAX`` over the PK table is exact
#: because a save writes at most one row per entry per call and the table is
#: single-writer per (user, entry) in practice.
_SELECT_NEXT_VERSION = """
    SELECT COALESCE(MAX(version), 0) + 1
    FROM NOVA_SYSTEM.CONFIG_WORKSPACE_FILE_VERSIONS
    WHERE entry_id = %s AND user_name = %s
"""

_SELECT_VERSIONS = f"""
    SELECT {_VERSION_COLUMNS}
    FROM NOVA_SYSTEM.CONFIG_WORKSPACE_FILE_VERSIONS
    WHERE entry_id = %s AND user_name = %s
    ORDER BY version DESC
    LIMIT %s
"""

_SELECT_VERSION = f"""
    SELECT {_VERSION_COLUMNS}
    FROM NOVA_SYSTEM.CONFIG_WORKSPACE_FILE_VERSIONS
    WHERE entry_id = %s AND user_name = %s AND version = %s
"""

_INSERT_VERSION = """
    INSERT INTO NOVA_SYSTEM.CONFIG_WORKSPACE_FILE_VERSIONS
    (id, entry_id, user_name, version, object_key, size_bytes, etag, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
"""

_DELETE_VERSIONS_FOR_ENTRY = """
    DELETE FROM NOVA_SYSTEM.CONFIG_WORKSPACE_FILE_VERSIONS
    WHERE entry_id = %s AND user_name = %s
"""


async def _system_query(coro: Awaitable[T]) -> T:
    """Await a system query, mapping bootstrap failures to a typed 503."""
    try:
        return await coro
    except _NOT_READY_ERRORS as exc:
        raise WorkspaceNotReadyError(
            "Workspace storage is not ready: NOVA_SYSTEM is unavailable or "
            "the workspace tables have not been initialised. An administrator "
            "must complete Nova's initial setup."
        ) from exc


def _to_entry(row: list[Any], path: str) -> dict[str, Any]:
    return {
        "id": row[0],
        "user_name": row[1],
        "parent_path": row[2],
        "name": row[3],
        "path": path,
        "entry_type": row[4],
        "object_key": row[5],
        "size_bytes": row[6] or 0,
        "etag": row[7],
        "created_at": row[8],
        "updated_at": row[9],
    }


def _to_version(row: list[Any]) -> dict[str, Any]:
    return {
        "id": row[0],
        "entry_id": row[1],
        "user_name": row[2],
        "version": row[3],
        "object_key": row[4],
        "size_bytes": row[5] or 0,
        "etag": row[6],
        "created_at": row[7],
    }


class WorkspaceRepository:
    async def list_entries(self, username: str) -> list[dict[str, Any]]:
        result = await _system_query(db.execute_system(_SELECT_ENTRIES, [username]))
        return [_to_entry(row, self.build_path(row[2], row[3])) for row in result["rows"]]

    async def get_entry(self, username: str, entry_id: str) -> dict[str, Any] | None:
        result = await _system_query(db.execute_system(_SELECT_ENTRY, [entry_id, username]))
        if not result["rows"]:
            return None
        row = result["rows"][0]
        return _to_entry(row, self.build_path(row[2], row[3]))

    async def insert_entry(
        self,
        *,
        entry_id: str,
        username: str,
        parent_path: str,
        name: str,
        entry_type: str,
        object_key: str | None,
        size_bytes: int = 0,
        etag: str | None = None,
    ) -> None:
        await _system_query(
            db.execute_system(
                _INSERT_ENTRY,
                [entry_id, username, parent_path, name, entry_type, object_key, size_bytes, etag],
            )
        )

    async def update_entry(
        self,
        *,
        entry_id: str,
        username: str,
        parent_path: str,
        name: str,
        object_key: str | None,
        size_bytes: int,
        etag: str | None,
    ) -> None:
        await _system_query(
            db.execute_system(
                _UPDATE_ENTRY,
                [parent_path, name, object_key, size_bytes, etag, entry_id, username],
            )
        )

    async def soft_delete_entry(self, username: str, entry_id: str) -> None:
        await _system_query(db.execute_system(_SOFT_DELETE_ENTRY, [entry_id, username]))

    async def set_preference(self, username: str, key: str, value: str) -> None:
        await _system_query(db.execute_system(_SET_PREFERENCE, [username, key, value]))

    async def get_preferences(self, username: str, keys: list[str]) -> dict[str, str]:
        placeholders = ", ".join(["%s"] * len(keys))
        result = await _system_query(
            db.execute_system(
                _GET_PREFERENCES.format(placeholders=placeholders),
                [username, *keys],
            )
        )
        return {row[0]: row[1] for row in result["rows"]}

    async def next_version_number(self, username: str, entry_id: str) -> int:
        result = await _system_query(
            db.execute_system(_SELECT_NEXT_VERSION, [entry_id, username])
        )
        return int(result["rows"][0][0]) if result["rows"] else 1

    async def insert_version(
        self,
        *,
        version_id: str,
        entry_id: str,
        username: str,
        version: int,
        object_key: str,
        size_bytes: int,
        etag: str | None,
    ) -> None:
        await _system_query(
            db.execute_system(
                _INSERT_VERSION,
                [version_id, entry_id, username, version, object_key, size_bytes, etag],
            )
        )

    async def list_versions(
        self, username: str, entry_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        result = await _system_query(
            db.execute_system(_SELECT_VERSIONS, [entry_id, username, limit])
        )
        return [_to_version(row) for row in result["rows"]]

    async def get_version(
        self, username: str, entry_id: str, version: int
    ) -> dict[str, Any] | None:
        result = await _system_query(
            db.execute_system(_SELECT_VERSION, [entry_id, username, version])
        )
        if not result["rows"]:
            return None
        return _to_version(result["rows"][0])

    async def delete_versions_for_entry(self, username: str, entry_id: str) -> None:
        await _system_query(
            db.execute_system(_DELETE_VERSIONS_FOR_ENTRY, [entry_id, username])
        )

    @staticmethod
    def build_path(parent_path: str, name: str) -> str:
        return "/".join(part for part in [parent_path.strip("/"), name.strip("/")] if part)


workspace_repository = WorkspaceRepository()
