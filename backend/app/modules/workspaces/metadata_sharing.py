"""Workspace ownership and shares in the caller's PostgreSQL transaction.

The authenticated session supplies assigned roles for file sharing. Data queries
retain the actor's single active role. ``SpaceService`` is the runtime caller;
this repository never falls back to StarRocks or writes file objects.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, Self
from uuid import uuid4

import psycopg
from psycopg.pq import TransactionStatus

from app.core.exceptions import StorageError
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext

_NAME_MAX = 120


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


def _revision(value: int) -> None:
    if type(value) is not int or not 1 <= value < 2**63 - 1:
        raise ValueError("A positive workspace revision is required")


def _roles(session: Mapping[str, Any]) -> tuple[str, ...]:
    roles = session.get("assigned_roles") or session.get("roles") or []
    if not isinstance(roles, (list, tuple)) or not 1 <= len(roles) <= 1024:
        raise ValueError("Authenticated workspace roles are required")
    if any(
        not isinstance(role, str) or not role or role != role.strip()
        or len(role.encode("utf-8")) > 128 or "\x00" in role
        for role in roles
    ):
        raise ValueError("Invalid workspace role")
    return tuple(sorted(set(roles)))


class WorkspaceMetadataScope:
    """Actor-bound scope shared by the Workspace metadata repositories."""

    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
        roles: tuple[str, ...],
    ) -> None:
        self.connection = connection
        self.actor = actor
        self._roles = json.dumps(roles)
        self._active = False

    async def _transaction(self) -> None:
        if not self._active or self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Workspace storage requires its actor-bound transaction scope")
        await self._assert_actor(self.connection, self.actor, self._roles)

    @staticmethod
    async def _assert_actor(connection, actor: SecurityContext, roles: str) -> None:
        cursor = await connection.execute(
            "SELECT current_setting('nova.principal',true)=%s "
            "AND current_setting('nova.active_role',true)=%s "
            "AND current_setting('nova.security_context_version',true)=%s "
            "AND current_setting('nova.principal_type',true)=%s "
            "AND current_setting('nova.session_id',true)=%s "
            "AND current_setting('nova.service_identity',true)=current_user "
            "AND coalesce(current_setting('nova.workspace_roles',true),'')=%s AS valid",
            (actor.principal, actor.active_role, str(actor.security_context_version),
             actor.principal_type, actor.session_id or "", roles),
        )
        row = await cursor.fetchone()
        if not row or row["valid"] is not True:
            raise MetadataPermissionDenied()

    @classmethod
    @asynccontextmanager
    async def bind(
        cls, connection: psycopg.AsyncConnection[dict[str, Any]], session: Mapping[str, Any],
    ) -> AsyncIterator[Self]:
        """Bind server-validated session state, never a request's role selection."""
        if connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Workspace storage requires an active metadata transaction")
        roles = _roles(session)
        actor = SecurityContext.from_session(dict(session))
        await cls._assert_actor(connection, actor, "")
        repository = cls(connection, actor, roles)
        async with connection.transaction():
            await connection.execute(
                "SELECT set_config('nova.workspace_roles',%s,true)", (repository._roles,),
            )
            repository._active = True
            try:
                yield repository
                await connection.execute("SELECT set_config('nova.workspace_roles','',true)")
            finally:
                repository._active = False


