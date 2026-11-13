"""Personal and shared workspaces, and who may open them.

Every person has a **personal workspace**: their own files, under their
username. A **shared workspace** is a named set of files one person owns and
shares with roles. Anyone who has one of those roles **assigned** (whichever
role is active) sees it, with the permission the share gives: ``view`` or
``edit``. The owner may also rename it, change its shares and delete it.

Files of a shared workspace live in ``nova_meta.config_workspace_entries`` like
personal files, under the namespace ``~ws/<id>`` (usernames never contain ``/``), so
the workspace service, versions and storage work unchanged. This module is the
one place that turns a person and a workspace, or a person and a file, into a
namespace they may use. A workspace or file the person may not open is
reported as not found, so its existence does not leak.

Sharing a workspace shares files (SQL, notebooks, documents), never data:
queries and notebook cells run as the person who runs them, under their own
role, and notebook results stay per person.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from app.common.audit import write_audit_log
from app.core.exceptions import StorageError
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.core.metadata_scope import actor_for, current_scope, metadata_transaction
from app.modules.workspaces.actor import acting_as, declare_session
from app.modules.workspaces.metadata_sharing import (
    WorkspaceSharingRepository,
)
from app.modules.workspaces.metadata_sharing import (
    _clean_name as _clean_name,
)

PERSONAL = "personal"
SHARED_PREFIX = "~ws/"
PERMISSIONS = ("view", "edit")
Permission = Literal["owner", "edit", "view"]

_ID = re.compile(r"^[0-9a-f]{32}$")
_CHANGED = "This workspace changed since you opened it. Reload it and try again."


@dataclass(frozen=True)
class Space:
    id: str
    name: str
    kind: Literal["personal", "shared"]
    namespace: str
    owner: str
    permission: Permission

    @property
    def can_edit(self) -> bool:
        return self.permission in {"owner", "edit"}

    @property
    def can_manage(self) -> bool:
        return self.permission == "owner"

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "owner": self.owner,
            "permission": self.permission,
        }


def namespace_of_space(space_id: str) -> str:
    return f"{SHARED_PREFIX}{space_id}"


def _not_found() -> StorageError:
    return StorageError("Workspace not found", status_code=404)


def _space(row: dict[str, Any] | None) -> Space | None:
    """A visible workspace row as this person sees it; ``None`` when hidden."""
    if row is None or row.get("permission") not in {"owner", "edit", "view"}:
        return None
    return Space(row["id"], row["name"], "shared", namespace_of_space(row["id"]),
                 row["owner"], row["permission"])


@asynccontextmanager
async def _sharing(
    user: dict[str, Any], *, read_only: bool = False,
) -> AsyncIterator[WorkspaceSharingRepository]:
    """Workspace ownership and shares, as this person with their assigned roles.

    It also records the person as the acting session for the Workspace file
    commands the same request runs next.
    """
    declare_session(user)
    try:
        async with (
            metadata_transaction(actor_for(user), read_only=read_only) as scope,
            WorkspaceSharingRepository.bind(scope.connection, user) as repository,
        ):
            yield repository
    except MetadataConflict:
        raise StorageError(_CHANGED, status_code=409) from None
    except MetadataPermissionDenied:
        raise StorageError("Only the owner can do this", status_code=403) from None
    except ValueError as error:
        raise StorageError(str(error) or "Invalid workspace request", status_code=400) from None


class SpaceService:
    # ── Resolving ────────────────────────────────────────────────────

    def personal(self, user: dict[str, Any]) -> Space:
        declare_session(user)
        username = str(user["username"])
        return Space(PERSONAL, "Personal Workspace", "personal", username, username, "owner")

    async def resolve(
        self, user: dict[str, Any], space_id: str | None, *, edit: bool = False
    ) -> Space:
        """The workspace ``space_id`` for this person, or not found."""
        if not space_id or space_id == PERSONAL:
            return self.personal(user)
        if not _ID.match(space_id):
            raise _not_found()
        async with _sharing(user, read_only=True) as repository:
            space = _space(await repository.get(space_id))
        if space is None:
            raise _not_found()
        if edit and not space.can_edit:
            raise StorageError("You can view this workspace but not change it", status_code=403)
        return space

    async def for_namespace(
        self, user: dict[str, Any], namespace: str, *, edit: bool = False
    ) -> Space:
        if namespace == str(user["username"]):
            return self.personal(user)
        if not namespace.startswith(SHARED_PREFIX):
            raise StorageError("Workspace entry not found", status_code=404)
        try:
            return await self.resolve(user, namespace[len(SHARED_PREFIX):], edit=edit)
        except StorageError as exc:
            if exc.status_code == 404:
                raise StorageError("Workspace entry not found", status_code=404) from None
            raise

    async def for_entry(
        self, user: dict[str, Any], entry_id: str, *, edit: bool = False
    ) -> Space:
        """The workspace a file or folder belongs to, if this person may open it."""
        from app.modules.workspaces.service import workspace_service

        declare_session(user)
        namespace = await workspace_service.namespace_of(entry_id)
        if namespace is None:
            raise StorageError("Workspace entry not found", status_code=404)
        return await self.for_namespace(user, namespace, edit=edit)

    async def visible(self, user: dict[str, Any]) -> list[Space]:
        spaces = [self.personal(user)]
        async with _sharing(user, read_only=True) as repository:
            rows = await repository.visible()
        spaces.extend(space for space in map(_space, rows) if space is not None)
        return spaces

    # ── Managing ─────────────────────────────────────────────────────

    async def create(self, user: dict[str, Any], name: str) -> Space:
        clean = _clean_name(name)
        async with _sharing(user) as repository:
            row = await repository.create(clean)
            await _audit("create_workspace", clean, row["id"])
        return Space(row["id"], row["name"], "shared", namespace_of_space(row["id"]),
                     row["owner"], "owner")

    async def _owned(self, user: dict[str, Any], space_id: str) -> Space:
        space = await self.resolve(user, space_id)
        if space.kind != "shared":
            raise StorageError("Your personal workspace cannot be changed this way",
                               status_code=400)
        if not space.can_manage:
            raise StorageError("Only the owner can do this", status_code=403)
        return space

    @staticmethod
    async def _current(repository: WorkspaceSharingRepository, space_id: str) -> dict[str, Any]:
        row = await repository.get(space_id)
        if row is None:
            raise _not_found()
        return row

    async def rename(self, user: dict[str, Any], space_id: str, name: str) -> Space:
        space = await self._owned(user, space_id)
        clean = _clean_name(name)
        async with _sharing(user) as repository:
            row = await self._current(repository, space_id)
            await repository.rename(
                space_id, clean, expected_revision=row["metadata_revision"]
            )
            await _audit("rename_workspace", clean, space_id)
        return Space(space.id, clean, space.kind, space.namespace, space.owner, space.permission)

    async def delete(self, user: dict[str, Any], space_id: str) -> None:
        from app.modules.workspaces.service import workspace_service

        space = await self._owned(user, space_id)
        # Files are retired first, through their owning command, so their
        # objects are drained after each metadata commit.
        with acting_as(user):
            entries = await workspace_service.list_entries(space.namespace)
            for entry in entries:
                if not entry["parent_path"]:
                    await workspace_service.delete_entry(space.namespace, entry["id"])
        from app.modules.workspaces.service import stop_deleted_notebooks

        await stop_deleted_notebooks(str(entry["id"]) for entry in entries)
        async with _sharing(user) as repository:
            row = await self._current(repository, space_id)
            await repository.delete(space_id, expected_revision=row["metadata_revision"])
            await _audit("delete_workspace", space.name, space_id)

    async def shares(self, user: dict[str, Any], space_id: str) -> list[dict[str, Any]]:
        space = await self.resolve(user, space_id)
        if space.kind != "shared":
            return []
        async with _sharing(user, read_only=True) as repository:
            return await repository.shares(space_id)

    async def set_shares(
        self, user: dict[str, Any], space_id: str, shares: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Replace the roles a workspace is shared with (owner only)."""
        await self._owned(user, space_id)
        from app.modules.users.service import user_service

        known = {str(r["name"]) for r in await user_service.list_roles() if r.get("name")}
        clean: dict[str, str] = {}
        for share in shares:
            role = str(share.get("role") or "").strip()
            permission = str(share.get("permission") or "view")
            if permission not in PERMISSIONS:
                raise StorageError("Share with view or edit permission", status_code=400)
            if role not in known:
                raise StorageError(f"Role {role or '(empty)'} does not exist", status_code=400)
            clean[role] = permission
        async with _sharing(user) as repository:
            row = await self._current(repository, space_id)
            await repository.replace_shares(
                space_id, clean, expected_revision=row["metadata_revision"]
            )
            await _audit(
                "share_workspace",
                ", ".join(f"{role}:{permission}" for role, permission in sorted(clean.items()))
                or "(not shared)",
                space_id,
            )
            return await repository.shares(space_id)


async def _audit(action: str, detail: str, space_id: str) -> None:
    """The success audit, in the command's metadata transaction."""
    actor = current_scope().actor
    await write_audit_log(
        event_type="workspace",
        user_name=actor.principal,
        action=action,
        object_type="workspace",
        object_name=f"{space_id}: {detail}"[:500],
        status="SUCCESS",
        session_id=actor.session_id,
        active_role=actor.active_role,
        security_context_version=actor.security_context_version,
    )


space_service = SpaceService()
