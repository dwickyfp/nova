"""Immutable, bounded change batches over Nova's configured object storage."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from io import BytesIO

import pyarrow as pa
import pyarrow.parquet as pq

from app.modules.streams.schemas import StreamError, append_row_id

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_DECODED_BYTES = 256 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 256
METADATA_COLUMNS = (
    "NOVA$ACTION", "NOVA$IS_UPDATE", "NOVA$ROW_ID", "NOVA$COMMIT_ID",
    "NOVA$COMMIT_TS", "NOVA$SEQUENCE",
)


@dataclass(frozen=True, slots=True)
class BatchManifest:
    source_id: str
    batch_id: str
    epoch: int
    sequence: int
    checksum: str
    schema_fingerprint: str
    row_count: int
    byte_count: int
    # This relative key is internal metadata, not an API field or plan binding.
    object_key: str = dataclass_field(repr=False)


def _digest(value: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise StreamError("STREAM_BATCH_INVALID", "Invalid batch identity")
    return value


def batch_key(source_id: str, batch_id: str, epoch: int, sequence: int) -> str:
    if type(epoch) is not int or type(sequence) is not int or min(epoch, sequence) < 0:
        raise StreamError("STREAM_BATCH_INVALID", "Invalid batch position")
    return (
        f"changes/{_digest(source_id)}/epoch={epoch}/sequence={sequence}/"
        f"{_digest(batch_id)}.parquet"
    )


def schema_fingerprint(schema: pa.Schema) -> str:
    return hashlib.sha256(schema.remove_metadata().serialize().to_pybytes()).hexdigest()


def validate_batch(payload: bytes, expected: pa.Schema) -> pa.Table:
    if not payload or len(payload) > MAX_UPLOAD_BYTES:
        raise StreamError("STREAM_BATCH_LIMIT", "Batch exceeds the upload limit or is empty")
    if len(expected) > MAX_COLUMNS or any(
        field.name.upper().startswith("NOVA$") for field in expected
    ):
        raise StreamError("STREAM_SCHEMA_UNSUPPORTED", "Source schema is not supported")
    if len({field.name.casefold() for field in expected}) != len(expected):
        raise StreamError("STREAM_SCHEMA_UNSUPPORTED", "Duplicate source column names")
    supported = (
        pa.types.is_boolean, pa.types.is_integer, pa.types.is_floating,
        pa.types.is_decimal, pa.types.is_string, pa.types.is_date32, pa.types.is_timestamp,
    )
    for field in expected:
        if not any(check(field.type) for check in supported):
            raise StreamError("STREAM_SCHEMA_UNSUPPORTED", "Source column type is not supported")
        if pa.types.is_timestamp(field.type) and (
            field.type.tz is not None or field.type.unit != "us"
        ):
            raise StreamError("STREAM_SCHEMA_UNSUPPORTED", "Use microsecond wall-clock timestamps")
    try:
        parquet = pq.ParquetFile(BytesIO(payload))
        metadata = parquet.metadata
        decoded_size = sum(
            metadata.row_group(i).total_byte_size for i in range(metadata.num_row_groups)
        )
        if metadata.num_rows > MAX_ROWS or decoded_size > MAX_DECODED_BYTES:
            raise StreamError("STREAM_BATCH_LIMIT", "Batch exceeds the decoded size or row limit")
        if not parquet.schema_arrow.equals(expected, check_metadata=False):
            raise StreamError("STREAM_SCHEMA_MISMATCH", "Batch schema differs from the source")
        table = parquet.read(use_threads=False)
        if table.nbytes > MAX_DECODED_BYTES:
            raise StreamError("STREAM_BATCH_LIMIT", "Batch exceeds the decoded size limit")
        for field, column in zip(expected, table.columns, strict=True):
            if not field.nullable and column.null_count:
                raise StreamError("STREAM_BATCH_INVALID", "Null in required source column")
        return table.replace_schema_metadata(None)
    except StreamError:
        raise
    except Exception:
        raise StreamError("STREAM_BATCH_INVALID", "Cannot read the Parquet batch") from None


def journal_payload(table: pa.Table, source_id: str, batch_id: str) -> bytes:
    """Commit time and sequence remain manifest facts, added at query lowering."""
    _digest(source_id)
    _digest(batch_id)
    ids = pa.array([append_row_id(source_id, batch_id, n) for n in range(table.num_rows)])
    journal = table.append_column("NOVA$ROW_ID", ids)
    output = BytesIO()
    pq.write_table(journal, output, compression="zstd", version="2.6")
    payload = output.getvalue()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise StreamError("STREAM_BATCH_LIMIT", "Journal batch exceeds the storage limit")
    return payload


class ChangeStorage:
    def __init__(self, connection_name: str) -> None:
        self.connection_name = connection_name

    def _client(self):
        import boto3
        from botocore.config import Config

        from app.core.config import get_storage_connection, load_nova_app_config
        from app.modules.query.dialect.injector import resolve_storage_credentials

        if self.connection_name not in load_nova_app_config().storage_connections:
            raise StreamError("STREAM_STORAGE_UNAVAILABLE", "Storage connection is unavailable")
        connection = get_storage_connection(self.connection_name)
        key, secret = resolve_storage_credentials(self.connection_name)
        client = boto3.client(
            "s3", endpoint_url=connection.endpoint or None,
            aws_access_key_id=key or None, aws_secret_access_key=secret or None,
            region_name=connection.region or "us-east-1",
            config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=30),
        )
        return client, connection.bucket

    async def put(
        self, *, source_id: str, batch_id: str, epoch: int, sequence: int, table: pa.Table
    ) -> BatchManifest:
        payload = await asyncio.to_thread(journal_payload, table, source_id, batch_id)
        key = batch_key(source_id, batch_id, epoch, sequence)
        checksum = hashlib.sha256(payload).hexdigest()
        manifest = BatchManifest(
            source_id, batch_id, epoch, sequence, checksum, schema_fingerprint(table.schema),
            table.num_rows, len(payload), key,
        )
        await asyncio.to_thread(self._put, manifest, payload)
        return manifest

    def _put(self, manifest: BatchManifest, payload: bytes) -> None:
        from botocore.exceptions import ClientError

        try:
            client, bucket = self._client()
            try:
                client.put_object(
                    Bucket=bucket, Key=manifest.object_key, Body=payload,
                    IfNoneMatch="*", Metadata={"sha256": manifest.checksum},
                    ContentType="application/vnd.apache.parquet",
                )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in {"PreconditionFailed", "412"}:
                    raise
                existing = client.get_object(Bucket=bucket, Key=manifest.object_key)
                try:
                    body = existing["Body"].read(MAX_UPLOAD_BYTES + 1)
                finally:
                    existing["Body"].close()
                if hashlib.sha256(body).hexdigest() != manifest.checksum:
                    raise StreamError(
                        "STREAM_BATCH_CONFLICT", "Immutable batch already differs"
                    ) from None
        except StreamError:
            raise
        except Exception:
            raise StreamError(
                "STREAM_STORAGE_UNAVAILABLE", "Change storage is unavailable"
            ) from None
