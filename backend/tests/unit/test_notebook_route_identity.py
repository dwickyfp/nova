from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.notebooks import router
from app.modules.notebooks.document import NotebookConflictError, starter_document
from app.modules.notebooks.schemas import NotebookCreate, NotebookEdit
from app.modules.workspaces.actor import acting_as, acting_session
from app.modules.workspaces.spaces import Space


@pytest.mark.parametrize("operation", ["create", "edit"])
@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("conflict", [False, True])
async def test_notebook_writer_preserves_session_and_restores_context(
    monkeypatch, operation, shared, conflict,
):
    user = {
        "username": "editor", "active_role": "ANALYST", "roles": ["ANALYST", "EDITORS"],
        "session_id": "editor-login", "security_context_version": 7,
    }
    previous = {**user, "session_id": "other-login", "security_context_version": 3}
    namespace = "~ws/shared" if shared else "editor"
    space = Space("shared" if shared else "personal", "Notes",
                  "shared" if shared else "personal", namespace,
                  "owner" if shared else "editor", "edit" if shared else "owner")
    resolve = AsyncMock(return_value=space)
    for_entry = AsyncMock(return_value=space)
    monkeypatch.setattr(router.space_service, "resolve", resolve)
    monkeypatch.setattr(router.space_service, "for_entry", for_entry)
    seen = []

    async def write(actual_namespace, *args, **kwargs):
        seen.append(dict(acting_session()))
        assert actual_namespace == namespace
        assert acting_session() == user
        if conflict:
            if operation == "edit":
                raise NotebookConflictError(8)
            raise router.NotebookFormatError("Invalid notebook")
        entry = {"id": "notebook", "name": "notes.ipynb", "path": "notes.ipynb",
                 "parent_path": "", "entry_type": "file"}
        result = (entry, starter_document())
        return (*result, ["cell"]) if operation == "edit" else result

    monkeypatch.setattr(router.notebook_documents, operation, write)
    with acting_as(previous):
        if operation == "create":
            call = router.create_notebook(NotebookCreate(name="notes", space=space.id), user)
        else:
            call = router.edit_notebook(
                "notebook",
                NotebookEdit(base_revision=7, edits=[{"op": "delete", "cell_id": "cell"}]),
                user,
            )
        if conflict:
            with pytest.raises(HTTPException) as error:
                await call
            assert error.value.status_code == (409 if operation == "edit" else 400)
        else:
            result = await call
            assert result["entry"].id == "notebook"
        assert acting_session() == previous
    assert seen == [user]
    if operation == "create":
        resolve.assert_awaited_once_with(user, space.id, edit=True)
    else:
        for_entry.assert_awaited_once_with(user, "notebook", edit=True)
