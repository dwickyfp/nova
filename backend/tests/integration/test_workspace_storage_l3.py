from __future__ import annotations

from uuid import uuid4

import pytest
from botocore.exceptions import ClientError


@pytest.mark.engine
async def test_workspace_file_lifecycle_uses_real_engine_and_storage(
    client, admin_token, minio_client
):
    try:
        minio_client.create_bucket(Bucket="stages")
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {
            "BucketAlreadyExists",
            "BucketAlreadyOwnedByYou",
        }:
            raise

    client.headers["Authorization"] = f"Bearer {admin_token}"
    name = f"audit_{uuid4().hex}.sql"
    created = await client.post(
        "/api/v1/workspaces/files",
        json={"name": name, "parent_path": "", "content": "SELECT 1"},
    )
    assert created.status_code == 201, created.text
    entry_id = created.json()["entry"]["id"]

    try:
        opened = await client.get(f"/api/v1/workspaces/files/{entry_id}")
        assert opened.status_code == 200, opened.text
        assert opened.json()["content"] == "SELECT 1"

        updated = await client.put(
            f"/api/v1/workspaces/files/{entry_id}", json={"content": "SELECT 2"}
        )
        assert updated.status_code == 200, updated.text

        versions = await client.get(f"/api/v1/workspaces/files/{entry_id}/versions")
        assert versions.status_code == 200, versions.text
        assert len(versions.json()["versions"]) == 1

        previous = await client.get(
            f"/api/v1/workspaces/files/{entry_id}/versions/1"
        )
        assert previous.status_code == 200, previous.text
        assert previous.json()["content"] == "SELECT 1"

        renamed = await client.post(
            "/api/v1/workspaces/rename",
            json={"id": entry_id, "name": f"renamed_{name}"},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["entry"]["name"] == f"renamed_{name}"
    finally:
        deleted = await client.delete(f"/api/v1/workspaces/files/{entry_id}")
        assert deleted.status_code == 200, deleted.text

    missing = await client.get(f"/api/v1/workspaces/files/{entry_id}")
    assert missing.status_code == 404, missing.text
