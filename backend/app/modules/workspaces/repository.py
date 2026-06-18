from __future__ import annotations

from typing import Any

from app.core.database import db


class WorkspaceRepository:
    async def list_entries(self, username: str) -> list[dict[str, Any]]:
        result = await db.execute_system(
            """
            SELECT id, user_name, parent_path, name, entry_type, object_key,
                   size_bytes, etag, created_at, updated_at
            FROM NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
            WHERE user_name = %s AND is_deleted = false
            ORDER BY parent_path, entry_type, name
            """,
            [username],
        )
        entries = []
        for row in result["rows"]:
            path = self.build_path(row[2], row[3])
            entries.append(
                {
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
            )
        return entries

    async def get_entry(self, username: str, entry_id: str) -> dict[str, Any] | None:
        result = await db.execute_system(
            """
            SELECT id, user_name, parent_path, name, entry_type, object_key,
                   size_bytes, etag, created_at, updated_at
            FROM NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
            WHERE id = %s AND user_name = %s AND is_deleted = false
            """,
            [entry_id, username],
        )
        if not result["rows"]:
            return None
        row = result["rows"][0]
        return {
            "id": row[0],
            "user_name": row[1],
            "parent_path": row[2],
            "name": row[3],
            "path": self.build_path(row[2], row[3]),
            "entry_type": row[4],
            "object_key": row[5],
            "size_bytes": row[6] or 0,
            "etag": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }

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
        await db.execute_system(
            """
            INSERT INTO NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
            (id, user_name, parent_path, name, entry_type, object_key, size_bytes, etag, created_at, updated_at, is_deleted)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), false)
            """,
            [entry_id, username, parent_path, name, entry_type, object_key, size_bytes, etag],
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
        await db.execute_system(
            """
            INSERT INTO NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
            (id, user_name, parent_path, name, entry_type, object_key, size_bytes, etag, created_at, updated_at, is_deleted)
            SELECT id, user_name, %s, %s, entry_type, %s, %s, %s, created_at, NOW(), false
            FROM NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
            WHERE id = %s AND user_name = %s
            """,
            [parent_path, name, object_key, size_bytes, etag, entry_id, username],
        )

    async def soft_delete_entry(self, username: str, entry_id: str) -> None:
        await db.execute_system(
            """
            INSERT INTO NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
            (id, user_name, parent_path, name, entry_type, object_key, size_bytes, etag, created_at, updated_at, deleted_at, is_deleted)
            SELECT id, user_name, parent_path, name, entry_type, object_key, size_bytes, etag,
                   created_at, NOW(), NOW(), true
            FROM NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
            WHERE id = %s AND user_name = %s
            """,
            [entry_id, username],
        )

    async def set_preference(self, username: str, key: str, value: str) -> None:
        await db.execute_system(
            """
            INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES
            (user_name, pref_key, pref_value, updated_at)
            VALUES (%s, %s, %s, NOW())
            """,
            [username, key, value],
        )

    async def get_preferences(self, username: str, keys: list[str]) -> dict[str, str]:
        placeholders = ", ".join(["%s"] * len(keys))
        result = await db.execute_system(
            f"""
            SELECT pref_key, pref_value
            FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES
            WHERE user_name = %s AND pref_key IN ({placeholders})
            """,
            [username, *keys],
        )
        return {row[0]: row[1] for row in result["rows"]}

    @staticmethod
    def build_path(parent_path: str, name: str) -> str:
        return "/".join(part for part in [parent_path.strip("/"), name.strip("/")] if part)


workspace_repository = WorkspaceRepository()
