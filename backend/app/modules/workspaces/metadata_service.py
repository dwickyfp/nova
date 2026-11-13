"""Workspace file commands with metadata in PostgreSQL.

``WorkspaceService`` delegates every file command here: the same method names
and arguments, with metadata in ``nova_meta`` and payloads in the existing
Workspace object storage. There is no ``NOVA_SYSTEM`` path or fallback.

Every save writes a new object under a fresh key, then publishes it with a
revision check in one metadata transaction. The previous live object becomes
the version snapshot in that same transaction, so a concurrent or lost writer
can never overwrite an object that metadata references. Objects written by a
losing writer are removed; deletions drain objects only after commit.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol
from uuid import uuid4

from app.core.exceptions import StorageError
from app.core.metadata_db import (
    MetadataConflict,
    MetadataDatabase,
    MetadataOutcomeUnknown,
    MetadataPermissionDenied,
)
from app.core.metadata_scope import actor_for, metadata_transaction
from app.modules.access_control.security_context import SecurityContext
from app.modules.workspaces.actor import acting_session
from app.modules.workspaces.metadata_files import WorkspaceFileRepository, build_path

logger = logging.getLogger(__name__)

Transaction = Callable[..., AbstractAsyncContextManager[WorkspaceFileRepository]]
#: Records a governed file action inside the command's metadata transaction:
#: ``(action, object_type, object_name)``.
Audit = Callable[[str, str, str], Awaitable[None]]

_CHANGED = "This file changed since you opened it. Reload it and try again."
_EXISTS = "A file or folder with this name already exists here"


class ObjectStore(Protocol):
    def key(self, namespace: str, *parts: str) -> str: ...
    async def put(self, key: str, body: bytes) -> str: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


class WorkspaceObjectStore:
    """Object effects through the existing Workspace storage owner."""

    def __init__(self, service: Any) -> None:
        self.service = service

    def key(self, namespace: str, *parts: str) -> str:
        return str(self.service.internal_object_key(namespace, *parts))

    async def put(self, key: str, body: bytes) -> str:
        result = await asyncio.to_thread(self.service._put_object, key, body)
        return str(result.get("ETag", "")).strip('"')

    async def get(self, key: str) -> bytes:
        return bytes(await asyncio.to_thread(self.service._read_object, key))

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.service._delete_object, key)


def bound_transaction(
    database: MetadataDatabase, session: Mapping[str, Any],
) -> Transaction:
    """Open one actor-bound metadata transaction per command."""

    @asynccontextmanager
    async def transaction(*, read_only: bool = False):
        actor = SecurityContext.from_session(dict(session))
        async with (
            database.transaction(actor, read_only=read_only) as connection,
            WorkspaceFileRepository.bind(connection, session) as repository,
        ):
            yield repository

    return transaction


@asynccontextmanager
async def acting_transaction(*, read_only: bool = False):
    """The file repository for the acting session, in this task's metadata transaction."""
    session = acting_session()
    async with (
        metadata_transaction(actor_for(session), read_only=read_only) as scope,
        WorkspaceFileRepository.bind(scope.connection, session) as repository,
    ):
        yield repository


def _not_found() -> StorageError:
    return StorageError("Workspace entry not found", status_code=404)


def _translate(error: Exception) -> StorageError:
    if isinstance(error, MetadataPermissionDenied):
        return StorageError("You can only view this workspace", status_code=403)
    if isinstance(error, MetadataConflict):
        return StorageError(_CHANGED, status_code=409)
    return StorageError(str(error) or "Invalid workspace request", status_code=400)