class WorkspaceSharingRepository(WorkspaceMetadataScope):
    async def visible(self) -> list[dict[str, Any]]:
        await self._transaction()
        cursor = await self.connection.execute(
            "SELECT *,nova_meta.workspace_permission(id) AS permission "
            "FROM nova_meta.config_workspaces WHERE is_deleted=false ORDER BY name,id"
        )
        return list(await cursor.fetchall())

    async def get(self, identity: str) -> dict[str, Any] | None:
        await self._transaction()
        cursor = await self.connection.execute(
            "SELECT *,nova_meta.workspace_permission(id) AS permission "
            "FROM nova_meta.config_workspaces WHERE id=%s AND is_deleted=false", (identity,),
        )
        return await cursor.fetchone()

    async def shares(self, identity: str) -> list[dict[str, Any]]:
        await self._transaction()
        cursor = await self.connection.execute(
            "SELECT role_name AS role,permission,granted_by,granted_at,metadata_revision "
            "FROM nova_meta.config_workspace_shares "
            "WHERE workspace_id=%s AND NOT is_deleted ORDER BY role_name", (identity,),
        )
        return list(await cursor.fetchall())

    async def create(self, name: str) -> dict[str, Any]:
        await self._transaction()
        cursor = await self.connection.execute(
            "INSERT INTO nova_meta.config_workspaces (id,name,owner) VALUES (%s,%s,%s) RETURNING *",
            (uuid4().hex, _clean_name(name), self.actor.principal),
        )
        row = await cursor.fetchone()
        assert row is not None
        return row

    async def _owned_revision(self, identity: str, expected_revision: int) -> None:
        _revision(expected_revision)
        row = await self.get(identity)
        if row is None or row["metadata_revision"] != expected_revision:
            raise MetadataConflict()
        if row["owner"] != self.actor.principal:
            raise MetadataPermissionDenied()

    async def rename(
        self, identity: str, name: str, *, expected_revision: int,
    ) -> dict[str, Any]:
        await self._transaction()
        name = _clean_name(name)
        await self._owned_revision(identity, expected_revision)
        cursor = await self.connection.execute(
            "UPDATE nova_meta.config_workspaces SET name=%s,metadata_revision=metadata_revision+1 "
            "WHERE id=%s AND metadata_revision=%s AND is_deleted=false RETURNING *",
            (name, identity, expected_revision),
        )
        row = await cursor.fetchone()
        if row is None:
            raise MetadataConflict()
        return row

    async def replace_shares(
        self, identity: str, shares: Mapping[str, str], *, expected_revision: int,
    ) -> dict[str, Any]:
        """Serialize the whole set against the workspace revision, retaining tombstones.

        The owning service validates role existence before opening this transaction.
        A caught failure rolls back the complete replacement, including its audit.
        """
        await self._transaction()
        changes = dict(shares)
        if len(changes) > 1024 or any(
            not isinstance(role, str) or not role or role != role.strip()
            or len(role.encode("utf-8")) > 128 or "\x00" in role
            or not isinstance(permission, str) or permission not in {"view", "edit"}
            for role, permission in changes.items()
        ):
            raise ValueError("Invalid workspace shares")
        await self._owned_revision(identity, expected_revision)
        async with self.connection.transaction():
            cursor = await self.connection.execute(
                "UPDATE nova_meta.config_workspaces SET metadata_revision=metadata_revision+1 "
                "WHERE id=%s AND metadata_revision=%s AND is_deleted=false RETURNING *",
                (identity, expected_revision),
            )
            row = await cursor.fetchone()
            if row is None:
                raise MetadataConflict()
            await self.connection.execute(
                "UPDATE nova_meta.config_workspace_shares SET is_deleted=true,"
                "metadata_revision=metadata_revision+1 WHERE workspace_id=%s "
                "AND NOT is_deleted AND NOT (role_name=ANY(%s))", (identity, list(changes)),
            )
            for role, permission in sorted(changes.items()):
                await self.connection.execute(
                    "INSERT INTO nova_meta.config_workspace_shares "
                    "(workspace_id,role_name,permission) VALUES (%s,%s,%s) "
                    "ON CONFLICT (workspace_id,role_name) DO UPDATE SET "
                    "permission=EXCLUDED.permission,is_deleted=false,"
                    "metadata_revision=config_workspace_shares.metadata_revision+1",
                    (identity, role, permission),
                )
            return row

    async def delete(self, identity: str, *, expected_revision: int) -> None:
        """Tombstone a workspace after its owning file-deletion workflow has drained."""
        await self._transaction()
        await self._owned_revision(identity, expected_revision)
        async with self.connection.transaction():
            cursor = await self.connection.execute(
                "SELECT id FROM nova_meta.config_workspaces "
                "WHERE id=%s AND metadata_revision=%s AND is_deleted=false FOR UPDATE",
                (identity, expected_revision),
            )
            if await cursor.fetchone() is None:
                raise MetadataConflict()
            await self.connection.execute(
                "UPDATE nova_meta.config_workspace_shares SET is_deleted=true,"
                "metadata_revision=metadata_revision+1 WHERE workspace_id=%s AND NOT is_deleted",
                (identity,),
            )
            await self.connection.execute(
                "UPDATE nova_meta.config_workspaces SET is_deleted=true,"
                "metadata_revision=metadata_revision+1 WHERE id=%s", (identity,),
            )
