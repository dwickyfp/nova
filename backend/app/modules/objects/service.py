"""Object Browser service — tree navigation + detail views.

Orchestrates metadata queries for the Snowsight-style sidebar:
  Catalog → Database → Schema → Tables/Views/MVs
"""

from app.modules.objects.repository import ObjectRepository


class ObjectService:
    """High-level object browsing operations."""

    def __init__(self):
        self._repo = ObjectRepository()

    async def browse_tree(
        self,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> list[dict]:
        """Full tree: all databases with their table/view counts.

        Used for the sidebar root expansion.
        """
        databases = await self._repo.list_databases(
            username=username,
            encrypted_password=encrypted_password,
            role=role,
        )
        result = []

        for db in databases:
            name = db["name"]
            types = await self._repo.list_table_types(
                name,
                username=username,
                encrypted_password=encrypted_password,
                role=role,
            )
            result.append({
                "name": name,
                "table_count": len(types["tables"]),
                "view_count": len(types["views"]),
                "mv_count": len(types["materialized_views"]),
                "total_objects": len(types["tables"]) + len(types["views"]) + len(types["materialized_views"]),
            })

        return result

    async def list_databases(
        self,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> list[dict]:
        """List all databases."""
        return await self._repo.list_databases(
            username=username,
            encrypted_password=encrypted_password,
            role=role,
        )

    async def get_database_detail(
        self,
        name: str,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> dict | None:
        """Get database detail with object counts."""
        db_info = await self._repo.get_database(
            name,
            username=username,
            encrypted_password=encrypted_password,
            role=role,
        )
        if not db_info:
            return None

        types = await self._repo.list_table_types(
            name,
            username=username,
            encrypted_password=encrypted_password,
            role=role,
        )
        db_info["table_count"] = len(types["tables"])
        db_info["view_count"] = len(types["views"])
        db_info["mv_count"] = len(types["materialized_views"])
        return db_info

    async def list_objects(
        self,
        database: str,
        object_type: str = "all",
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> dict:
        """List objects in a database, optionally filtered by type.

        Args:
            database: Database name
            object_type: "all", "table", "view", "materialized_view"

        Returns:
            {"tables": [...], "views": [...], "materialized_views": [...]}
        """
        if object_type == "table":
            tables = await self._repo.list_tables(
                database,
                username=username,
                encrypted_password=encrypted_password,
                role=role,
            )
            return {"tables": tables, "views": [], "materialized_views": []}
        elif object_type == "view":
            views = await self._repo.list_views(
                database,
                username=username,
                encrypted_password=encrypted_password,
                role=role,
            )
            return {"tables": [], "views": views, "materialized_views": []}
        elif object_type == "materialized_view":
            mvs = await self._repo.list_materialized_views(
                database,
                username=username,
                encrypted_password=encrypted_password,
                role=role,
            )
            return {"tables": [], "views": [], "materialized_views": mvs}
        else:
            return await self._repo.list_table_types(
                database,
                username=username,
                encrypted_password=encrypted_password,
                role=role,
            )

    async def get_table_detail(
        self,
        database: str,
        table: str,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> dict | None:
        """Get full table detail — columns, DDL, status."""
        return await self._repo.get_table_detail(
            database,
            table,
            username=username,
            encrypted_password=encrypted_password,
            role=role,
        )

    async def get_view_detail(
        self,
        database: str,
        view: str,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> dict | None:
        """Get view detail — definition, columns."""
        return await self._repo.get_view_detail(
            database,
            view,
            username=username,
            encrypted_password=encrypted_password,
            role=role,
        )

    async def get_columns(
        self,
        database: str,
        table: str,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> list[dict]:
        """Get column list for a table or view."""
        result = await self._repo._execute_user(
            f"DESC `{database}`.`{table}`",
            username=username,
            encrypted_password=encrypted_password,
            role=role,
            database=database,
        )
        columns = []
        for row in result["rows"]:
            columns.append({
                "name": row[0],
                "type": row[1],
                "null": row[2],
                "key": row[3],
                "default": row[4],
                "extra": row[5] if len(row) > 5 else "",
            })
        return columns

    async def list_schemas(
        self,
        database: str,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> list[dict]:
        database_info = await self._repo.get_database(
            database,
            username=username,
            encrypted_password=encrypted_password,
            role=role,
        )
        if not database_info:
            return []

        result = await db.execute_system(
            """
            SELECT DISTINCT schema_name
            FROM NOVA_SYSTEM.CONFIG_STAGES
            WHERE database_name = %s
            ORDER BY schema_name
            """,
            [database],
        )
        schemas = [{"name": row[0]} for row in result["rows"] if row[0]]
        if not schemas:
            schemas.append({"name": "default"})
        return schemas

    async def get_schema_tree(
        self,
        database: str,
        schema: str,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> dict:
        # StarRocks objects are database-scoped in Nova today, while stages are
        # schema-scoped. To avoid rendering the same table/view list under every
        # schema node, only surface database objects under the default schema.
        objects = {"tables": [], "views": [], "materialized_views": []}
        if schema == "default":
            objects = await self.list_objects(
                database,
                username=username,
                encrypted_password=encrypted_password,
                role=role,
            )
        stage_rows = await db.execute_system(
            """
            SELECT id, name
            FROM NOVA_SYSTEM.CONFIG_STAGES
            WHERE database_name = %s AND schema_name = %s
            ORDER BY name
            """,
            [database, schema],
        )
        return {
            "database": database,
            "schema": schema,
            "tables": objects["tables"],
            "views": objects["views"],
            "materialized_views": objects["materialized_views"],
            "stages": [{"id": row[0], "name": row[1], "type": "STAGE"} for row in stage_rows["rows"]],
        }


# Need to import db for get_columns
from app.core.database import db

object_service = ObjectService()
