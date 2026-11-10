"""Shared workspaces: who may open, change and manage them."""

from __future__ import annotations

import pytest

from app.core.exceptions import StorageError
from app.modules.workspaces import spaces
from app.modules.workspaces.spaces import SpaceService

SPACE = "a" * 32
OWNER = {"username": "owner", "assigned_roles": ["analyst"], "session_id": "s0"}
EDITOR = {"username": "eve", "assigned_roles": ["finance", "sales"], "session_id": "s1"}
VIEWER = {"username": "vic", "assigned_roles": ["sales"], "session_id": "s2"}
STRANGER = {"username": "sam", "assigned_roles": ["support"], "session_id": "s3"}


class FakeRepo:
    def __init__(self) -> None:
        self.rows = {SPACE: {"id": SPACE, "name": "Revenue", "owner": "owner",
                             "created_at": None, "updated_at": None}}
        self.share_rows = [{"role": "finance", "permission": "edit"},
                           {"role": "sales", "permission": "view"}]

    async def get(self, space_id):
        return self.rows.get(space_id)

    async def shares(self, space_id):
        return list(self.share_rows)

    async def list_visible(self, username, roles):
        return [row for row in self.rows.values()
                if row["owner"] == username or roles & {s["role"] for s in self.share_rows}]

    async def replace_shares(self, space_id, shares, granted_by):
        self.share_rows = [{"role": r, "permission": p} for r, p in shares]

    async def insert(self, space_id, name, owner):
        self.rows[space_id] = {"id": space_id, "name": name, "owner": owner,
                               "created_at": None, "updated_at": None}


@pytest.fixture
def service(monkeypatch):
    audits: list[dict] = []

    async def audit(**kwargs):
        audits.append(kwargs)

    async def roles():
        return [{"name": name} for name in ("analyst", "finance", "sales", "support")]

    from app.modules.users.service import user_service

    monkeypatch.setattr(spaces, "write_audit_log", audit)
    monkeypatch.setattr(user_service, "list_roles", roles)
    svc = SpaceService(FakeRepo())
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

    monkeypatch.setattr(spaces.workspace_repository, "namespace_of", namespace_of)
    assert (await service.for_entry(VIEWER, "shared-file")).kind == "shared"
    assert (await service.for_entry(VIEWER, "mine")).kind == "personal"
    for entry in ("theirs", "missing"):
        with pytest.raises(StorageError) as hidden:
            await service.for_entry(VIEWER, entry)
        assert hidden.value.status_code == 404
    with pytest.raises(StorageError) as refused:
        await service.for_entry(VIEWER, "shared-file", edit=True)
    assert refused.value.status_code == 403
