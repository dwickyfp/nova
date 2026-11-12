"""Epoch activation requires the complete frozen import, never a partial success list."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.metadata_cutover import DeploymentState, decide_publish
from app.core.metadata_import_contract import ImportContract, SourceColumn, TableContract
from app.core.metadata_import_manifest import (
    VALIDATION_FORMAT,
    canonical_json,
    migration_ledger_sha256,
    sha256_bytes,
    write_private,
)
from app.core.metadata_import_semantics import CHECKS
from app.core.metadata_schema import load_migrations


@pytest.fixture
def manifest():
    migrations = load_migrations()
    freeze = uuid4()
    timestamp = datetime.now(UTC)
    state = DeploymentState(len(migrations), None, True, freeze, timestamp)
    tables = {
        name: TableContract(
            name, "fixture", "PRIMARY", True, ("id",),
            (SourceColumn("id", "varchar(64)", False, "id"),), name.lower(), False, None,
        ) for name in ("CONFIG_A", "CONFIG_B")
    }
    contract = ImportContract("a" * 64, tables)
    document = {
        "format": VALIDATION_FORMAT, "status": "VALIDATED", "schema_version": len(migrations),
        "migration_ledger_sha256": migration_ledger_sha256(
            (item.version, item.name, item.checksum) for item in migrations
        ),
        "contract_sha256": contract.sha256, "freeze_operation_id": str(freeze),
        "validated_at": timestamp.isoformat(), "conflicts": {"blocking": 0},
        "source_consistency": "FROZEN_STABLE", "snapshot_id": str(uuid4()),
        "import_run_id": str(uuid4()), "export_manifest_sha256": "b" * 64,
        "semantics": {"status": "VERIFIED", "checks": dict.fromkeys(CHECKS, 0)},
        "managed_payloads": {"status": "VERIFIED", "count": 0, "receipts": [],
                             "receipts_sha256": sha256_bytes(canonical_json([]))},
        "tables": {name: {
            "status": "MATCHED", "target_table": table.target_table,
            "source_count": 2, "target_count": 2,
            "source_key_digest": "c" * 64, "target_key_digest": "c" * 64,
            "source_row_digest": "d" * 64, "target_row_digest": "d" * 64,
        } for name, table in tables.items()},
    }
    return state, contract, document


def decide(manifest, document=None, digest=None):
    state, contract, original = manifest
    encoded = canonical_json(document if document is not None else original)
    return decide_publish(state, encoded, digest or sha256_bytes(encoded), contract=contract)


def test_complete_manifest_publishes_one_epoch_without_opening_writes(manifest):
    result = decide(manifest)
    assert result.refusal is None and result.epoch_after is not None
    assert result.maintenance_after is True
    assert result.freeze_operation_id == manifest[0].active_freeze_id
    assert result.manifest_sha256 == sha256_bytes(canonical_json(manifest[2]))


@pytest.mark.parametrize(("field", "value"), [
    ("contract_sha256", "f" * 64), ("migration_ledger_sha256", "f" * 64),
    ("freeze_operation_id", str(uuid4())), ("source_consistency", "LIVE"),
    ("conflicts", {"blocking": 1}), ("conflicts", {"blocking": False}),
    ("schema_version", True), ("status", "PARTIAL"), ("snapshot_id", "not-a-uuid"),
    ("managed_payloads", None),
    ("managed_payloads", {"status": "VERIFIED", "count": 0, "receipts": [],
                          "receipts_sha256": "f" * 64}),
])
def test_wrong_provenance_never_activates(manifest, field, value):
    document = deepcopy(manifest[2])
    document[field] = value
    result = decide(manifest, document)
    assert result.refusal and result.epoch_after is None and result.maintenance_after


@pytest.mark.parametrize("fault", ["missing", "unexpected", "count", "boolean_count",
                                   "key", "digest", "target", "absent", "unverified"])
def test_every_inventory_entry_must_match(manifest, fault):
    document = deepcopy(manifest[2])
    entry = document["tables"]["CONFIG_A"]
    if fault == "missing":
        del document["tables"]["CONFIG_B"]
    elif fault == "unexpected":
        document["tables"]["CONFIG_X"] = deepcopy(entry)
    else:
        field, value = {
            "count": ("target_count", 1), "boolean_count": ("source_count", True),
            "key": ("target_key_digest", "e" * 64), "digest": ("source_row_digest", ""),
            "target": ("target_table", "other"), "absent": ("status", "ABSENT"),
            "unverified": ("status", "PENDING"),
        }[fault]
        entry[field] = value
    result = decide(manifest, document)
    assert result.refusal and result.epoch_after is None


def test_manifest_must_postdate_the_actual_freeze(manifest):
    document = deepcopy(manifest[2])
    document["validated_at"] = (manifest[0].active_freeze_at - timedelta(seconds=1)).isoformat()
    assert decide(manifest, document).refusal == "manifest_predates_freeze"
    document["validated_at"] = "2026-11-12T12:00:00"
    assert decide(manifest, document).refusal == "manifest_predates_freeze"


def test_changed_bytes_and_duplicate_json_keys_are_refused(manifest):
    assert decide(manifest, digest="f" * 64).refusal == "manifest_digest_mismatch"
    state, contract, document = manifest
    data = canonical_json(document)
    duplicate = b'{"status":"UNVERIFIED",' + data[1:]
    result = decide_publish(state, duplicate, sha256_bytes(duplicate), contract=contract)
    assert result.refusal == "manifest_canonical"


def test_missing_packaged_contract_is_a_release_blocker(manifest, monkeypatch):
    from app.core import metadata_cutover
    from app.core.metadata_import import ImportContractError

    def missing():
        raise ImportContractError()

    monkeypatch.setattr(metadata_cutover, "load_contract", missing)
    data = canonical_json(manifest[2])
    result = decide_publish(manifest[0], data, sha256_bytes(data))
    assert result.refusal == "import_contract_unavailable"


def test_private_artifact_write_cannot_follow_a_predicted_temporary_symlink(tmp_path):
    victim = tmp_path / "unrelated"
    victim.write_text("keep")
    destination = tmp_path / "manifest.json"
    destination.with_suffix(".json.tmp").symlink_to(victim)
    write_private(destination, b"private-export")
    assert victim.read_text() == "keep"
    assert destination.read_bytes() == b"private-export"
    assert destination.stat().st_mode & 0o077 == 0
