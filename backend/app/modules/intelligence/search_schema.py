"""AI Search control metadata schema.

Indexes, versions, sync state, query logs and evaluation runs live in the
PostgreSQL metadata store (revision 0022), created by the metadata migrations
rather than at application startup. Index payloads (the full-text and vector
projections) stay in the engine and are created per version by
:mod:`app.modules.intelligence.vector_backend` when a build runs.
"""


async def ensure_search_schema() -> None:
    """No-op kept for startup callers: there is no engine-side control schema."""
