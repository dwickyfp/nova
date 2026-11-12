"""Workspace files: metadata in PostgreSQL, payloads in Workspace object storage."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.common.audit import write_audit_log
from app.common.preferences import PreferenceChange, PreferenceRepository
from app.core.config import get_storage_connection, load_nova_app_config
from app.core.exceptions import StorageError
from app.core.metadata_scope import actor_for, current_scope, metadata_transaction
from app.modules.query.dialect.injector import resolve_storage_credentials
from app.modules.workspaces.actor import acting_session
from app.modules.workspaces.metadata_service import (
    WorkspaceMetadataService,
    WorkspaceObjectStore,
    acting_transaction,
)
from app.modules.workspaces.storage_errors import classify_storage_error


class WorkspaceService:
    PREF_OPEN_TABS = "workspace.open_tabs"
    PREF_ACTIVE_TAB = "workspace.active_tab"
    PREF_SIDEBAR_STATE = "workspace.sidebar_state"
    #: Phase 10 assistant panel visibility. A non-sensitive layout preference,
    #: stored like ``PREF_SIDEBAR_STATE`` — not conversation state, so E5a's
    #: in-memory rule for threads/grants does not apply.
    PREF_ASSISTANT_STATE = "workspace.assistant_state"
    PREF_LAST_DATABASE = "workspace.last_database"
    PREF_LAST_SCHEMA = "workspace.last_schema"
    PREF_LAST_ROLE = "workspace.last_role"

    def _client(self):
        config = load_nova_app_config()
        conn = get_storage_connection(config.workspace.storage_connection)
        # Credentials come from nova.yaml / env — never hardcoded.
        access_key, secret_key = resolve_storage_credentials(config.workspace.storage_connection)
        return boto3.client(
            "s3",
            endpoint_url=conn.endpoint or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            config=BotoConfig(signature_version="s3v4"),
            region_name=conn.region or "us-east-1",
        )

    def _bucket(self) -> str:
        config = load_nova_app_config()
        return get_storage_connection(config.workspace.storage_connection).bucket

    @property
    def _files(self) -> WorkspaceMetadataService:
        return WorkspaceMetadataService(
            acting_transaction, WorkspaceObjectStore(self), audit=_audit_file_action,
        )

    _TREE_PREFERENCES = (
        PREF_OPEN_TABS,
        PREF_ACTIVE_TAB,
        PREF_SIDEBAR_STATE,
        PREF_ASSISTANT_STATE,
        PREF_LAST_DATABASE,
        PREF_LAST_SCHEMA,
        PREF_LAST_ROLE,
    )

    async def get_tree(self, username: str) -> dict:
        entries = await self._files.list_entries(username)
        async with metadata_transaction(actor_for(acting_session()), read_only=True) as scope:
            records = await PreferenceRepository(scope.connection).read(
                username, self._TREE_PREFERENCES
            )
        prefs = {
            key: record.value
            for key, record in records.items()
            if not record.deleted and record.value is not None
        }
        return {
            "root_name": "My Workspace",
            "entries": entries,
            "open_tabs": json.loads(prefs.get(self.PREF_OPEN_TABS, "[]")),
            "active_tab": prefs.get(self.PREF_ACTIVE_TAB),
            "sidebar_collapsed": prefs.get(self.PREF_SIDEBAR_STATE, "false") == "true",
            "assistant_collapsed": prefs.get(self.PREF_ASSISTANT_STATE, "false") == "true",
            "defaults": {
                "database": prefs.get(self.PREF_LAST_DATABASE),
                "schema": prefs.get(self.PREF_LAST_SCHEMA),
                "role": prefs.get(self.PREF_LAST_ROLE),
            },
        }

    async def create_file(
        self, username: str, parent_path: str, name: str, content: str, *,
        on_created: Callable[[dict], Awaitable[None]] | None = None,
    ) -> dict:
        """Create a text file; ``on_created`` runs in the same metadata transaction."""
        return await self._files.create_file(
            username, parent_path, name, content, on_created=on_created
        )

    async def create_file_bytes(
        self, username: str, parent_path: str, name: str, data: bytes, *, replace: bool = False
    ) -> dict:
        """Store an uploaded or generated file (CSV, Parquet, images, …).

        Unlike :meth:`create_file` the content is bytes and is never opened as
        text. ``replace`` overwrites an existing file at the same path (used to
        copy a runtime's output files back), keeping its entry id.
        """
        return await self._files.create_file_bytes(
            username, parent_path, name, data, replace=replace
        )

    async def create_folder(self, username: str, parent_path: str, name: str) -> dict:
        return await self._files.create_folder(username, parent_path, name)

    async def get_file(self, username: str, entry_id: str) -> tuple[dict, str]:
        return await self._files.get_file(username, entry_id)

    async def update_file(
        self, username: str, entry_id: str, content: str, *,
        expected_revision: int | None = None,
    ) -> dict:
        """Save new content; the previous content becomes the next version."""
        return await self._files.update_file(
            username, entry_id, content, expected_revision=expected_revision
        )

    async def list_file_versions(
        self, username: str, entry_id: str, limit: int = 100
    ) -> list[dict]:
        return await self._files.list_file_versions(username, entry_id, limit)

    async def get_file_version(self, username: str, entry_id: str, version: int) -> str:
        return await self._files.get_file_version(username, entry_id, version)

    async def get_file_version_record(
        self, username: str, entry_id: str, version: int
    ) -> tuple[dict, str]:
        """Return one version's metadata and its content in a single lookup."""
        return await self._files.get_file_version_record(username, entry_id, version)

    async def rename_entry(
        self,
        username: str,
        entry_id: str,
        new_name: str,
        new_parent_path: str | None = None,
    ) -> dict:
        return await self._files.rename_entry(username, entry_id, new_name, new_parent_path)

    async def delete_entry(self, username: str, entry_id: str) -> None:
        await self._files.delete_entry(username, entry_id)

    async def save_state(
        self,
        username: str,
        *,
        open_tabs: list[str],
        active_tab: str | None,
        sidebar_collapsed: bool,
        assistant_collapsed: bool,
        last_database: str | None,
        last_schema: str | None,
        last_role: str | None,
    ) -> None:
        values = {
            self.PREF_OPEN_TABS: json.dumps(open_tabs),
            self.PREF_ACTIVE_TAB: active_tab or "",
            self.PREF_SIDEBAR_STATE: "true" if sidebar_collapsed else "false",
            self.PREF_ASSISTANT_STATE: "true" if assistant_collapsed else "false",
        }
        for key, value in (
            (self.PREF_LAST_DATABASE, last_database),
            (self.PREF_LAST_SCHEMA, last_schema),
            (self.PREF_LAST_ROLE, last_role),
        ):
            if value is not None:
                values[key] = value
        async with metadata_transaction(actor_for(acting_session())) as scope:
            preferences = PreferenceRepository(scope.connection)
            current = await preferences.read(username, list(values))
            changes = {
                key: PreferenceChange(
                    current[key].revision if key in current else 0, value
                )
                for key, value in values.items()
                if key not in current or current[key].deleted or current[key].value != value
            }
            if changes:
                # A concurrent save of the same keys loses with a 409 conflict.
                await preferences.change(username, changes)

    # ── Internal objects beside the tree (notebook outputs) ──────────────

    def internal_object_key(self, username: str, *parts: str) -> str:
        """Key under the user's area for data that is not a tree entry.

        Kept under a dot folder (like ``.versions``) so it never appears in the
        Workspace tree and a rename never has to move it.
        """
        clean = [part.strip("/") for part in parts if part and part.strip("/")]
        if any(part in {".", ".."} or "/.." in part or "../" in part for part in clean):
            raise StorageError("Invalid internal object path", status_code=400)
        base_prefix = load_nova_app_config().workspace.base_prefix.strip("/")
        return "/".join([p for p in [base_prefix, username, *clean] if p])

    async def put_internal_object(self, key: str, body: bytes) -> None:
        await asyncio.to_thread(self._put_object, key, body)

    async def read_internal_object(self, key: str) -> bytes:
        return await asyncio.to_thread(self._read_object, key)

    async def read_entry_bytes(self, username: str, entry_id: str) -> tuple[dict, bytes]:
        """A file entry and its raw content, for copying into a runtime."""
        return await self._files.read_entry_bytes(username, entry_id)

    async def get_entry(self, username: str, entry_id: str) -> dict | None:
        return await self._files.get_entry(username, entry_id)

    async def list_entries(self, username: str) -> list[dict]:
        return await self._files.list_entries(username)

    async def namespace_of(self, entry_id: str) -> str | None:
        """Whose files a live entry belongs to, if the acting person may read it."""
        return await self._files.namespace_of(entry_id)

    def _put_object(self, key: str, body: bytes) -> dict:
        try:
            return self._client().put_object(Bucket=self._bucket(), Key=key, Body=body)
        except (ClientError, BotoCoreError) as exc:
            raise classify_storage_error(exc, action="save") from exc

    def _read_object(self, key: str) -> bytes:
        try:
            body = self._client().get_object(Bucket=self._bucket(), Key=key)["Body"]
            try:
                return body.read()
            finally:
                body.close()
        except ClientError as exc:
            raise classify_storage_error(exc, action="read") from exc
        except BotoCoreError as exc:
            raise classify_storage_error(exc, action="read") from exc

    def _delete_object(self, key: str) -> None:
        try:
            self._client().delete_object(Bucket=self._bucket(), Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise classify_storage_error(exc, action="delete") from exc


async def _audit_file_action(action: str, object_type: str, object_name: str) -> None:
    """The success audit of a file command, in that command's metadata transaction."""
    actor = current_scope().actor
    await write_audit_log(
        event_type="workspace",
        user_name=actor.principal,
        action=action,
        object_type=object_type,
        object_name=object_name,
        status="SUCCESS",
        session_id=actor.session_id,
        active_role=actor.active_role,
        security_context_version=actor.security_context_version,
    )


workspace_service = WorkspaceService()
