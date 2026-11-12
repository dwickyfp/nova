"""Payload receipts validate bytes without leaking source addresses or executing models."""

from hashlib import sha256
from types import SimpleNamespace

import pytest

from app.core import metadata_import_payloads as module
from app.core.metadata_import_contract import ImportContract, load_contract
from app.core.metadata_import_manifest import encode_chunk, write_chunk


@pytest.fixture
def source(tmp_path):
    table = load_contract().tables["CONFIG_WORKSPACE_ENTRIES"]
    row = {
        "id": "file-1",
        "user_name": "owner",
        "parent_path": "/",
        "name": "file.txt",
        "entry_type": "file",
        "object_key": "private-canary/key",
        "size_bytes": 3,
        "etag": "etag",
        "is_deleted": 0,
    }
    data = encode_chunk([[row.get(c.name) for c in table.columns]])
    chunk = write_chunk(tmp_path, "CONFIG_WORKSPACE_ENTRIES/000000.jsonl.gz", data)
    chunk["rows"] = 1
    export = SimpleNamespace(directory=tmp_path, chunks=lambda _: [(0, chunk)])
    return export, ImportContract("a" * 64, {table.source_table: table})


async def test_verified_receipts_contain_no_payload_or_private_reference(source):
    export, contract = source
    report = await module.verify_payloads(
        export, contract, lambda _: (3, sha256(b"abc").hexdigest(), "etag")
    )
    assert report["status"] == "VERIFIED" and report["count"] == 1
    assert report["receipts"][0]["content_sha256"] == sha256(b"abc").hexdigest()
    assert "private-canary" not in str(report)


@pytest.mark.parametrize(
    "reply,reason",
    [
        ((2, "a" * 64, "etag"), "size_mismatch"),
        ((3, "a" * 64, "changed"), "etag_mismatch"),
    ],
)
async def test_mismatched_payload_refuses_without_exposing_key(source, reply, reason):
    with pytest.raises(module.PayloadVerificationError) as caught:
        await module.verify_payloads(*source, lambda _: reply)
    assert caught.value.reason == reason
    assert "private-canary" not in str(caught.value)


async def test_provider_failure_does_not_expose_error_or_reference(source):
    def fail(_):
        raise OSError("private-canary storage credential")

    with pytest.raises(module.PayloadVerificationError) as caught:
        await module.verify_payloads(*source, fail)
    assert caught.value.reason == "unavailable"
    assert caught.value.__suppress_context__
    assert "private-canary" not in str(caught.value)


@pytest.mark.parametrize(
    "name,row,required",
    [
        ("CONFIG_WORKSPACE_ENTRIES", {"entry_type": "file", "is_deleted": False}, True),
        ("CONFIG_WORKSPACE_FILE_VERSIONS", {}, True),
        ("ML_MODEL_VERSIONS", {"status": "READY"}, True),
        ("ML_MODEL_VERSIONS", {"status": "FAILED"}, False),
    ],
)
async def test_missing_required_payload_reference_cannot_validate(
    tmp_path,
    name,
    row,
    required,
):
    table = load_contract().tables[name]
    chunk = write_chunk(
        tmp_path,
        name + "/000000.jsonl.gz",
        encode_chunk([[row.get(column.name) for column in table.columns]]),
    )
    chunk["rows"] = 1
    export = SimpleNamespace(directory=tmp_path, chunks=lambda _: [(0, chunk)])
    contract = ImportContract("a" * 64, {name: table})

    def unexpected_read(_):
        pytest.fail("No storage access is possible without a reference")

    if required:
        with pytest.raises(module.PayloadVerificationError, match="missing_reference"):
            await module.verify_payloads(export, contract, unexpected_read)
    else:
        assert (await module.verify_payloads(export, contract, unexpected_read))["count"] == 0


def test_model_bytes_are_streamed_without_loading_or_deserialization(monkeypatch):
    from io import BytesIO

    from app.modules.ml_engine.artifacts.store import ObjectArtifactStore

    body = BytesIO(b"not executable model bytes")
    client = SimpleNamespace(
        get_object=lambda **_: {
            "Body": body,
            "ContentLength": 27,
            "ETag": '"receipt"',
        }
    )
    monkeypatch.setattr(ObjectArtifactStore, "_client", lambda _: client)
    monkeypatch.setattr(
        ObjectArtifactStore, "_connection", lambda _: SimpleNamespace(bucket="fixture")
    )
    ref = module.PayloadReference(
        "ML_MODEL_VERSIONS", "a" * 64, "model", "nova-artifact://fixture/model", 26
    )
    # A truncated response is refused even if its bytes have a valid digest.
    with pytest.raises(module.PayloadVerificationError, match="incomplete_read"):
        module.ManagedPayloadReader()(ref)
    assert body.closed
