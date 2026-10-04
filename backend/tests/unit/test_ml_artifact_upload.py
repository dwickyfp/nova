"""Object publication must follow successful length and checksum verification."""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.modules.ml_engine.artifacts.store import ObjectArtifactStore
from app.modules.ml_engine.service import MLEngineService


class Client:
    def __init__(self, failure):
        self.objects = {}
        self.failure = failure

    def put_object(self, *, Bucket, Key, Body):
        self.objects[Key] = Body
        if self.failure == "upload":
            raise OSError("upload failed")

    def head_object(self, *, Bucket, Key):
        return {"ContentLength": len(self.objects[Key]) + (self.failure == "size")}

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(b"corrupt" if self.failure == "checksum" else self.objects[Key])}

    def copy_object(self, *, Bucket, Key, CopySource):
        if self.failure == "copy":
            raise OSError("copy failed")
        self.objects[Key] = self.objects[CopySource["Key"]]

    def delete_object(self, *, Bucket, Key):
        if self.failure == "cleanup":
            raise OSError("cleanup failed")
        self.objects.pop(Key, None)


@pytest.mark.parametrize("failure", ["upload", "size", "checksum", "copy", None, "cleanup"])
def test_atomic_publication(monkeypatch, failure):
    client = Client(failure)
    store = ObjectArtifactStore("test")
    monkeypatch.setattr(store, "_client", lambda: client)
    monkeypatch.setattr(store, "_connection", lambda: SimpleNamespace(bucket="test"))
    if failure in {"upload", "size", "checksum", "copy"}:
        with pytest.raises(OSError):
            store.put("model.joblib", b"complete-model")
        assert not client.objects
    else:
        assert store.put("model.joblib", b"complete-model").endswith("/model.joblib")
        assert client.objects["model.joblib"] == b"complete-model"
        assert len(client.objects) == (2 if failure == "cleanup" else 1)


@pytest.mark.parametrize("failure", ["delete", "remains"])
async def test_failed_model_artifact_cleanup_retains_retryable_registry_identity(failure):
    store = Mock()
    store.exists.return_value = failure == "remains"
    if failure == "delete":
        store.delete.side_effect = OSError("credential-bearing provider detail")
    service = MLEngineService(artifact_store=store)
    service.get_model = AsyncMock(
        return_value={"versions": [{"artifact_uri": "nova-artifact://test/owned-model.joblib"}]}
    )
    service.repository = Mock()
    service.repository._connect = AsyncMock()
    with pytest.raises(OSError, match="model metadata retained") as error:
        await service.delete_model("owned-model", owner_name="alice", database_name="db")
    assert "credential-bearing" not in str(error.value)
    service.repository._connect.assert_not_awaited()


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "AccessDenied"])
def test_artifact_absence_requires_a_missing_object_response(monkeypatch, code):
    from botocore.exceptions import ClientError

    client = Mock()
    client.head_object.side_effect = ClientError({"Error": {"Code": code}}, "HeadObject")
    store = ObjectArtifactStore("test")
    monkeypatch.setattr(store, "_client", lambda: client)
    monkeypatch.setattr(store, "_connection", lambda: SimpleNamespace(bucket="test"))
    if code == "AccessDenied":
        with pytest.raises(ClientError):
            store.exists("nova-artifact://test/owned-model.joblib")
    else:
        assert not store.exists("nova-artifact://test/owned-model.joblib")
