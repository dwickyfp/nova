"""Workspace state persistence for the assistant panel toggle.

NOVA-61 T-D1 requires the assistant panel's collapsed state to survive reload
through the existing workspace-state path, not ``localStorage``. This test
pins the field to that path so a later refactor cannot silently drop it.
"""

from __future__ import annotations

from app.modules.workspaces.schemas import WorkspaceStateRequest, WorkspaceTreeResponse


def test_assistant_collapsed_defaults_to_open():
    assert WorkspaceStateRequest().assistant_collapsed is False
    assert WorkspaceTreeResponse(entries=[]).assistant_collapsed is False


def test_assistant_collapsed_round_trips_through_the_request_schema():
    request = WorkspaceStateRequest(assistant_collapsed=True)
    assert request.assistant_collapsed is True


async def test_save_state_persists_the_assistant_preference(monkeypatch, fake_metadata_db):
    from unittest.mock import AsyncMock

    from app.modules.workspaces import service as module
    from app.modules.workspaces.actor import acting_as

    repo = AsyncMock()
    repo.read.return_value = {}
    monkeypatch.setattr(module, "PreferenceRepository", lambda connection: repo)
    with acting_as(USER):
        await module.WorkspaceService().save_state(
            "alice", open_tabs=[], active_tab=None, sidebar_collapsed=False,
            assistant_collapsed=True, last_database=None, last_schema=None, last_role=None,
        )
    owner, changes = repo.change.call_args.args
    assert owner == "alice"
    assert changes[module.WorkspaceService.PREF_ASSISTANT_STATE].value == "true"
    assert len(fake_metadata_db.committed) == 1
    assert fake_metadata_db.committed[0].actor.session_id == "s1"


async def test_get_tree_reports_the_persisted_assistant_preference(monkeypatch, fake_metadata_db):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.modules.workspaces import service as module
    from app.modules.workspaces.actor import acting_as

    repo = AsyncMock()
    repo.read.return_value = {
        module.WorkspaceService.PREF_ASSISTANT_STATE: SimpleNamespace(deleted=False, value="true"),
    }
    files = AsyncMock()
    files.list_entries.return_value = []
    monkeypatch.setattr(module, "PreferenceRepository", lambda connection: repo)
    monkeypatch.setattr(module.WorkspaceService, "_files", property(lambda self: files))
    with acting_as(USER):
        tree = await module.WorkspaceService().get_tree("alice")
    assert tree["assistant_collapsed"] is True
    assert fake_metadata_db.committed[0].read_only


USER = {"username": "alice", "session_id": "s1", "active_role": "ANALYST",
        "assigned_roles": ["ANALYST"]}