class WorkspaceMetadataService:
    def __init__(
        self, transaction: Transaction, objects: ObjectStore, audit: Audit | None = None,
    ) -> None:
        self._transaction = transaction
        self._objects = objects
        self._audit = audit

    async def _record(self, action: str, object_type: str, object_name: str) -> None:
        if self._audit is not None:
            await self._audit(action, object_type, object_name)

    async def _discard(self, keys: list[str]) -> int:
        """Best-effort removal of unreferenced objects; returns how many failed."""
        failed = 0
        for key in keys:
            try:
                await self._objects.delete(key)
            except StorageError:
                failed += 1
        if failed:
            logger.warning("Workspace object cleanup left %d unreferenced objects", failed)
        return failed

    async def _write(self, namespace: str, entry_id: str, body: bytes) -> tuple[str, str]:
        key = self._objects.key(namespace, ".objects", entry_id, uuid4().hex)
        return key, await self._objects.put(key, body)

    async def _entry(self, namespace: str, entry_id: str) -> dict[str, Any]:
        async with self._transaction(read_only=True) as files:
            entry = await files.get_entry(namespace, entry_id)
        if entry is None:
            raise _not_found()
        return entry

    async def get_entry(self, namespace: str, entry_id: str) -> dict[str, Any] | None:
        async with self._transaction(read_only=True) as files:
            return await files.get_entry(namespace, entry_id)

    async def list_entries(self, namespace: str) -> list[dict[str, Any]]:
        async with self._transaction(read_only=True) as files:
            return await files.list_entries(namespace)

    async def namespace_of(self, entry_id: str) -> str | None:
        async with self._transaction(read_only=True) as files:
            return await files.namespace_of(entry_id)

    async def get_file(self, namespace: str, entry_id: str) -> tuple[dict[str, Any], str]:
        entry, body = await self.read_entry_bytes(namespace, entry_id)
        return entry, body.decode("utf-8")

    async def read_entry_bytes(self, namespace: str, entry_id: str) -> tuple[dict[str, Any], bytes]:
        entry = await self._entry(namespace, entry_id)
        if entry["entry_type"] != "file":
            raise StorageError("Only files can be opened", status_code=400)
        return entry, await self._objects.get(entry["object_key"])

    async def create_folder(self, namespace: str, parent_path: str, name: str) -> dict[str, Any]:
        try:
            async with self._transaction() as files:
                entry = await files.insert_entry(
                    namespace, parent_path=parent_path, name=name, entry_type="folder",
                    object_key=None,
                )
                await self._record("create_folder", "workspace_folder", entry["path"])
                return entry
        except MetadataConflict:
            raise StorageError(_EXISTS, status_code=409) from None
        except (MetadataPermissionDenied, ValueError) as error:
            raise _translate(error) from None

    async def create_file(
        self, namespace: str, parent_path: str, name: str, content: str, *,
        on_created: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Create a text file; ``on_created`` runs in the same metadata transaction."""
        return await self.create_file_bytes(
            namespace, parent_path, name, content.encode("utf-8"), action="create_file",
            on_created=on_created,
        )

    async def create_file_bytes(
        self, namespace: str, parent_path: str, name: str, data: bytes, *, replace: bool = False,
        action: str = "upload_file",
        on_created: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        if replace:
            path = build_path(parent_path, name)
            for entry in await self.list_entries(namespace):
                if entry["path"] == path:
                    if entry["entry_type"] != "file":
                        raise StorageError(_EXISTS, status_code=409)
                    return await self._save(namespace, entry, data, snapshot=False)
        key, etag = await self._write(namespace, uuid4().hex, data)
        try:
            async with self._transaction() as files:
                entry = await files.insert_entry(
                    namespace, parent_path=parent_path, name=name, entry_type="file",
                    object_key=key, size_bytes=len(data), etag=etag,
                )
                await self._record(action, "workspace_file", entry["path"])
                if on_created is not None:
                    await on_created(entry)
                return entry
        except MetadataOutcomeUnknown:
            raise  # The object may now be referenced; recovery must decide.
        except MetadataConflict:
            await self._discard([key])
            raise StorageError(_EXISTS, status_code=409) from None
        except (MetadataPermissionDenied, ValueError) as error:
            await self._discard([key])
            raise _translate(error) from None
        except BaseException:
            await self._discard([key])
            raise

    async def update_file(
        self, namespace: str, entry_id: str, content: str, *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        entry = await self._entry(namespace, entry_id)
        if entry["entry_type"] != "file":
            raise StorageError("Only files have content", status_code=400)
        if expected_revision is not None and entry["metadata_revision"] != expected_revision:
            raise StorageError(_CHANGED, status_code=409)
        return await self._save(namespace, entry, content.encode("utf-8"), snapshot=True)

    async def _save(
        self, namespace: str, entry: dict[str, Any], data: bytes, *, snapshot: bool,
    ) -> dict[str, Any]:
        old_key = entry["object_key"]
        try:
            current = await self._objects.get(old_key)
        except StorageError as error:
            if error.status_code != 404:
                raise
            current = None
        if current == data:
            return entry
        key, etag = await self._write(namespace, entry["id"], data)
        keep_old = snapshot and bool(current)
        try:
            async with self._transaction() as files:
                saved = await files.update_file(
                    namespace, entry["id"], expected_revision=entry["metadata_revision"],
                    object_key=key, size_bytes=len(data), etag=etag,
                )
                if keep_old:
                    assert current is not None
                    digest = hashlib.sha256(current).hexdigest()
                    version = await files.reserve_version(
                        namespace, entry["id"], object_key_for=lambda _number: old_key,
                    )
                    # A source row without an ETag records its content digest instead.
                    await files.publish_version(
                        namespace, version["id"], size_bytes=len(current),
                        etag=entry["etag"] or f"sha256:{digest}", content_sha256=digest,
                    )
        except MetadataOutcomeUnknown:
            raise
        except BaseException as error:
            await self._discard([key])
            if isinstance(error, (MetadataConflict, MetadataPermissionDenied, ValueError)):
                raise _translate(error) from None
            raise
        if not keep_old and current is not None:
            await self._discard([old_key])
        return saved

    async def rename_entry(
        self, namespace: str, entry_id: str, new_name: str, new_parent_path: str | None = None,
    ) -> dict[str, Any]:
        entry = await self._entry(namespace, entry_id)
        try:
            async with self._transaction() as files:
                moved, _ = await files.move(
                    namespace, entry_id, expected_revision=entry["metadata_revision"],
                    new_parent_path=(
                        entry["parent_path"] if new_parent_path is None else new_parent_path
                    ),
                    new_name=new_name,
                )
                await self._record(
                    "rename", f"workspace_{entry['entry_type']}",
                    f"{entry['path']} -> {moved['path']}",
                )
                return moved
        except MetadataConflict:
            raise StorageError(_EXISTS + " or the item changed", status_code=409) from None
        except (MetadataPermissionDenied, ValueError) as error:
            raise _translate(error) from None

    async def delete_entry(self, namespace: str, entry_id: str) -> None:
        entry = await self._entry(namespace, entry_id)
        try:
            async with self._transaction() as files:
                keys = await files.delete(
                    namespace, entry_id, expected_revision=entry["metadata_revision"],
                )
                await self._record("delete", f"workspace_{entry['entry_type']}", entry["path"])
        except (MetadataConflict, MetadataPermissionDenied, ValueError) as error:
            raise _translate(error) from None
        await self._discard(keys)

    async def list_file_versions(
        self, namespace: str, entry_id: str, limit: int = 100,
    ) -> list[dict[str, Any]]:
        entry = await self._entry(namespace, entry_id)
        if entry["entry_type"] != "file":
            raise StorageError("Only files have versions", status_code=400)
        async with self._transaction(read_only=True) as files:
            return await files.list_versions(namespace, entry_id, limit=limit)

    async def get_file_version_record(
        self, namespace: str, entry_id: str, version: int,
    ) -> tuple[dict[str, Any], str]:
        try:
            async with self._transaction(read_only=True) as files:
                record = await files.get_version(namespace, entry_id, version)
        except MetadataConflict:
            raise StorageError(
                "This version has conflicting history and cannot be opened", status_code=409,
            ) from None
        if record is None:
            raise StorageError("Version not found", status_code=404)
        body = await self._objects.get(record["object_key"])
        return record, body.decode("utf-8")

    async def get_file_version(self, namespace: str, entry_id: str, version: int) -> str:
        return (await self.get_file_version_record(namespace, entry_id, version))[1]
