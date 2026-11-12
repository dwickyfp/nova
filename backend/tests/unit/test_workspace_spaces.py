"""Shared workspaces: who may open, change and manage them."""

from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.core.exceptions import StorageError
from app.core.metadata_scope import current_scope
from app.modules.workspaces import spaces
from app.modules.workspaces.spaces import SpaceService

SPACE = "a" * 32
OWNER = {
    "username": "owner",
    "assigned_roles": ["analyst"],
    "session_id": "s0",
    "active_role": "analyst",
}
EDITOR = {
    "username": "eve",
    "assigned_roles": ["finance", "sales"],
    "session_id": "s1",
    "active_role": "finance",
}
VIEWER = {
    "username": "vic",
    "assigned_roles": ["sales"],
    "session_id": "s2",
    "active_role": "sales",
}
STRANGER = {
    "username": "sam",
    "assigned_roles": ["support"],
    "session_id": "s3",
    "active_role": "support",
}


class FakeRepo:
    def __init__(self):
        self.rows = {
            SPACE: {"id": SPACE, "name": "Revenue", "owner": "owner", "metadata_revision": 1}
        }
        self.share_rows = {SPACE: {"finance": "edit", "sales": "view"}}

    def bound(self, user):
        store = self

        class Bound:
            async def get(self, space_id):
                row = store.rows.get(space_id)
                if row is None:
                    return None
                assigned = user["assigned_roles"]
                granted = [
                    permission
                    for role, permission in store.share_rows.get(space_id, {}).items()
                    if role in assigned
                ]
                permission = (
                    "owner"
                    if row["owner"] == user["username"]
                    else "edit"
                    if "edit" in granted
                    else "view"
                    if "view" in granted
                    else None
                )
                return {**row, "permission": permission} if permission else None

            async def visible(self):
                return [row for key in store.rows if (row := await self.get(key))]

            async def create(self, name):
                key = uuid4().hex
                store.rows[key] = {
                    "id": key,
                    "name": name,
                    "owner": user["username"],
                    "metadata_revision": 1,
                }
                return await self.get(key)

            async def shares(self, space_id):
                return [
                    {"role": role, "permission": permission}
                    for role, permission in store.share_rows.get(space_id, {}).items()
                ]

            async def replace_shares(self, space_id, shares, *, expected_revision):
                row = store.rows[space_id]
                assert row["owner"] == user["username"]
                assert row["metadata_revision"] == expected_revision
                store.share_rows[space_id] = shares
                row["metadata_revision"] += 1

        return Bound()


@pytest.fixture
def service(monkeypatch, fake_metadata_db):
    audits: list[dict] = []

    async def audit(**kwargs):
        audits.append(kwargs)

    async def roles():
        return [{"name": name} for name in ("analyst", "finance", "sales", "support")]

    from app.modules.users.service import user_service

    monkeypatch.setattr(spaces, "write_audit_log", audit)
    monkeypatch.setattr(user_service, "list_roles", roles)
    repository = FakeRepo()

    @asynccontextmanager
    async def bind(connection, user):
        assert current_scope().actor.principal == user["username"]
        yield repository.bound(user)

    monkeypatch.setattr(spaces.WorkspaceSharingRepository, "bind", bind)
    svc = SpaceService()
    svc.audits = audits  # type: ignore[attr-defined]
    return svc


async def test_the_owner_manages_and_role_members_get_their_permission(service):
    assert (await service.resolve(OWNER, SPACE)).permission == "owner"
    assert (await service.resolve(EDITOR, SPACE)).permission == "edit"  # edit wins
    viewer = await service.resolve(VIEWER, SPACE)
    assert viewer.permission == "view" and not viewer.can_edit
    assert viewer.namespace == f"~ws/{SPACE}"


async def test_without_a_matching_role_the_workspace_does_not_exist(service):
    with pytest.raises(StorageError) as missing:
        await service.resolve(STRANGER, SPACE)
    assert missing.value.status_code == 404
    with pytest.raises(StorageError) as odd:
        await service.resolve(OWNER, "../../etc")
    assert odd.value.status_code == 404


async def test_a_viewer_cannot_change_files(service):
    with pytest.raises(StorageError) as refused:
        await service.resolve(VIEWER, SPACE, edit=True)
    assert refused.value.status_code == 403


async def test_the_personal_workspace_is_the_persons_own(service):
    mine = await service.resolve(EDITOR, None)
    assert mine.namespace == "eve" and mine.can_manage
    assert (await service.for_namespace(EDITOR, "eve")).kind == "personal"
    with pytest.raises(StorageError) as other:
        await service.for_namespace(EDITOR, "owner")  # someone else's personal files
    assert other.value.status_code == 404


async def test_only_the_owner_shares_and_only_with_existing_roles(service):
    with pytest.raises(StorageError) as not_owner:
        await service.set_shares(EDITOR, SPACE, [{"role": "support", "permission": "edit"}])
    assert not_owner.value.status_code == 403
    with pytest.raises(StorageError, match="does not exist"):
        await service.set_shares(OWNER, SPACE, [{"role": "nobody", "permission": "view"}])
    with pytest.raises(StorageError, match="view or edit"):
        await service.set_shares(OWNER, SPACE, [{"role": "sales", "permission": "admin"}])
    shares = await service.set_shares(OWNER, SPACE, [{"role": "support", "permission": "view"}])
    assert shares == [{"role": "support", "permission": "view"}]
    assert (await service.resolve(STRANGER, SPACE)).permission == "view"
    with pytest.raises(StorageError):
        await service.resolve(VIEWER, SPACE)  # sales no longer has it
    assert service.audits[-1]["action"] == "share_workspace"
    assert service.audits[-1]["user_name"] == "owner"


async def test_listing_shows_the_personal_workspace_then_shared_ones(service):
    names = [(s.kind, s.permission) for s in await service.visible(VIEWER)]
    assert names == [("personal", "owner"), ("shared", "view")]
    assert [s.kind for s in await service.visible(STRANGER)] == ["personal"]


async def test_creating_a_workspace_makes_the_creator_owner(service):
    space = await service.create(EDITOR, "  Board   pack ")
    assert space.name == "Board pack" and space.permission == "owner"
    with pytest.raises(StorageError):
        await service.create(EDITOR, "a/b")


async def test_a_file_is_opened_through_its_workspace(service, monkeypatch):
    async def namespace_of(entry_id):
        return {"shared-file": f"~ws/{SPACE}", "mine": "vic", "theirs": "owner"}.get(entry_id)

    from app.modules.workspaces.service import workspace_service

    monkeypatch.setattr(workspace_service, "namespace_of", namespace_of)
    assert (await service.for_entry(VIEWER, "shared-file")).kind == "shared"
    assert (await service.for_entry(VIEWER, "mine")).kind == "personal"
    for entry in ("theirs", "missing"):
        with pytest.raises(StorageError) as hidden:
            await service.for_entry(VIEWER, entry)
        assert hidden.value.status_code == 404
    with pytest.raises(StorageError) as refused:
        await service.for_entry(VIEWER, "shared-file", edit=True)
    assert refused.value.status_code == 403
