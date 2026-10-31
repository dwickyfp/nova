"""Superset metadata travels with NOVA_SYSTEM snapshots (docs/arch-17-superset.md, Phase 5)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.backup import bi_metadata
from app.modules.backup.schemas import SnapshotCreate, SnapshotRestore
from app.modules.backup.service import BackupService

CALLER = {"username": "admin", "encrypted_password": "x", "session_id": "s1"}


class Store:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def uri_for_key(self, key: str) -> str:
        return f"nova-artifact://production/{key}"

    def put(self, key: str, payload: bytes) -> str:
        self.objects[self.uri_for_key(key)] = payload
        return self.uri_for_key(key)

    def exists(self, uri: str) -> bool:
        return uri in self.objects

    def get(self, uri: str) -> bytes:
        return self.objects[uri]


@pytest.fixture
def pg(monkeypatch):
    store = Store()
    calls: list[tuple[list[str], bytes | None, dict]] = []

    async def run(arguments, stdin=None):
        calls.append((arguments, stdin, bi_metadata._client_env()))
        return b"PGDMP-archive" if arguments[0] == "pg_dump" else b""

    monkeypatch.setattr(bi_metadata, "_store", lambda: store)
    monkeypatch.setattr(bi_metadata, "_run", run)
    monkeypatch.setattr(bi_metadata.settings, "SUPERSET_DB_PASSWORD", "pg-secret")
    return store, calls


@pytest.mark.asyncio
async def test_a_dump_is_stored_under_the_snapshot_and_restored_from_it(pg) -> None:
    store, calls = pg

    saved = await bi_metadata.dump("nightly", "daily_2026")
    restored = await bi_metadata.restore("nightly", "daily_2026")

    assert saved.status == "saved" and saved.size_bytes == len(b"PGDMP-archive")
    assert list(store.objects) == ["nova-artifact://production/backups/bi/nightly/daily_2026.dump"]
    assert restored.status == "restored" and "Restart the Superset services" in restored.message
    dump_args, _, env = calls[0]
    restore_args, stdin, _ = calls[1]
    assert dump_args[:2] == ["pg_dump", "--format=custom"]
    assert "--single-transaction" in restore_args and "--clean" in restore_args
    assert stdin == b"PGDMP-archive"
    # The password reaches the client through its environment only.
    assert env["PGPASSWORD"] == "pg-secret" and env["PGHOST"] == "superset-db"
    assert not any("pg-secret" in argument for argument in dump_args + restore_args)


@pytest.mark.asyncio
async def test_a_snapshot_without_bi_metadata_restores_nothing(pg) -> None:
    _, calls = pg
    result = await bi_metadata.restore("nightly", "older")
    assert result.status == "absent" and calls == []


@pytest.mark.asyncio
async def test_failures_are_reported_without_the_password(monkeypatch) -> None:
    monkeypatch.setattr(bi_metadata.settings, "SUPERSET_DB_PASSWORD", "")
    result = await bi_metadata.dump("nightly", "l1")
    assert result.status == "failed" and "not configured" in result.message

    monkeypatch.setattr(bi_metadata.settings, "SUPERSET_DB_PASSWORD", "pg-secret")
    failing = AsyncMock(
        side_effect=bi_metadata.BiMetadataError("pg_dump failed: connection refused")
    )
    monkeypatch.setattr(bi_metadata, "_run", failing)
    result = await bi_metadata.dump("nightly", "l1")
    assert result.as_dict() == {
        "status": "failed",
        "message": "pg_dump failed: connection refused",
        "size_bytes": None,
    }


@pytest.mark.asyncio
async def test_only_full_nova_metadata_snapshots_carry_bi_metadata(monkeypatch) -> None:
    service = BackupService()
    monkeypatch.setattr(service, "_run", AsyncMock())
    audit = AsyncMock()
    monkeypatch.setattr("app.modules.backup.service.write_audit_log", audit)
    outcome = bi_metadata.BiMetadataResult("saved", "ok", 10)
    dump = AsyncMock(return_value=outcome)
    restore = AsyncMock(return_value=bi_metadata.BiMetadataResult("restored", "ok", 10))
    monkeypatch.setattr(bi_metadata, "dump", dump)
    monkeypatch.setattr(bi_metadata, "restore", restore)

    created = await service.create_snapshot(
        SnapshotCreate(database="NOVA_SYSTEM", label="l1", repository="nightly"), **CALLER
    )
    other = await service.create_snapshot(
        SnapshotCreate(database="sales", label="l1", repository="nightly"), **CALLER
    )
    restored = await service.restore_snapshot(
        SnapshotRestore(database="NOVA_SYSTEM", label="l1", repository="nightly"), **CALLER
    )
    partial = await service.restore_snapshot(
        SnapshotRestore(
            database="NOVA_SYSTEM", label="l1", repository="nightly", tables=["AUDIT_LOG"]
        ),
        **CALLER,
    )

    assert created["bi_metadata"]["status"] == "saved" and "bi_metadata" not in other
    assert restored["bi_metadata"]["status"] == "restored" and "bi_metadata" not in partial
    dump.assert_awaited_once_with("nightly", "l1")
    restore.assert_awaited_once_with("nightly", "l1")
    actions = [call.kwargs["action"] for call in audit.await_args_list]
    assert actions == ["BACKUP_BI_METADATA", "RESTORE_BI_METADATA"]
