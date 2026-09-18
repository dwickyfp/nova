"""Index metadata reads on the caller's StarRocks connection.

``SHOW INDEX FROM`` is the documented way to list a table's indexes (StarRocks
has no ``information_schema.statistics`` equivalent Nova can rely on across
versions). Like every other metadata read in the tree, it runs on the caller's
connection so StarRocks RBAC decides visibility — never the root pool.
"""

from __future__ import annotations

from typing import Any

import asyncmy


class IndexRepository:
    """Read a table's indexes from ``SHOW INDEX``."""

    async def list_indexes(
        self,
        conn: asyncmy.Connection,
        database: str,
        table: str,
    ) -> list[dict[str, Any]]:
        """Return one dict per index on ``database.table``.

        ``SHOW INDEX`` returns one row per indexed column; a composite index
        therefore appears once per column. Rows are grouped by index name so the
        API surface matches the index, not its columns, and the column list is
        preserved. Column positions come from the engine's ``Seq_in_index``.
        """
        async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
            await cur.execute(f"SHOW INDEX FROM `{database}`.`{table}`")
            rows = await cur.fetchall()

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            name = str(row.get("Key_name") or row.get("Index_name") or "")
            if not name:
                continue
            entry = grouped.setdefault(
                name,
                {
                    "name": name,
                    "columns": [],
                    "type": str(row.get("Index_type") or ""),
                    "comment": str(row.get("Comment") or ""),
                },
            )
            column = row.get("Column_name")
            if column is not None and str(column) not in entry["columns"]:
                entry["columns"].append(str(column))

        return list(grouped.values())


index_repository = IndexRepository()
