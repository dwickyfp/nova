"""Snippet repository — CRUD on NOVA_SYSTEM.CONFIG_PINNED_QUERIES."""

import uuid

from app.core.database import db


class SnippetRepository:
    """Direct StarRocks access for pinned query snippets."""

    async def list_for_user(self, username: str) -> list[dict]:
        result = await db.execute_system(
            "SELECT id, user_name, name, sql_text, database_name, schema_name, "
            "is_shared, created_at FROM NOVA_SYSTEM.CONFIG_PINNED_QUERIES "
            "WHERE user_name = %s OR is_shared = true "
            "ORDER BY created_at DESC",
            [username],
        )
        return [
            {
                "id": row[0],
                "user_name": row[1],
                "name": row[2],
                "sql_text": row[3],
                "database_name": row[4],
                "schema_name": row[5],
                "is_shared": row[6],
                "created_at": str(row[7]) if row[7] else None,
            }
            for row in result["rows"]
        ]

    async def insert(
        self,
        *,
        username: str,
        name: str,
        sql_text: str,
        database_name: str | None = None,
        schema_name: str | None = None,
        is_shared: bool = False,
    ) -> str:
        snippet_id = str(uuid.uuid4())
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_PINNED_QUERIES "
            "(id, user_name, name, sql_text, database_name, schema_name, is_shared) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [snippet_id, username, name, sql_text, database_name, schema_name, is_shared],
        )
        return snippet_id

    async def update(
        self,
        *,
        snippet_id: str,
        username: str,
        name: str | None = None,
        sql_text: str | None = None,
        is_shared: bool | None = None,
    ) -> bool:
        # StarRocks Primary Key table: INSERT overwrites the row with same PK
        # First read the current row
        result = await db.execute_system(
            "SELECT id, user_name, name, sql_text, database_name, schema_name, is_shared "
            "FROM NOVA_SYSTEM.CONFIG_PINNED_QUERIES WHERE id = %s AND user_name = %s",
            [snippet_id, username],
        )
        if not result["rows"]:
            return False
        row = result["rows"][0]
        final_name = name if name is not None else row[2]
        final_sql = sql_text if sql_text is not None else row[3]
        final_shared = is_shared if is_shared is not None else row[6]
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_PINNED_QUERIES "
            "(id, user_name, name, sql_text, database_name, schema_name, is_shared) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [snippet_id, username, final_name, final_sql, row[4], row[5], final_shared],
        )
        return True

    async def delete(self, snippet_id: str, username: str) -> bool:
        # Check if snippet exists first
        check = await db.execute_system(
            "SELECT id FROM NOVA_SYSTEM.CONFIG_PINNED_QUERIES WHERE id = %s AND user_name = %s",
            [snippet_id, username],
        )
        if not check["rows"]:
            return False
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_PINNED_QUERIES WHERE id = %s AND user_name = %s",
            [snippet_id, username],
        )
        return True


snippet_repo = SnippetRepository()
