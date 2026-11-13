"""Real PG17 imports preserve rows and resume only committed immutable chunks."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from app.core.metadata_cutover import OperatorRequest, decide_freeze, decide_publish, execute
from app.core.metadata_import_contract import load_contract, load_provenance
from app.core.metadata_import_manifest import (
    EXPORT_FORMAT,
    TableDigest,
    canonical_json,
    chunk_name,
    encode_chunk,
    encode_source_value,
    key_fingerprint,
    load_export_manifest,
    private_directory,
    row_fingerprint,
    sha256_bytes,
    write_chunk,
    write_private,
)
from app.core.metadata_importer import ImportRefused, import_snapshot
from app.core.metadata_migrations import migrate
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401

pytestmark = pytest.mark.metadata_pg
OPERATOR = OperatorRequest("import-test", "Disposable acceptance", "d" * 40)


def snapshot(directory: Path, records: dict, *, chunk_size=1):
    from app.core.metadata_import import import_row_id

    private_directory(directory)
    contract, provenance, identity = load_contract(), load_provenance(), uuid4()
    tables = {}
    for name, table in contract.tables.items():
        entry = {
            "status": "EXPORTED" if table.source_present else "ABSENT",
            "source_present": table.source_present,
            "key_model": table.key_model,
            "target_table": table.target_table,
            "stable": True,
            "chunks": [],
            "rows": 0,
        }
        if table.source_present:
            rows = [
                [
                    encode_source_value(
                        c.source_type, r[c.name] if not c.nullable else r.get(c.name)
                    )
                    for c in table.columns
                ]
                for r in records.get(name, [])
            ]
            digest = TableDigest()
            for offset in range(0, len(rows), chunk_size):
                values = rows[offset : offset + chunk_size]
                chunk = write_chunk(
                    directory, chunk_name(name, offset // chunk_size), encode_chunk(values)
                )
                chunk["rows"] = len(values)
                entry["chunks"].append(chunk)
                for index, row in enumerate(values):
                    key = (
                        [str(import_row_id(identity, name, chunk["sha256"], offset + index))]
                        if table.record_id
                        else [row[i] for i in table.key_indexes()]
                    )
                    digest.add(key_fingerprint(key), row_fingerprint(table.source_types, row))
            entry.update(digest.finish())
            entry["null_counts"] = {
                c.name: sum(r[i] is None for r in rows) for i, c in enumerate(table.columns)
            }
            entry["validation_count"] = len(rows)
        tables[name] = entry
    document = {
        "format": EXPORT_FORMAT,
        "snapshot_id": str(identity),
        "tables": tables,
        "contract_sha256": contract.sha256,
        "timestamp_provenance_sha256": provenance.sha256,
        "clock_binding": {
            "UTC": "UTC",
            "ENGINE_SESSION": "Asia/Jakarta",
            "ENGINE_GLOBAL": "Asia/Jakarta",
        },
        "consistency": "FROZEN_STABLE",
        "unstable_tables": [],
        "uncontracted_tables": [],
        "row_identity": "table-offset/v1",
    }
    path = directory / "manifest.json"
    write_private(path, canonical_json(document))
    return load_export_manifest(path)


@pytest_asyncio.fixture
async def target(environment):  # noqa: F811
    await environment.bootstrap()
    config = environment.role_config("nova_meta_migrator")
    await migrate(config, app_version=OPERATOR.app_version)
    freeze = await execute(config, "freeze", OPERATOR, decide_freeze)
    assert freeze.committed
    return environment, config, freeze.operation_id


def legacy_rows():
    instant = datetime(2026, 10, 1, 13, 20, 30)
    return {
        "AI_SEARCH_QUERY_LOG": [
            {
                "id": "duplicate",
                "index_name": "search",
                "index_version": 1,
                "user_name": "owner",
                "mode": "semantic",
                "elapsed_ms": 1,
                "lexical_candidates": 1,
                "semantic_candidates": 1,
                "result_count": 1,
                "status": "SUCCESS",
                "created_at": instant,
            }
        ]
        * 2,
        "CONFIG_USER_PREFERENCES": [
            {
                "user_name": "owner",
                "pref_key": "studio.theme",
                "pref_value": "dark",
                "updated_at": instant,
            },
            {
                "user_name": "owner",
                "pref_key": "workspace.sidebar_state",
                "pref_value": "exact\\ntext",
                "updated_at": instant,
            },
        ],
        "CONFIG_AI_PROVIDERS": [
            {
                "id": "provider",
                "name": "Synthetic provider",
                "type": "openai",
                "endpoint": "https://example.invalid",
                "api_key": "enc:synthetic-import-ciphertext",
                "is_active": True,
                "created_at": instant,
            }
        ],
        "CONFIG_TASK_ROLE_BINDINGS": [
            {
                "role_name": "ANALYST",
                "execution_user": "task-owner",
                "configured_by": "admin",
                "updated_at": instant,
            }
        ],
        "CONFIG_STAGES": [
            {
                "id": "old-id",
                "name": "old-stage",
                "database_name": "db",
                "schema_name": "PUBLIC",
                "storage_connection": "files",
                "base_prefix": "legacy/path",
                "created_at": instant,
            }
        ],
        "CONFIG_AGENT_RUNS": [
            {
                "run_id": "old-run",
                "owner_name": "owner",
                "agent_id": "__auto__",
                "thread_id": "old-thread",
                "role_name": "ANALYST",
                "status": "completed",
                "last_sequence": 17,
                "started_at": instant,
                "updated_at": instant,
                "parent_run_id": "parent",
                "root_run_id": "root",
                "depth": 2,
                "objective": "Historical objective",
                "payload": '{"exact":123456789012345678901234567890}',
                "checkpoint": '{"position":3}',
                "result_summary": "Historical result",
                "prompt_tokens": 42,
                "completion_tokens": 17,
                "lease_owner": "old-worker",
                "generation": 4,
                "error_class": "OldFailure",
            }
        ],
        "STAGE_FILE_MANIFEST": [
            {
                "file_id": 42,
                "stage_id": "old-id",
                "file_path": "a.csv",
                "file_name": "a.csv",
                "uploaded_at": None,
            },
        ],
    }


async def test_interrupted_import_resumes_exact_chunks_and_preserves_provenance(target, tmp_path):
    env, config, freeze = target
    records = legacy_rows()
    del records["STAGE_FILE_MANIFEST"]  # No reviewed writer for this reserved store.
    export = snapshot(tmp_path / "export", records)
    validation = tmp_path / "validated.json"

    def crash(_name, _index):
        raise ConnectionError("Simulated process exit after committed chunk")

    with pytest.raises(ConnectionError):
        await import_snapshot(
            config,
            export,
            load_contract(),
            load_provenance(),
            freeze_id=freeze,
            validation_path=validation,
            after_chunk=crash,
        )
    assert not validation.exists()
    cursor = await env.admin.execute("SELECT count(*) AS n FROM nova_meta.metadata_import_chunks")
    assert (await cursor.fetchone())["n"] == 1
    report = await import_snapshot(
        config,
        export,
        load_contract(),
        load_provenance(),
        freeze_id=freeze,
        validation_path=validation,
    )
    assert report["status"] == "VALIDATED" and len(report["tables"]) == len(load_contract().tables)
    cursor = await env.admin.execute("SELECT id,record_id FROM nova_meta.ai_search_query_log")
    duplicates = await cursor.fetchall()
    assert [r["id"] for r in duplicates] == ["duplicate", "duplicate"]
    assert len({r["record_id"] for r in duplicates}) == 2
    cursor = await env.admin.execute("SELECT updated_at FROM nova_meta.config_user_preferences")
    assert all(
        r["updated_at"] == datetime(2026, 10, 1, 6, 20, 30, tzinfo=UTC)
        for r in await cursor.fetchall()
    )
    cursor = await env.admin.execute(
        "SELECT started_at,payload::text,provenance,root_run_id FROM nova_meta.config_agent_runs"
    )
    run = await cursor.fetchone()
    assert run["started_at"] == datetime(2026, 10, 1, 13, 20, 30, tzinfo=UTC)
    assert "123456789012345678901234567890" in run["payload"]
    assert run["provenance"] == "imported" and run["root_run_id"] == "root"
    cursor = await env.admin.execute(
        "SELECT ciphertext FROM nova_meta.config_ai_provider_credentials"
    )
    assert (await cursor.fetchone())["ciphertext"] == "enc:synthetic-import-ciphertext"
    assert "synthetic-import-ciphertext" not in validation.read_text()
    data = validation.read_bytes()
    # A lost validation file can be reconstructed from the immutable PG receipt.
    validation.unlink()
    recovered = await import_snapshot(
        config,
        export,
        load_contract(),
        load_provenance(),
        freeze_id=freeze,
        validation_path=validation,
    )
    assert recovered == report and validation.read_bytes() == data
    import json

    forged = json.loads(data)
    forged["validated_at"] = datetime.now(UTC).isoformat()
    forged_bytes = canonical_json(forged)
    refused = await execute(
        config,
        "publish_epoch",
        OPERATOR,
        lambda state: decide_publish(state, forged_bytes, sha256_bytes(forged_bytes)),
    )
    assert not refused.committed and refused.refusal == "import_receipt_missing"
    published = await execute(
        config,
        "publish_epoch",
        OPERATOR,
        lambda state: decide_publish(state, data, sha256_bytes(data)),
    )
    assert published.committed, published.refusal
    with pytest.raises(ImportRefused, match="freeze_or_epoch"):
        await import_snapshot(
            config,
            export,
            load_contract(),
            load_provenance(),
            freeze_id=freeze,
            validation_path=validation,
        )


async def test_unproven_timestamp_blocks_before_any_rows_or_receipts(target, tmp_path):
    env, config, freeze = target
    records = legacy_rows()
    records["STAGE_FILE_MANIFEST"][0]["uploaded_at"] = datetime(2026, 10, 1)
    export = snapshot(tmp_path / "export", records)
    with pytest.raises(ImportRefused) as refused:
        await import_snapshot(
            config,
            export,
            load_contract(),
            load_provenance(),
            freeze_id=freeze,
            validation_path=tmp_path / "validated.json",
        )
    assert refused.value.reason == "timestamp_provenance"
    cursor = await env.admin.execute("SELECT count(*) AS n FROM nova_meta.metadata_import_runs")
    assert (await cursor.fetchone())["n"] == 0


async def test_changed_chunk_cannot_be_resumed_as_a_verified_import(target, tmp_path):
    env, config, freeze = target
    records = legacy_rows()
    del records["STAGE_FILE_MANIFEST"]
    export = snapshot(tmp_path / "export", records)
    entry = export.tables["CONFIG_USER_PREFERENCES"]["chunks"][0]
    (export.directory / entry["file"]).write_bytes(b"altered")
    from app.core.metadata_import_manifest import ManifestError

    with pytest.raises(ManifestError, match="chunk_altered"):
        await import_snapshot(
            config,
            export,
            load_contract(),
            load_provenance(),
            freeze_id=freeze,
            validation_path=tmp_path / "validated.json",
        )
    cursor = await env.admin.execute("SELECT count(*) AS n FROM nova_meta.metadata_import_runs")
    assert (await cursor.fetchone())["n"] == 0
