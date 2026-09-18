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


async def test_save_state_persists_the_assistant_preference(monkeypatch):
    """The service writes the assistant state under its own pref key."""
    from app.modules.workspaces.service import WorkspaceService

    written: dict[str, str] = {}

    async def fake_set_preference(username, key, value):
        written[key] = value

    service = WorkspaceService()
    monkeypatch.setattr(service._repo, "set_preference", fake_set_preference)

    await service.save_state(
        "alice",
        open_tabs=[],
        active_tab=None,
        sidebar_collapsed=False,
        assistant_collapsed=True,
        last_database=None,
        last_schema=None,
        last_role=None,
    )

    assert written[WorkspaceService.PREF_ASSISTANT_STATE] == "true"


async def test_get_tree_reports_the_persisted_assistant_preference(monkeypatch):
    from app.modules.workspaces.service import WorkspaceService

    service = WorkspaceService()

    async def fake_list_entries(username):
        return []

    async def fake_get_preferences(username, keys):
        return {WorkspaceService.PREF_ASSISTANT_STATE: "true"}

    monkeypatch.setattr(service._repo, "list_entries", fake_list_entries)
    monkeypatch.setattr(service._repo, "get_preferences", fake_get_preferences)

    tree = await service.get_tree("alice")
    assert tree["assistant_collapsed"] is True
