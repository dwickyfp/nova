"""Object publication must follow successful length and checksum verification."""

import io
from types import SimpleNamespace

import pytest

from app.modules.ml_engine.artifacts.store import ObjectArtifactStore


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
