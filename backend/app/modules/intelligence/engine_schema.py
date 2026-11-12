"""Verify the migrated Intelligence metadata authority at startup."""

from app.core.metadata_runtime import metadata_db


async def ensure_engine_schema() -> None:
    await metadata_db().probe()
