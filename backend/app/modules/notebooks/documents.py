"""Notebook documents stored as Workspace files.

``WorkspaceService`` stays the single writer of the file and its version
history; this service owns the notebook format on top of it: creating a
notebook, reading it, and applying structural edits atomically against the
revision the client saw. Concurrent edits from two tabs serialize on a Redis
lock and the later one is refused with the current revision instead of
silently overwriting the earlier one.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from app.common.audit import write_audit_log
from app.core.exceptions import StorageError
from app.core.redis import session_store
from app.modules.notebooks.document import (
    NotebookConflictError,
    NotebookDocument,
    NotebookFormatError,
    apply_edits,
    copy_document,
    parse_ipynb,
    starter_document,
    to_ipynb,
)
from app.modules.workspaces.service import workspace_service

logger = logging.getLogger(__name__)

NOTEBOOK_EXTENSION = ".ipynb"
_LOCK_PREFIX = "nova:nb:doc-lock:"


def is_notebook_name(name: str) -> bool:
    return name.lower().endswith(NOTEBOOK_EXTENSION)


@asynccontextmanager
async def _document_lock(entry_id: str):
    """Serialize edits of one notebook across processes."""
    client = session_store._redis
    if client is None:  # pragma: no cover - the app always initialises Redis
        raise StorageError("Notebook editing is not available right now", status_code=503)
    lock = client.lock(_LOCK_PREFIX + entry_id, timeout=30, blocking_timeout=10)
    acquired = await lock.acquire()
    if not acquired:
        raise StorageError("The notebook is busy saving; try again", status_code=503)
    try:
        yield
    finally:
        try:
            await lock.release()
        except Exception:  # noqa: BLE001 - an expired lock needs no release
            logger.debug("Notebook lock already released", exc_info=True)


class NotebookDocumentService:
    async def create(
        self,
        username: str,
        *,
        parent_path: str,
        name: str,
        content: str | None = None,
    ) -> tuple[dict[str, Any], NotebookDocument]:
        """Create a notebook file; ``content`` imports an existing ``.ipynb``."""
        if not is_notebook_name(name):
            name = f"{name}{NOTEBOOK_EXTENSION}"
        document = parse_ipynb(content) if content is not None else starter_document()
        document.revision = 1
        entry = await workspace_service.create_file(
            username, parent_path, name, to_ipynb(document)
        )
        await write_audit_log(
            event_type="notebook",
            user_name=username,
            action="import" if content is not None else "create",
            object_type="notebook",
            object_name=entry["path"],
            status="SUCCESS",
        )
        return entry, document

    async def get(self, username: str, entry_id: str) -> tuple[dict[str, Any], NotebookDocument]:
        entry, text = await workspace_service.get_file(username, entry_id)
        if not is_notebook_name(entry["name"]):
            raise StorageError("This file is not a notebook", status_code=400)
        return entry, parse_ipynb(text)

    async def edit(
        self,
        username: str,
        entry_id: str,
        *,
        base_revision: int,
        edits: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], NotebookDocument, list[str]]:
        """Apply ``edits`` made against ``base_revision`` and save the result.

        Returns the saved entry, the new document and the ids of cells whose
        runnable source changed (their earlier outputs are now stale).
        """
        async with _document_lock(entry_id):
            _entry, current = await self.get(username, entry_id)
            if current.revision != base_revision:
                raise NotebookConflictError(current.revision)
            working = copy_document(current)
            changed = apply_edits(working, edits)
            working.revision = current.revision + 1
            saved = await workspace_service.update_file(username, entry_id, to_ipynb(working))
        return saved, working, changed

    async def export(self, username: str, entry_id: str) -> tuple[str, str]:
        """The notebook as nbformat 4.5 text plus a download file name."""
        entry, document = await self.get(username, entry_id)
        return entry["name"], to_ipynb(document)


notebook_documents = NotebookDocumentService()

__all__ = [
    "NotebookConflictError",
    "NotebookDocumentService",
    "NotebookFormatError",
    "is_notebook_name",
    "notebook_documents",
]
