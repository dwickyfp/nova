"""Object Browser service — tree navigation + detail views.

Orchestrates metadata queries for the Snowsight-style sidebar:
  Catalog → Database → Schema → Tables/Views/MVs
"""

from app.modules.objects.repository import ObjectRepository


class ObjectService:
    """High-level object browsing operations."""

    def __init__(self):
        self._repo = ObjectRepository()

    async def browse_tree(self) -> list[dict]:
        """Full tree: all databases with their table/view counts.

        Used for the sidebar root expansion.
        """
        databases = await self._repo.list_databases()
        result = []

        for db in databases:
            name = db["name"]
            types = await self._repo.list_table_types(name)
            result.append({
                "name": name,
                "table_count": len(types["tables"]),
                "view_count": len(types["views"]),
                "mv_count": len(types["materialized_views"]),
                "total_objects": len(types["tables"]) + len(types["views"]) + len(types["materialized_views"]),
            })

        return result

    async def list_databases(self) -> list[dict]:
        """List all databases."""
        return await self._repo.list_databases()

    async def get_database_detail(self, name: str) -> dict | None:
        """Get database detail with object counts."""
        db_info = await self._repo.get_database(name)
        if not db_info:
            return None

        types = await self._repo.list_table_types(name)
        db_info["table_count"] = len(types["tables"])
        db_info["view_count"] = len(types["views"])
        db_info["mv_count"] = len(types["materialized_views"])
        return db_info

    async def list_objects(self, database: str, object_type: str = "all") -> dict:
        """List objects in a database, optionally filtered by type.

        Args:
            database: Database name
            object_type: "all", "table", "view", "materialized_view"

        Returns:
            {"tables": [...], "views": [...], "materialized_views": [...]}
        """
        if object_type == "table":
            tables = await self._repo.list_tables(database)
            return {"tables": tables, "views": [], "materialized_views": []}
        elif object_type == "view":
            views = await self._repo.list_views(database)
            return {"tables": [], "views": views, "materialized_views": []}
        elif object_type == "materialized_view":
            mvs = await self._repo.list_materialized_views(database)
            return {"tables": [], "views": [], "materialized_views": mvs}
        else:
            return await self._repo.list_table_types(database)

    async def get_table_detail(self, database: str, table: str) -> dict | None:
        """Get full table detail — columns, DDL, status."""
        return await self._repo.get_table_detail(database, table)

    async def get_view_detail(self, database: str, view: str) -> dict | None:
        """Get view detail — definition, columns."""
        return await self._repo.get_view_detail(database, view)

    async def get_columns(self, database: str, table: str) -> list[dict]:
        """Get column list for a table or view."""
        result = await db.execute_system(f"DESC `{database}`.`{table}`")
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


# Need to import db for get_columns
from app.core.database import db

object_service = ObjectService()
