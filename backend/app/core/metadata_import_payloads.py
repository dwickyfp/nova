"""Read-only verification of managed payloads referenced by an import snapshot.

References stay private. The report contains row addresses, byte counts and
content digests; model bytes are streamed and never deserialized or executed.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from app.core.metadata_import_contract import ImportContract
from app.core.metadata_import_manifest import (
    ExportManifest,
    canonical_json,
    read_chunk,
    sha256_bytes,
)


@dataclass(frozen=True, repr=False)
class PayloadReference:
    table: str
    row_address: str
    kind: str
    reference: str
    expected_size: int | None
    expected_sha256: str | None = None
    expected_etag: str | None = None


class PayloadVerificationError(ValueError):
    def __init__(self, reference: PayloadReference, reason: str):
        self.table = reference.table
        self.row_address = reference.row_address
        self.reason = reason
        super().__init__("Managed payload verification failed: " + reason)


def references(export: ExportManifest, contract: ImportContract) -> Iterator[PayloadReference]:
    tables = {
        "CONFIG_WORKSPACE_ENTRIES",
        "CONFIG_WORKSPACE_FILE_VERSIONS",
        "CONFIG_NOTEBOOK_EXECUTIONS",
        "ML_MODEL_VERSIONS",
    }
    for name in sorted(tables & contract.tables.keys()):
        table = contract.tables[name]
        for chunk_index, chunk in export.chunks(name):
            for index, values in enumerate(read_chunk(export.directory, chunk, len(table.columns))):
                row = dict(zip(table.column_names, values, strict=True))
                address = sha256_bytes(canonical_json([name, chunk_index, index, chunk["sha256"]]))
                if name == "CONFIG_WORKSPACE_ENTRIES" and (
                    row["is_deleted"] or row["entry_type"] != "file"
                ):
                    continue
                if name == "ML_MODEL_VERSIONS":
                    key, size, digest, etag = (
                        row["artifact_uri"],
                        row["artifact_size"],
                        row["artifact_sha256"],
                        None,
                    )
                    kind = "model"
                elif name == "CONFIG_NOTEBOOK_EXECUTIONS":
                    key, size, digest, etag = row["output_key"], row["output_bytes"], None, None
                    kind = "workspace"
                else:
                    key, size, digest, etag = (
                        row["object_key"],
                        row["size_bytes"],
                        None,
                        row["etag"],
                    )
                    kind = "workspace"
                reference = PayloadReference(name, address, kind, key or "", size, digest, etag)
                required = name in {"CONFIG_WORKSPACE_ENTRIES", "CONFIG_WORKSPACE_FILE_VERSIONS"}
                required = required or (name == "ML_MODEL_VERSIONS" and row["status"] == "READY")
                if required and not key:
                    raise PayloadVerificationError(reference, "missing_reference")
                if key:
                    yield reference


class ManagedPayloadReader:
    """Use each existing storage owner; never accept arbitrary URLs or deserialize models."""

    def __call__(self, reference: PayloadReference) -> tuple[int, str, str]:
        if reference.kind == "workspace":
            from app.modules.workspaces.service import workspace_service

            client, bucket, key = (
                workspace_service._client(),
                workspace_service._bucket(),
                reference.reference,
            )
        else:
            from app.modules.ml_engine.artifacts.store import ObjectArtifactStore

            connection, key = ObjectArtifactStore._parse(reference.reference)
            store = ObjectArtifactStore(connection)
            client, bucket = store._client(), store._connection().bucket
        response = client.get_object(Bucket=bucket, Key=key)
        body, digest, size = response["Body"], hashlib.sha256(), 0
        try:
            for block in iter(lambda: body.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
        finally:
            body.close()
        if size != int(response["ContentLength"]):
            raise PayloadVerificationError(reference, "incomplete_read")
        return size, digest.hexdigest(), str(response.get("ETag") or "").strip('"')


async def verify_payloads(
    export: ExportManifest,
    contract: ImportContract,
    reader: Callable[[PayloadReference], tuple[int, str, str]] | None = None,
) -> dict[str, Any]:
    reader = reader or ManagedPayloadReader()
    receipts = []
    for reference in references(export, contract):
        try:
            size, digest, etag = await asyncio.to_thread(reader, reference)
        except Exception:
            raise PayloadVerificationError(reference, "unavailable") from None
        expected_etag = (reference.expected_etag or "").strip('"')
        if reference.expected_size is not None and size != reference.expected_size:
            raise PayloadVerificationError(reference, "size_mismatch")
        if reference.expected_sha256 and digest != reference.expected_sha256:
            raise PayloadVerificationError(reference, "digest_mismatch")
        if expected_etag and expected_etag not in (etag, "sha256:" + digest):
            raise PayloadVerificationError(reference, "etag_mismatch")
        receipts.append(
            {
                "source_table": reference.table,
                "row_address": reference.row_address,
                "size_bytes": size,
                "content_sha256": digest,
            }
        )
    return {
        "status": "VERIFIED",
        "count": len(receipts),
        "receipts_sha256": sha256_bytes(canonical_json(receipts)),
        "receipts": receipts,
    }
