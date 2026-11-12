"""Startup schema step for Semantic Views.

Semantic Views and their versions live in PostgreSQL (``nova_meta``, revision
0022), created by the metadata migrations rather than at application startup.
The only engine table left here is Superset's Semantic View dataset map
(``NOVA_SYSTEM.CONFIG_SEMANTIC_BI_DATASETS``), which stays on its owner.
"""

from app.core.database import db


async def ensure_semantic_view_schema() -> None:
    """Create the BI dataset map; Semantic View tables need no startup DDL."""
    from app.modules.superset.semantic import MAPPING_DDL

    await db.execute_system(MAPPING_DDL)
