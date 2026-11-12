"""Workspace file entries and version receipts in the caller's PostgreSQL transaction.

A namespace is a personal principal name or ``~ws/<id>``. The database grants
reads to any permission on that namespace and writes to ``owner``/``edit``.
Object payloads stay with the existing storage owner: this repository reserves
version numbers before an object is written and publishes its receipt after,
so a lost writer leaves a visible reservation rather than a silent overwrite.
Runtime callers switch at the all-domain cutover; there is no StarRocks fallback.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.workspaces.metadata_sharing import WorkspaceMetadataScope

_LIVE = "is_deleted IS NOT TRUE"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ENTRY_TYPES = frozenset({"file", "folder"})


def build_path(parent_path: str, name: str) -> str:
    return "/".join(part for part in (parent_path.strip("/"), name.strip("/")) if part)


def _revision(value: int) -> None:
    if type(value) is not int or not 1 <= value < 2**63 - 1:
        raise ValueError("A positive entry revision is required")


def _name(name: str) -> str:
    if (
        not isinstance(name, str) or not name.strip() or name in {".", ".."}
        or "/" in name or "\x00" in name or len(name.encode("utf-8")) > 256
    ):
        raise ValueError("Invalid workspace entry name")
    return name


def _parent(parent_path: str) -> str:
    if not isinstance(parent_path, str) or "\x00" in parent_path:
        raise ValueError("Invalid workspace folder")
    clean = parent_path.strip("/")
    if "//" in clean or len(clean.encode("utf-8")) > 1024 or any(
        part in {".", ".."} for part in clean.split("/")
    ):
        raise ValueError("Invalid workspace folder")
    return clean


def _with_path(row: dict[str, Any]) -> dict[str, Any]:
    row["path"] = build_path(row["parent_path"], row["name"])
    row["size_bytes"] = row["size_bytes"] or 0
    return row


class WorkspaceFileRepository(WorkspaceMetadataScope):
    async def list_entries(self, namespace: str) -> list[dict[str, Any]]:
        await self._transaction()
        cursor = await self.connection.execute(
            f"SELECT * FROM nova_meta.config_workspace_entries WHERE user_name=%s AND {_LIVE} "
            "ORDER BY parent_path,entry_type,name", (namespace,),
        )
        return [_with_path(row) for row in await cursor.fetchall()]

    async def get_entry(self, namespace: str, entry_id: str) -> dict[str, Any] | None:
        await self._transaction()
        cursor = await self.connection.execute(
            f"SELECT * FROM nova_meta.config_workspace_entries WHERE id=%s AND user_name=%s "
            f"AND {_LIVE}", (entry_id, namespace),
        )
        row = await cursor.fetchone()
        return _with_path(row) if row else None

    async def namespace_of(self, entry_id: str) -> str | None:
        """The namespace of a live entry this actor may read; ``None`` otherwise."""
        await self._transaction()
        cursor = await self.connection.execute(
            f"SELECT user_name FROM nova_meta.config_workspace_entries WHERE id=%s AND {_LIVE}",
            (entry_id,),
        )
        row = await cursor.fetchone()
        return str(row["user_name"]) if row else None

    async def insert_entry(
        self, namespace: str, *, parent_path: str, name: str, entry_type: str,
        object_key: str | None, size_bytes: int = 0, etag: str | None = None,
    ) -> dict[str, Any]:
        await self._transaction()
        if entry_type not in _ENTRY_TYPES:
            raise ValueError("Invalid workspace entry type")
        cursor = await self.connection.execute(
            "INSERT INTO nova_meta.config_workspace_entries "
            "(id,user_name,parent_path,name,entry_type,object_key,size_bytes,etag) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (str(uuid4()), namespace, _parent(parent_path), _name(name), entry_type,
             object_key, size_bytes, etag),
        )
        row = await cursor.fetchone()
        assert row is not None
        return _with_path(row)

    async def _locked(
        self, namespace: str, entry_id: str, expected_revision: int,
    ) -> dict[str, Any]:
        _revision(expected_revision)
        cursor = await self.connection.execute(
            f"SELECT * FROM nova_meta.config_workspace_entries WHERE id=%s AND user_name=%s "
            f"AND {_LIVE} FOR UPDATE", (entry_id, namespace),
        )
        row = await cursor.fetchone()
        if row is None:
            await self._refuse_readable(namespace, entry_id)
        if row is None or row["metadata_revision"] != expected_revision:
            raise MetadataConflict()
        return _with_path(row)

    async def _refuse_readable(self, namespace: str, entry_id: str) -> None:
        """A row readable but not lockable for update is a view-only share."""
        cursor = await self.connection.execute(
            f"SELECT 1 FROM nova_meta.config_workspace_entries WHERE id=%s AND user_name=%s "
            f"AND {_LIVE}", (entry_id, namespace),
        )
        if await cursor.fetchone() is not None:
            raise MetadataPermissionDenied()

    async def update_file(
        self, namespace: str, entry_id: str, *, expected_revision: int,
        object_key: str, size_bytes: int, etag: str | None,
    ) -> dict[str, Any]:
        """Publish a file's new live object receipt against the revision it was read at."""
        await self._transaction()
        async with self.connection.transaction():
            entry = await self._locked(namespace, entry_id, expected_revision)
            if entry["entry_type"] != "file":
                raise ValueError("Only files have content")
            cursor = await self.connection.execute(
                "UPDATE nova_meta.config_workspace_entries SET object_key=%s,size_bytes=%s,"
                "etag=%s,metadata_revision=metadata_revision+1 WHERE id=%s RETURNING *",
                (object_key, size_bytes, etag, entry_id),
            )
            row = await cursor.fetchone()
            assert row is not None
            return _with_path(row)

    async def _subtree(self, namespace: str, path: str) -> list[dict[str, Any]]:
        # Prefix comparison avoids LIKE wildcards in user-chosen names.
        cursor = await self.connection.execute(
            f"SELECT * FROM nova_meta.config_workspace_entries WHERE user_name=%s AND {_LIVE} "
            "AND (parent_path=%s OR left(parent_path,char_length(%s)+1)=%s||'/') "
            "ORDER BY id FOR UPDATE", (namespace, path, path, path),
        )
        return [_with_path(row) for row in await cursor.fetchall()]

    async def move(
        self, namespace: str, entry_id: str, *, expected_revision: int,
        new_parent_path: str, new_name: str, object_key_for: Callable[[str], str],
    ) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        """Rename or move an entry and every descendant in one transaction.

        Returns the moved entry and the ``(old, new)`` object keys the caller
        must reconcile in object storage. Path collisions raise a conflict.
        """
        await self._transaction()
        parent, name = _parent(new_parent_path), _name(new_name)
        async with self.connection.transaction():
            entry = await self._locked(namespace, entry_id, expected_revision)
            old_path, new_path = entry["path"], build_path(parent, name)
            if entry["entry_type"] == "folder" and new_path.startswith(old_path + "/"):
                raise ValueError("A folder cannot move into itself")
            moved = entry
            moves: list[tuple[str, str]] = []
            children = (
                await self._subtree(namespace, old_path) if entry["entry_type"] == "folder" else []
            )
            for item in [entry, *children]:
                if item is entry:
                    target_parent, target_name, target_path = parent, name, new_path
                else:
                    target_path = new_path + item["path"][len(old_path):]
                    target_parent, _, target_name = target_path.rpartition("/")
                key = None
                if item["entry_type"] == "file":
                    key = object_key_for(target_path)
                    if item["object_key"] != key:
                        moves.append((item["object_key"], key))
                cursor = await self.connection.execute(
                    "UPDATE nova_meta.config_workspace_entries SET parent_path=%s,name=%s,"
                    "object_key=%s,metadata_revision=metadata_revision+1 WHERE id=%s "
                    "RETURNING *", (target_parent, target_name, key, item["id"]),
                )
                row = await cursor.fetchone()
                assert row is not None
                if item is entry:
                    moved = _with_path(row)
            return moved, moves

    async def delete(
        self, namespace: str, entry_id: str, *, expected_revision: int,
    ) -> list[str]:
        """Tombstone an entry, its descendants and their versions together.

        Returns every retired object key so the caller can drain object storage
        after commit; metadata never claims cross-store atomicity.
        """
        await self._transaction()
        async with self.connection.transaction():
            entry = await self._locked(namespace, entry_id, expected_revision)
            items = [entry]
            if entry["entry_type"] == "folder":
                items += await self._subtree(namespace, entry["path"])
            ids = [item["id"] for item in items]
            cursor = await self.connection.execute(
                "UPDATE nova_meta.config_workspace_file_versions SET is_deleted=true,"
                "metadata_revision=metadata_revision+1 WHERE entry_id=ANY(%s) AND user_name=%s "
                "AND NOT is_deleted RETURNING object_key", (ids, namespace),
            )
            keys = [row["object_key"] for row in await cursor.fetchall()]
            await self.connection.execute(
                "UPDATE nova_meta.config_workspace_entries SET is_deleted=true,"
                "metadata_revision=metadata_revision+1 WHERE id=ANY(%s)", (ids,),
            )
            keys += [item["object_key"] for item in items if item["object_key"]]
            return sorted(set(keys))

    async def reserve_version(
        self, namespace: str, entry_id: str, *, object_key_for: Callable[[int], str],
    ) -> dict[str, Any]:
        """Reserve the next number above every retained version, serialized per file."""
        await self._transaction()
        async with self.connection.transaction():
            cursor = await self.connection.execute(
                f"SELECT entry_type FROM nova_meta.config_workspace_entries WHERE id=%s "
                f"AND user_name=%s AND {_LIVE} FOR UPDATE", (entry_id, namespace),
            )
            entry = await cursor.fetchone()
            if entry is None:
                await self._refuse_readable(namespace, entry_id)
                raise MetadataConflict()
            if entry["entry_type"] != "file":
                raise ValueError("Only files have versions")
            cursor = await self.connection.execute(
                "SELECT coalesce(max(version),0)+1 AS next FROM "
                "nova_meta.config_workspace_file_versions WHERE entry_id=%s", (entry_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            version = int(row["next"])
            cursor = await self.connection.execute(
                "INSERT INTO nova_meta.config_workspace_file_versions "
                "(id,entry_id,user_name,version,object_key) VALUES (%s,%s,%s,%s,%s) RETURNING *",
                (str(uuid4()), entry_id, namespace, version, object_key_for(version)),
            )
            reserved = await cursor.fetchone()
            assert reserved is not None
            return reserved

    async def publish_version(
        self, namespace: str, version_id: str, *, size_bytes: int, etag: str,
        content_sha256: str,
    ) -> dict[str, Any]:
        """Record the object receipt for a reservation. A repeat publish conflicts."""
        await self._transaction()
        if type(size_bytes) is not int or size_bytes < 0 or not etag or not _SHA256.fullmatch(
            content_sha256 or ""
        ):
            raise ValueError("A complete object receipt is required")
        return await self._settle(
            namespace, version_id,
            "state='published',size_bytes=%s,etag=%s,content_sha256=%s",
            (size_bytes, etag, content_sha256),
        )

    async def abandon_version(self, namespace: str, version_id: str) -> dict[str, Any]:
        """Close a reservation whose object write did not happen."""
        await self._transaction()
        return await self._settle(namespace, version_id, "state='abandoned'", ())

    async def _settle(
        self, namespace: str, version_id: str, assignment: str, params: tuple[Any, ...],
    ) -> dict[str, Any]:
        cursor = await self.connection.execute(
            f"UPDATE nova_meta.config_workspace_file_versions SET {assignment},"
            "metadata_revision=metadata_revision+1 WHERE id=%s AND user_name=%s "
            "AND state='reserved' AND NOT is_deleted RETURNING *",
            (*params, version_id, namespace),
        )
        row = await cursor.fetchone()
        if row is None:
            raise MetadataConflict()
        return row

    async def reserved_versions(self, namespace: str) -> list[dict[str, Any]]:
        """Reservations without a receipt, for recovery to verify against storage."""
        await self._transaction()
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.config_workspace_file_versions WHERE user_name=%s "
            "AND state='reserved' AND NOT is_deleted ORDER BY created_at,id", (namespace,),
        )
        return list(await cursor.fetchall())

    async def list_versions(
        self, namespace: str, entry_id: str, *, limit: int = 100, offset: int = 0,
    ) -> list[dict[str, Any]]:
        await self._transaction()
        if (
            type(limit) is not int or type(offset) is not int
            or not 1 <= limit <= 1000 or offset < 0
        ):
            raise ValueError("Invalid version page")
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.config_workspace_file_versions WHERE entry_id=%s "
            "AND user_name=%s AND state='published' AND NOT is_deleted "
            "ORDER BY version DESC,id LIMIT %s OFFSET %s", (entry_id, namespace, limit, offset),
        )
        return list(await cursor.fetchall())

    async def get_version(
        self, namespace: str, entry_id: str, version: int,
    ) -> dict[str, Any] | None:
        """One published version. Unreconciled imported duplicates are a conflict."""
        await self._transaction()
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.config_workspace_file_versions WHERE entry_id=%s "
            "AND user_name=%s AND version=%s AND state='published' AND NOT is_deleted "
            "ORDER BY id LIMIT 2", (entry_id, namespace, version),
        )
        rows = await cursor.fetchall()
        if len(rows) > 1:
            raise MetadataConflict()
        return rows[0] if rows else None
