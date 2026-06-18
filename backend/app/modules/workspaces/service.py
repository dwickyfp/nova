from __future__ import annotations

import json
from uuid import uuid4

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.common.audit import write_audit_log
from app.core.config import get_storage_connection, load_nova_app_config
from app.core.exceptions import StorageError
from app.modules.workspaces.repository import workspace_repository


class WorkspaceService:
    PREF_OPEN_TABS = "workspace.open_tabs"
    PREF_ACTIVE_TAB = "workspace.active_tab"
    PREF_SIDEBAR_STATE = "workspace.sidebar_state"
    PREF_LAST_DATABASE = "workspace.last_database"
    PREF_LAST_SCHEMA = "workspace.last_schema"
    PREF_LAST_ROLE = "workspace.last_role"

    def __init__(self):
        self._repo = workspace_repository

    def _client(self):
        config = load_nova_app_config()
        conn = get_storage_connection(config.workspace.storage_connection)
        return boto3.client(
            "s3",
            endpoint_url=conn.endpoint or None,
            aws_access_key_id=conn.access_key,
            aws_secret_access_key=conn.secret_key,
            config=BotoConfig(signature_version="s3v4"),
            region_name=conn.region or "us-east-1",
        )

    def _bucket(self) -> str:
        config = load_nova_app_config()
        return get_storage_connection(config.workspace.storage_connection).bucket

    def build_object_key(self, username: str, relative_path: str) -> str:
        base_prefix = load_nova_app_config().workspace.base_prefix.strip("/")
        clean_path = relative_path.strip("/")
        return "/".join(part for part in [base_prefix, username, clean_path] if part)

    async def get_tree(self, username: str) -> dict:
        entries = await self._repo.list_entries(username)
        prefs = await self._repo.get_preferences(
            username,
            [
                self.PREF_OPEN_TABS,
                self.PREF_ACTIVE_TAB,
                self.PREF_SIDEBAR_STATE,
                self.PREF_LAST_DATABASE,
                self.PREF_LAST_SCHEMA,
                self.PREF_LAST_ROLE,
            ],
        )
        return {
            "root_name": "My Workspace",
            "entries": entries,
            "open_tabs": json.loads(prefs.get(self.PREF_OPEN_TABS, "[]")),
            "active_tab": prefs.get(self.PREF_ACTIVE_TAB),
            "sidebar_collapsed": prefs.get(self.PREF_SIDEBAR_STATE, "false") == "true",
            "defaults": {
                "database": prefs.get(self.PREF_LAST_DATABASE),
                "schema": prefs.get(self.PREF_LAST_SCHEMA),
                "role": prefs.get(self.PREF_LAST_ROLE),
            },
        }

    async def create_file(self, username: str, parent_path: str, name: str, content: str) -> dict:
        path = self._repo.build_path(parent_path, name)
        key = self.build_object_key(username, path)
        result = self._put_object(key, content.encode("utf-8"))
        entry_id = str(uuid4())
        await self._repo.insert_entry(
            entry_id=entry_id,
            username=username,
            parent_path=parent_path.strip("/"),
            name=name,
            entry_type="file",
            object_key=key,
            size_bytes=len(content.encode("utf-8")),
            etag=result.get("ETag", "").strip('"'),
        )
        entry = await self._repo.get_entry(username, entry_id)
        await write_audit_log(
            event_type="workspace",
            user_name=username,
            action="create_file",
            object_type="workspace_file",
            object_name=path,
            status="SUCCESS",
        )
        return entry

    async def create_folder(self, username: str, parent_path: str, name: str) -> dict:
        entry_id = str(uuid4())
        await self._repo.insert_entry(
            entry_id=entry_id,
            username=username,
            parent_path=parent_path.strip("/"),
            name=name,
            entry_type="folder",
            object_key=None,
        )
        entry = await self._repo.get_entry(username, entry_id)
        await write_audit_log(
            event_type="workspace",
            user_name=username,
            action="create_folder",
            object_type="workspace_folder",
            object_name=entry["path"],
            status="SUCCESS",
        )
        return entry

    async def get_file(self, username: str, entry_id: str) -> tuple[dict, str]:
        entry = await self._repo.get_entry(username, entry_id)
        if not entry:
            raise StorageError("Workspace entry not found", status_code=404)
        if entry["entry_type"] != "file":
            raise StorageError("Only SQL files can be opened", status_code=400)
        content = self._read_object(entry["object_key"])
        return entry, content.decode("utf-8")

    async def update_file(self, username: str, entry_id: str, content: str) -> dict:
        entry = await self._repo.get_entry(username, entry_id)
        if not entry:
            raise StorageError("Workspace entry not found", status_code=404)
        result = self._put_object(entry["object_key"], content.encode("utf-8"))
        await self._repo.update_entry(
            entry_id=entry_id,
            username=username,
            parent_path=entry["parent_path"],
            name=entry["name"],
            object_key=entry["object_key"],
            size_bytes=len(content.encode("utf-8")),
            etag=result.get("ETag", "").strip('"'),
        )
        updated = await self._repo.get_entry(username, entry_id)
        return updated

    async def rename_entry(
        self,
        username: str,
        entry_id: str,
        new_name: str,
        new_parent_path: str | None = None,
    ) -> dict:
        entry = await self._repo.get_entry(username, entry_id)
        if not entry:
            raise StorageError("Workspace entry not found", status_code=404)
        target_parent = (
            new_parent_path.strip("/") if new_parent_path is not None else entry["parent_path"]
        )
        old_path = entry["path"]
        new_path = self._repo.build_path(target_parent, new_name)
        if entry["entry_type"] == "file":
            new_object_key = self.build_object_key(username, new_path)
            self._move_object(entry["object_key"], new_object_key)
            await self._repo.update_entry(
                entry_id=entry_id,
                username=username,
                parent_path=target_parent,
                name=new_name,
                object_key=new_object_key,
                size_bytes=entry["size_bytes"],
                etag=entry["etag"],
            )
        else:
            descendants = await self._repo.list_entries(username)
            for item in descendants:
                if item["path"] == old_path or not item["path"].startswith(f"{old_path}/"):
                    continue
                rel_suffix = item["path"][len(old_path):].lstrip("/")
                if item["entry_type"] == "file" and item["object_key"]:
                    new_key = self.build_object_key(username, f"{new_path}/{rel_suffix}")
                    self._move_object(item["object_key"], new_key)
                    await self._repo.update_entry(
                        entry_id=item["id"],
                        username=username,
                        parent_path=self._parent_of(
                            self._repo.build_path(new_path, rel_suffix.rsplit("/", 1)[0])
                            if "/" in rel_suffix
                            else new_path
                        ),
                        name=rel_suffix.split("/")[-1],
                        object_key=new_key,
                        size_bytes=item["size_bytes"],
                        etag=item["etag"],
                    )
                elif item["entry_type"] == "folder":
                    new_child_path = f"{new_path}/{rel_suffix}"
                    await self._repo.update_entry(
                        entry_id=item["id"],
                        username=username,
                        parent_path=self._parent_of(new_child_path),
                        name=new_child_path.split("/")[-1],
                        object_key=None,
                        size_bytes=0,
                        etag=item["etag"],
                    )
            await self._repo.update_entry(
                entry_id=entry_id,
                username=username,
                parent_path=target_parent,
                name=new_name,
                object_key=None,
                size_bytes=0,
                etag=entry["etag"],
            )
        renamed = await self._repo.get_entry(username, entry_id)
        await write_audit_log(
            event_type="workspace",
            user_name=username,
            action="rename",
            object_type=f"workspace_{entry['entry_type']}",
            object_name=f"{old_path} -> {renamed['path']}",
            status="SUCCESS",
        )
        return renamed

    async def delete_entry(self, username: str, entry_id: str) -> None:
        entry = await self._repo.get_entry(username, entry_id)
        if not entry:
            raise StorageError("Workspace entry not found", status_code=404)
        all_entries = await self._repo.list_entries(username)
        targets = [
            item for item in all_entries
            if item["id"] == entry_id or item["path"].startswith(f"{entry['path']}/")
        ]
        for item in sorted(targets, key=lambda row: len(row["path"].split("/")), reverse=True):
            if item["entry_type"] == "file" and item["object_key"]:
                self._delete_object(item["object_key"])
            await self._repo.soft_delete_entry(username, item["id"])
        await write_audit_log(
            event_type="workspace",
            user_name=username,
            action="delete",
            object_type=f"workspace_{entry['entry_type']}",
            object_name=entry["path"],
            status="SUCCESS",
        )

    async def save_state(
        self,
        username: str,
        *,
        open_tabs: list[str],
        active_tab: str | None,
        sidebar_collapsed: bool,
        last_database: str | None,
        last_schema: str | None,
        last_role: str | None,
    ) -> None:
        await self._repo.set_preference(username, self.PREF_OPEN_TABS, json.dumps(open_tabs))
        await self._repo.set_preference(username, self.PREF_ACTIVE_TAB, active_tab or "")
        await self._repo.set_preference(
            username, self.PREF_SIDEBAR_STATE, "true" if sidebar_collapsed else "false"
        )
        if last_database is not None:
            await self._repo.set_preference(username, self.PREF_LAST_DATABASE, last_database)
        if last_schema is not None:
            await self._repo.set_preference(username, self.PREF_LAST_SCHEMA, last_schema)
        if last_role is not None:
            await self._repo.set_preference(username, self.PREF_LAST_ROLE, last_role)

    def _put_object(self, key: str, body: bytes) -> dict:
        try:
            return self._client().put_object(Bucket=self._bucket(), Key=key, Body=body)
        except ClientError as exc:
            raise StorageError(f"Failed to save workspace file: {exc}") from exc

    def _read_object(self, key: str) -> bytes:
        try:
            return self._client().get_object(Bucket=self._bucket(), Key=key)["Body"].read()
        except ClientError as exc:
            raise StorageError(f"Failed to read workspace file: {exc}", status_code=404) from exc

    def _delete_object(self, key: str) -> None:
        try:
            self._client().delete_object(Bucket=self._bucket(), Key=key)
        except ClientError:
            return

    def _move_object(self, source_key: str, dest_key: str) -> None:
        client = self._client()
        try:
            client.copy_object(
                Bucket=self._bucket(),
                CopySource={"Bucket": self._bucket(), "Key": source_key},
                Key=dest_key,
            )
            client.delete_object(Bucket=self._bucket(), Key=source_key)
        except ClientError as exc:
            raise StorageError(f"Failed to move workspace file: {exc}") from exc

    @staticmethod
    def _parent_of(path: str) -> str:
        return path.rsplit("/", 1)[0] if "/" in path else ""


workspace_service = WorkspaceService()
