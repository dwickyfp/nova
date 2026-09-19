"""Version history for workspace files (NOVA-XXX).

Each save snapshots the file's *previous* content as an immutable version, so a
save is recoverable. The tests drive ``WorkspaceService`` with in-memory fakes
for the repository and object storage, because the behaviour under test is the
snapshot/dedup decision, not S3 or StarRocks.
"""

from __future__ import annotations

from app.modules.workspaces.service import WorkspaceService


class _FakeRepo:
    def __init__(self, entry: dict | None):
        self.entry = entry
        self.versions: list[dict] = []
        self._seq = 0

    def build_path(self, parent_path: str, name: str) -> str:
        return "/".join(part for part in [parent_path.strip("/"), name.strip("/")] if part)

    async def get_entry(self, _username, _entry_id):
        return self.entry

    async def update_entry(self, **_kwargs):
        return None

    async def next_version_number(self, _username, _entry_id):
        self._seq += 1
        return max((v["version"] for v in self.versions), default=0) + 1

    async def insert_version(self, **kwargs):
        self.versions.append(
            {
                "id": kwargs["version_id"],
                "entry_id": kwargs["entry_id"],
                "version": kwargs["version"],
                "object_key": kwargs["object_key"],
                "size_bytes": kwargs["size_bytes"],
                "etag": kwargs["etag"],
            }
        )

    async def get_latest_version(self, _username, _entry_id):
        if not self.versions:
            return None
        return max(self.versions, key=lambda v: v["version"])

    async def list_versions(self, _username, _entry_id, limit=100):
        return sorted(self.versions, key=lambda v: v["version"], reverse=True)[:limit]

    async def get_version(self, _username, _entry_id, version):
        match = [v for v in self.versions if v["version"] == version]
        return match[0] if match else None

    async def delete_versions_for_entry(self, _username, _entry_id):
        self.versions.clear()


class _FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.put_count = 0

    def put_object(self, Bucket, Key, Body):  # noqa: N803 — boto3 keyword names
        self.objects[Key] = Body
        self.put_count += 1
        return {"ETag": f'"etag-{self.put_count}"'}

    def get_object(self, Bucket, Key):  # noqa: N803
        from app.core.exceptions import StorageError

        if Key not in self.objects:
            raise StorageError("object gone", status_code=404)

        class _Body:
            def __init__(self, data: bytes):
                self._data = data

            def read(self) -> bytes:
                return self._data

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
    }


def _service(
    monkeypatch, *, initial: str | None = "SELECT 1"
) -> tuple[WorkspaceService, _FakeRepo, _FakeStorage]:
    repo = _FakeRepo(_entry())
    storage = _FakeStorage()
    if initial is not None:
        storage.objects["workspaces/analyst/Untitled.sql"] = initial.encode()

    service = WorkspaceService()
    service._repo = repo
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
        assert version["object_key"].endswith(".versions/e1/1.sql")

    async def test_second_save_snapshots_the_current_content(self, monkeypatch):
        service, repo, storage = _service(monkeypatch, initial="SELECT 1")
        await service.update_file("analyst", "e1", "SELECT 2")
        await service.update_file("analyst", "e1", "SELECT 3")

        assert [v["version"] for v in repo.versions] == [1, 2]
        # Version 2 must hold what "SELECT 2" wrote, not the first content.
        assert storage.objects["workspaces/analyst/.versions/e1/2.sql"] == b"SELECT 2"

    async def test_live_object_is_updated_after_snapshot(self, monkeypatch):
        service, _repo, storage = _service(monkeypatch, initial="SELECT 1")
        await service.update_file("analyst", "e1", "SELECT 42")

        assert storage.objects["workspaces/analyst/Untitled.sql"] == b"SELECT 42"


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
    def test_version_key_never_equals_live_key(self, monkeypatch):
        service, _repo, _storage = _service(monkeypatch)

        live = service.build_object_key("analyst", "Untitled.sql")
        version = service._version_object_key("analyst", "e1", 1)

        assert live != version
        assert ".versions/e1/1.sql" in version
