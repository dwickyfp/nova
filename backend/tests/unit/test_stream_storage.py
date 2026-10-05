from io import BytesIO

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from botocore.exceptions import ClientError

from app.modules.streams.schemas import StreamError
from app.modules.streams.storage import ChangeStorage, batch_key, journal_payload, validate_batch


def encoded(table):
    output = BytesIO()
    pq.write_table(table, output)
    return output.getvalue()


def test_exact_schema_and_rows_roundtrip():
    table = pa.table({"id": [1, 2], "value": ["first", None]})
    assert validate_batch(encoded(table), table.schema).equals(table)


def test_schema_mismatch_is_not_silently_cast():
    table = pa.table({"id": [1, 2]})
    with pytest.raises(StreamError) as error:
        validate_batch(encoded(table), pa.schema([("id", pa.string())]))
    assert error.value.code == "STREAM_SCHEMA_MISMATCH"


@pytest.mark.parametrize("payload", [b"", b"not parquet"])
def test_invalid_payload_has_safe_error(payload):
    with pytest.raises(StreamError) as error:
        validate_batch(payload, pa.schema([("id", pa.int64())]))
    assert repr(payload) not in str(error.value)


def test_reserved_metadata_names_are_rejected():
    table = pa.table({"NOVA$ACTION": ["INSERT"]})
    with pytest.raises(StreamError):
        validate_batch(encoded(table), table.schema)


@pytest.mark.parametrize("identity", ["../escape", "a/b", "", "x" * 64])
def test_object_path_cannot_escape(identity):
    with pytest.raises(StreamError):
        batch_key(identity, "a" * 64, 1, 1)


def test_journal_contains_stable_row_ids():
    table = pa.table({"id": [1, 1]})
    payload = journal_payload(table, "a" * 64, "b" * 64)
    journal = pq.read_table(BytesIO(payload))
    assert journal.column_names == ["id", "NOVA$ROW_ID"]
    assert len(set(journal["NOVA$ROW_ID"].to_pylist())) == 2
    assert payload == journal_payload(table, "a" * 64, "b" * 64)


class Objects:
    def __init__(self):
        self.data = {}

    def put_object(self, **kwargs):
        assert kwargs["IfNoneMatch"] == "*"
        key = kwargs["Key"]
        if key in self.data:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.data[key] = kwargs["Body"]

    def get_object(self, **kwargs):
        return {"Body": BytesIO(self.data[kwargs["Key"]])}


async def test_storage_retry_is_immutable(monkeypatch):
    objects = Objects()
    storage = ChangeStorage("configured")
    monkeypatch.setattr(storage, "_client", lambda: (objects, "internal"))
    args = {"source_id": "a" * 64, "batch_id": "b" * 64, "epoch": 1, "sequence": 1}
    manifest = await storage.put(**args, table=pa.table({"id": [1]}))
    assert manifest == await storage.put(**args, table=pa.table({"id": [1]}))
    with pytest.raises(StreamError) as error:
        await storage.put(**args, table=pa.table({"id": [2]}))
    assert error.value.code == "STREAM_BATCH_CONFLICT"
    assert manifest.object_key not in repr(manifest)


async def test_storage_provider_error_is_redacted(monkeypatch):
    storage = ChangeStorage("configured")

    def client():
        raise RuntimeError("s3://private/access_key=secret")

    monkeypatch.setattr(storage, "_client", client)
    with pytest.raises(StreamError) as error:
        await storage.put(
            source_id="a" * 64, batch_id="b" * 64, epoch=1, sequence=1,
            table=pa.table({"id": [1]}),
        )
    assert "private" not in str(error.value)
    assert "secret" not in str(error.value)
