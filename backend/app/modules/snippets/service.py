"""Snippet service — business logic for pinned queries."""

import logging

from app.modules.snippets.repository import snippet_repo

logger = logging.getLogger(__name__)


class SnippetService:
    """CRUD operations on saved SQL snippets."""

    async def list_snippets(self, username: str) -> list[dict]:
        return await snippet_repo.list_for_user(username)

    async def create_snippet(
        self,
        *,
        username: str,
        name: str,
        sql_text: str,
        database_name: str | None = None,
        schema_name: str | None = None,
        is_shared: bool = False,
    ) -> str:
        snippet_id = await snippet_repo.insert(
            username=username,
            name=name,
            sql_text=sql_text,
            database_name=database_name,
            schema_name=schema_name,
            is_shared=is_shared,
        )
        logger.info("Snippet created: id=%s user=%s name=%s", snippet_id, username, name)
        return snippet_id

    async def update_snippet(
        self,
        *,
        snippet_id: str,
        username: str,
        name: str | None = None,
        sql_text: str | None = None,
        is_shared: bool | None = None,
    ) -> bool:
        return await snippet_repo.update(
            snippet_id=snippet_id,
            username=username,
            name=name,
            sql_text=sql_text,
            is_shared=is_shared,
        )

    async def delete_snippet(self, snippet_id: str, username: str) -> bool:
        return await snippet_repo.delete(snippet_id, username)


snippet_service = SnippetService()
