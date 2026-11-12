"""Version history for workspace files (NOVA-XXX).

Each save snapshots the file's *previous* content as an immutable version, so a
save is recoverable. The tests drive ``WorkspaceService`` with in-memory fakes
for the repository and object storage, because the behaviour under test is the
snapshot/dedup decision, not S3 or StarRocks.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.modules.workspaces.metadata_service import WorkspaceMetadataService, WorkspaceObjectStore
from app.modules.workspaces.service import WorkspaceService


class _FakeRepo:
    def __init__(self, entry: dict):
        self.entry = entry
        self.versions: list[dict] = []

    async def get_entry(self, _namespace, entry_id):
        return dict(self.entry) if self.entry["id"] == entry_id else None

    async def update_file(self, _namespace, entry_id, *, expected_revision, **changes):
        from app.core.metadata_db import MetadataConflict

        assert entry_id == self.entry["id"]
        if expected_revision != self.entry["metadata_revision"]:
            raise MetadataConflict()
        self.entry.update(changes, metadata_revision=expected_revision + 1)
        return dict(self.entry)

    async def reserve_version(self, _namespace, entry_id, *, object_key_for):
        number = len(self.versions) + 1
        row = {
            "id": str(number),
            "entry_id": entry_id,
            "version": number,
            "object_key": object_key_for(number),
        }
        self.versions.append(row)
        return row

    async def publish_version(self, _namespace, version_id, **fields):
        next(row for row in self.versions if row["id"] == version_id).update(fields)

    async def list_versions(self, _namespace, _entry_id, limit=100):
        return list(reversed(self.versions))[:limit]

    async def get_version(self, _namespace, _entry_id, version):
        return next((row for row in self.versions if row["version"] == version), None)


class _FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.put_count = 0

    def put_object(self, Bucket, Key, Body):  # noqa: N803 — boto3 keyword names
        self.objects[Key] = Body
        self.put_count += 1
        return {"ETag": f'"etag-{self.put_count}"'}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop(Key, None)

    def get_object(self, Bucket, Key):  # noqa: N803
        from app.core.exceptions import StorageError

        if Key not in self.objects:
            raise StorageError("object gone", status_code=404)

        class _Body:
            def __init__(self, data: bytes):
                self._data = data

            def read(self) -> bytes:
                return self._data

            def close(self) -> None:
                pass

        return {"Body": _Body(self.objects[Key])}


def _entry(object_key: str = "workspaces/analyst/Untitled.sql") -> dict:
    return {
        "id": "e1",
        "user_name": "analyst",
        "parent_path": "",
        "name": "Untitled.sql",
        "path": "Untitled.sql",
        "entry_type": "file",
        "object_key": object_key,
        "size_bytes": 0,
        "etag": None,
        "created_at": None,
        "updated_at": None,
        "metadata_revision": 1,
    }


def _service(
    monkeypatch, *, initial: str | None = "SELECT 1"
) -> tuple[WorkspaceService, _FakeRepo, _FakeStorage]:
    repo = _FakeRepo(_entry())
    storage = _FakeStorage()
    if initial is not None:
        storage.objects["workspaces/analyst/Untitled.sql"] = initial.encode()

    service = WorkspaceService()

    @asynccontextmanager
    async def transaction(*, read_only=False):
        yield repo

    files = WorkspaceMetadataService(transaction, WorkspaceObjectStore(service))
    monkeypatch.setattr(WorkspaceService, "_files", property(lambda self: files))
    service._client = lambda: storage  # type: ignore[method-assign]
    monkeypatch.setattr(
        "app.modules.workspaces.service.load_nova_app_config",
        lambda: type(
            "C",
            (),
            {
                "workspace": type(
                    "W", (), {"base_prefix": "workspaces", "storage_connection": "default"}
                )()
            },
        )(),
    )
    monkeypatch.setattr(
        "app.modules.workspaces.service.get_storage_connection",
        lambda _name: type("Conn", (), {"bucket": "nova-stages", "endpoint": "", "region": ""})(),
    )
    return service, repo, storage


class TestSaveSnapshotsPreviousContent:
    async def test_first_save_snapshots_the_initial_content(self, monkeypatch):
        service, repo, _storage = _service(monkeypatch, initial="SELECT 1")

        await service.update_file("analyst", "e1", "SELECT 2")

        assert [v["version"] for v in repo.versions] == [1]
        version = repo.versions[0]
        assert version["object_key"] == "workspaces/analyst/Untitled.sql"
        assert version["object_key"] != repo.entry["object_key"]

    async def test_second_save_snapshots_the_current_content(self, monkeypatch):
        service, repo, storage = _service(monkeypatch, initial="SELECT 1")
        await service.update_file("analyst", "e1", "SELECT 2")
        await service.update_file("analyst", "e1", "SELECT 3")

        assert [v["version"] for v in repo.versions] == [1, 2]
        # Version 2 must hold what "SELECT 2" wrote, not the first content.
        assert storage.objects[repo.versions[1]["object_key"]] == b"SELECT 2"

    async def test_live_object_is_updated_after_snapshot(self, monkeypatch):
        service, repo, storage = _service(monkeypatch, initial="SELECT 1")
        await service.update_file("analyst", "e1", "SELECT 42")

        assert storage.objects[repo.entry["object_key"]] == b"SELECT 42"
        assert storage.objects["workspaces/analyst/Untitled.sql"] == b"SELECT 1"


class TestDedup:
    async def test_identical_save_does_not_create_a_version(self, monkeypatch):
        service, repo, _storage = _service(monkeypatch, initial="SELECT 1")
        await service.update_file("analyst", "e1", "SELECT 1")

        assert repo.versions == []

    async def test_repeated_identical_saves_stay_deduped(self, monkeypatch):
        service, repo, _storage = _service(monkeypatch, initial="SELECT 1")
        await service.update_file("analyst", "e1", "SELECT 1")
        await service.update_file("analyst", "e1", "SELECT 1")

        assert repo.versions == []

    async def test_change_then_revert_creates_two_versions(self, monkeypatch):
        service, repo, _storage = _service(monkeypatch, initial="SELECT 1")
        await service.update_file("analyst", "e1", "SELECT 2")
        await service.update_file("analyst", "e1", "SELECT 1")

        # Snapshot 1 = "SELECT 1", snapshot 2 = "SELECT 2" (the content replaced
        # by the revert); both are distinct from their immediate predecessor.
        assert [v["version"] for v in repo.versions] == [1, 2]

    async def test_empty_file_has_no_history(self, monkeypatch):
        service, repo, _storage = _service(monkeypatch, initial="")

        await service.update_file("analyst", "e1", "SELECT 1")

        assert repo.versions == []

    async def test_first_write_to_new_object_has_no_predecessor(self, monkeypatch):
        service, repo, _storage = _service(monkeypatch, initial=None)

        await service.update_file("analyst", "e1", "SELECT 1")

        assert repo.versions == []


class TestVersionReads:
    async def test_list_versions_is_newest_first(self, monkeypatch):
        service, _repo, _storage = _service(monkeypatch, initial="SELECT 1")
        await service.update_file("analyst", "e1", "SELECT 2")
        await service.update_file("analyst", "e1", "SELECT 3")

        versions = await service.list_file_versions("analyst", "e1")
        assert [v["version"] for v in versions] == [2, 1]

    async def test_get_version_record_returns_content(self, monkeypatch):
        service, _repo, _storage = _service(monkeypatch, initial="SELECT 1")
        await service.update_file("analyst", "e1", "SELECT 2")

        record, content = await service.get_file_version_record("analyst", "e1", 1)
        assert record["version"] == 1
        assert content == "SELECT 1"

    async def test_missing_version_is_404(self, monkeypatch):
        import pytest

        from app.core.exceptions import StorageError

        service, _repo, _storage = _service(monkeypatch, initial="SELECT 1")

        with pytest.raises(StorageError) as caught:
            await service.get_file_version_record("analyst", "e1", 99)
        assert caught.value.status_code == 404


class TestVersionObjectKeysAreIsolated:
    async def test_version_key_never_equals_live_key(self, monkeypatch):
        service, repo, storage = _service(monkeypatch)
        await service.update_file("analyst", "e1", "SELECT 2")
        assert repo.entry["object_key"] != repo.versions[0]["object_key"]
        assert len(storage.objects) == 2


@pytest.mark.parametrize("status", [502, 503])
async def test_unavailable_previous_content_refuses_save_without_losing_history(
    monkeypatch, status
):
    from app.core.exceptions import StorageError

    service, repo, storage = _service(monkeypatch)
    before = dict(repo.entry)

    def unavailable(**kwargs):
        raise StorageError("Storage unavailable", status_code=status)

    monkeypatch.setattr(storage, "get_object", unavailable)
    with pytest.raises(StorageError) as error:
        await service.update_file("analyst", "e1", "SELECT 2")
    assert error.value.status_code == status
    assert repo.entry == before and repo.versions == []
    assert storage.put_count == 0
