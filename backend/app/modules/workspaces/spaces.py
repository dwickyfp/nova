"""Personal and shared workspaces, and who may open them.

Every person has a **personal workspace**: their own files, under their
username. A **shared workspace** is a named set of files one person owns and
shares with roles. Anyone who has one of those roles **assigned** (whichever
role is active) sees it, with the permission the share gives: ``view`` or
``edit``. The owner may also rename it, change its shares and delete it.

Files of a shared workspace live in ``CONFIG_WORKSPACE_ENTRIES`` like personal
files, under the namespace ``~ws/<id>`` (usernames never contain ``/``), so
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
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from app.common.audit import write_audit_log
from app.core.database import db
from app.core.exceptions import StorageError
from app.modules.workspaces.repository import workspace_repository

PERSONAL = "personal"
SHARED_PREFIX = "~ws/"
PERMISSIONS = ("view", "edit")
Permission = Literal["owner", "edit", "view"]

_ID = re.compile(r"^[0-9a-f]{32}$")
_NAME_MAX = 120


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


def _assigned_roles(user: dict[str, Any]) -> set[str]:
    roles = user.get("assigned_roles") or user.get("roles") or []
    return {str(role) for role in roles if role}


def _clean_name(name: str) -> str:
    clean = " ".join(str(name or "").split())
    if not clean:
        raise StorageError("Name the workspace", status_code=400)
    if len(clean) > _NAME_MAX:
        raise StorageError(f"A workspace name holds at most {_NAME_MAX} characters",
                           status_code=400)
    if "/" in clean:
        raise StorageError("A workspace name cannot contain /", status_code=400)
    return clean


class SpaceRepository:
    async def get(self, space_id: str) -> dict[str, Any] | None:
        result = await db.execute_system(
            "SELECT id, name, owner, created_at, updated_at FROM NOVA_SYSTEM.CONFIG_WORKSPACES "
            "WHERE id = %s AND is_deleted = false",
            [space_id],
        )
        rows = result.get("rows") or []
        if not rows:
            return None
        row = rows[0]
        return {"id": row[0], "name": row[1], "owner": row[2],
                "created_at": row[3], "updated_at": row[4]}

    async def list_visible(self, username: str, roles: set[str]) -> list[dict[str, Any]]:
        """Workspaces the person owns or that are shared with one of their roles."""
        params: list[Any] = [username]
        role_clause = ""
        if roles:
            placeholders = ", ".join(["%s"] * len(roles))
            role_clause = (
                " OR w.id IN (SELECT workspace_id FROM NOVA_SYSTEM.CONFIG_WORKSPACE_SHARES "
                f"WHERE role_name IN ({placeholders}))"
            )
            params.extend(sorted(roles))
        result = await db.execute_system(
            "SELECT w.id, w.name, w.owner FROM NOVA_SYSTEM.CONFIG_WORKSPACES w "
            f"WHERE w.is_deleted = false AND (w.owner = %s{role_clause}) ORDER BY w.name",
            params,
        )
        return [{"id": r[0], "name": r[1], "owner": r[2]} for r in result.get("rows") or []]

    async def shares(self, space_id: str) -> list[dict[str, Any]]:
        result = await db.execute_system(
            "SELECT role_name, permission, granted_by, granted_at "
            "FROM NOVA_SYSTEM.CONFIG_WORKSPACE_SHARES WHERE workspace_id = %s ORDER BY role_name",
            [space_id],
        )
        return [
            {"role": r[0], "permission": r[1], "granted_by": r[2], "granted_at": r[3]}
            for r in result.get("rows") or []
        ]

    async def insert(self, space_id: str, name: str, owner: str) -> None:
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_WORKSPACES (id, name, owner, created_at, updated_at, "
            "is_deleted) VALUES (%s, %s, %s, NOW(), NOW(), false)",
            [space_id, name, owner],
        )

    async def rename(self, space: dict[str, Any], name: str) -> None:
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_WORKSPACES (id, name, owner, created_at, updated_at, "
            "is_deleted) VALUES (%s, %s, %s, %s, NOW(), false)",
            [space["id"], name, space["owner"], space["created_at"]],
        )

    async def delete(self, space: dict[str, Any]) -> None:
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_WORKSPACES (id, name, owner, created_at, updated_at, "
            "is_deleted) VALUES (%s, %s, %s, %s, NOW(), true)",
            [space["id"], space["name"], space["owner"], space["created_at"]],
        )
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_WORKSPACE_SHARES WHERE workspace_id = %s",
            [space["id"]],
        )

    async def replace_shares(
        self, space_id: str, shares: list[tuple[str, str]], granted_by: str
    ) -> None:
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_WORKSPACE_SHARES WHERE workspace_id = %s", [space_id]
        )
        for role, permission in shares:
            await db.execute_system(
                "INSERT INTO NOVA_SYSTEM.CONFIG_WORKSPACE_SHARES (workspace_id, role_name, "
                "permission, granted_by, granted_at) VALUES (%s, %s, %s, %s, NOW())",
                [space_id, role, permission, granted_by],
            )


class SpaceService:
    def __init__(self, repo: SpaceRepository | None = None) -> None:
        self.repo = repo or SpaceRepository()

    # ── Resolving ────────────────────────────────────────────────────

    def personal(self, user: dict[str, Any]) -> Space:
        username = str(user["username"])
        return Space(PERSONAL, "Personal Workspace", "personal", username, username, "owner")

    async def _shared(self, user: dict[str, Any], row: dict[str, Any]) -> Space | None:
        username = str(user["username"])
        if row["owner"] == username:
            permission: Permission = "owner"
        else:
            roles = _assigned_roles(user)
            granted = {s["permission"] for s in await self.repo.shares(row["id"])
                       if s["role"] in roles}
            if not granted:
                return None
            permission = "edit" if "edit" in granted else "view"
        return Space(row["id"], row["name"], "shared", namespace_of_space(row["id"]),
                     row["owner"], permission)

    async def resolve(
        self, user: dict[str, Any], space_id: str | None, *, edit: bool = False
    ) -> Space:
        """The workspace ``space_id`` for this person, or not found."""
        if not space_id or space_id == PERSONAL:
            return self.personal(user)
        if not _ID.match(space_id):
            raise _not_found()
        row = await self.repo.get(space_id)
        space = await self._shared(user, row) if row else None
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
        namespace = await workspace_repository.namespace_of(entry_id)
        if namespace is None:
            raise StorageError("Workspace entry not found", status_code=404)
        return await self.for_namespace(user, namespace, edit=edit)

    async def visible(self, user: dict[str, Any]) -> list[Space]:
        spaces = [self.personal(user)]
        for row in await self.repo.list_visible(str(user["username"]), _assigned_roles(user)):
            space = await self._shared(user, {**row})
            if space is not None:
                spaces.append(space)
        return spaces

    # ── Managing ─────────────────────────────────────────────────────

    async def create(self, user: dict[str, Any], name: str) -> Space:
        clean = _clean_name(name)
        space_id = uuid.uuid4().hex
        await self.repo.insert(space_id, clean, str(user["username"]))
        await _audit(user, "create_workspace", clean, space_id)
        return Space(space_id, clean, "shared", namespace_of_space(space_id),
                     str(user["username"]), "owner")

    async def _owned(self, user: dict[str, Any], space_id: str) -> tuple[Space, dict[str, Any]]:
        space = await self.resolve(user, space_id)
        if space.kind != "shared":
            raise StorageError("Your personal workspace cannot be changed this way",
                               status_code=400)
        if not space.can_manage:
            raise StorageError("Only the owner can do this", status_code=403)
        row = await self.repo.get(space_id)
        if row is None:
            raise _not_found()
        return space, row

    async def rename(self, user: dict[str, Any], space_id: str, name: str) -> Space:
        space, row = await self._owned(user, space_id)
        clean = _clean_name(name)
        await self.repo.rename(row, clean)
        await _audit(user, "rename_workspace", clean, space_id)
        return Space(space.id, clean, space.kind, space.namespace, space.owner, space.permission)

    async def delete(self, user: dict[str, Any], space_id: str) -> None:
        from app.modules.workspaces.actor import acting_as
        from app.modules.workspaces.service import workspace_service

        space, row = await self._owned(user, space_id)
        with acting_as(str(user["username"])):
            for entry in await workspace_service.list_entries(space.namespace):
                if not entry["parent_path"]:
                    await workspace_service.delete_entry(space.namespace, entry["id"])
        await self.repo.delete(row)
        await _audit(user, "delete_workspace", space.name, space_id)

    async def shares(self, user: dict[str, Any], space_id: str) -> list[dict[str, Any]]:
        space = await self.resolve(user, space_id)
        if space.kind != "shared":
            return []
        return await self.repo.shares(space_id)

    async def set_shares(
        self, user: dict[str, Any], space_id: str, shares: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Replace the roles a workspace is shared with (owner only)."""
        _space, _row = await self._owned(user, space_id)
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
        await self.repo.replace_shares(space_id, sorted(clean.items()), str(user["username"]))
        await _audit(
            user, "share_workspace",
            ", ".join(f"{role}:{permission}" for role, permission in sorted(clean.items()))
            or "(not shared)",
            space_id,
        )
        return await self.repo.shares(space_id)


async def _audit(user: dict[str, Any], action: str, detail: str, space_id: str) -> None:
    await write_audit_log(
        event_type="workspace",
        user_name=str(user["username"]),
        action=action,
        object_type="workspace",
        object_name=f"{space_id}: {detail}"[:500],
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )


space_service = SpaceService()
