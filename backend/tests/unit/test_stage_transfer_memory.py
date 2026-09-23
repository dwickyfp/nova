import tempfile
import tracemalloc

import pytest

from app.modules.stages.service import StageService


class RecordingStorage:
    def __init__(self):
        self.bytes_uploaded = 0

    def upload_fileobj(self, stream, bucket, key):
        assert bucket == "bucket"
        assert key == "db/schema/stage/file.bin"
        while chunk := stream.read(64 * 1024):
            self.bytes_uploaded += len(chunk)


@pytest.mark.asyncio
async def test_stage_upload_keeps_large_file_out_of_python_heap(monkeypatch):
    storage = RecordingStorage()
    stage = {
        "name": "stage",
        "database_name": "db",
        "schema_name": "schema",
        "base_prefix": "db/schema/stage",
    }

    async def get_stage(self, _):
        return stage

    monkeypatch.setattr(StageService, "get_stage", get_stage)
    monkeypatch.setattr(
        StageService, "_s3_client_for_stage", staticmethod(lambda _: (storage, "bucket"))
    )

    with tempfile.TemporaryFile() as stream:
        for _ in range(128):
            stream.write(b"x" * 64 * 1024)
        stream.seek(0)

        tracemalloc.start()
        try:
            result = await StageService().upload_stream("stage-id", "file.bin", stream)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

    assert result["size"] == 8 * 1024 * 1024
    assert storage.bytes_uploaded == result["size"]
    assert peak < 2 * 1024 * 1024
