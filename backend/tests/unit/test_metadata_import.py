import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from psycopg.types.json import Jsonb

from app.core.metadata_import import (
    ImportColumn,
    ImportContractError,
    ImportValueError,
    import_row_id,
    target_type,
)


@pytest.mark.parametrize(("source", "target"), [
    ("varchar(64)", "varchar(64)"), ("varchar(65533)", "text"),
    ("int(11)", "integer"), ("bigint(20)", "bigint"), ("tinyint(1)", "boolean"),
    ("double", "double precision"), ("json", "jsonb"), ("datetime", "timestamptz"),
])
def test_explicit_supported_source_types(source, target):
    assert target_type(source) == target


def test_provider_ciphertext_storage_has_an_explicit_wider_target():
    assert target_type(
        "varchar(512)", source_table="CONFIG_AI_PROVIDERS", source_column="api_key",
    ) == "text"
    assert target_type(
        "varchar(512)", source_table="CONFIG_AI_PROVIDERS", source_column="endpoint",
    ) == "varchar(512)"
    with pytest.raises(ImportContractError):
        target_type("varchar(1024)", source_table="CONFIG_AI_PROVIDERS", source_column="api_key")


@pytest.mark.parametrize("context", [
    {"source_table": "CONFIG_AI_PROVIDERS"}, {"source_column": "api_key"},
    {"source_table": "invalid;sql", "source_column": "api_key"},
])
def test_incomplete_or_invalid_column_contract_is_refused(context):
    with pytest.raises(ImportContractError):
        target_type("varchar(512)", **context)


@pytest.mark.parametrize("source", [
    "varchar(0)", "varchar(-1)", "varchar(999999999999)", "unknown", "date",
    "varchar(64);drop table audit_log", "INT(11)", None, {},
])
def test_unknown_or_executable_type_is_refused(source):
    with pytest.raises(ImportContractError):
        target_type(source)


@pytest.mark.parametrize(("source", "value", "expected"), [
    ("int(11)", -(2 ** 31), -(2 ** 31)), ("int(11)", 2 ** 31 - 1, 2 ** 31 - 1),
    ("bigint(20)", -(2 ** 63), -(2 ** 63)), ("bigint(20)", 2 ** 63 - 1, 2 ** 63 - 1),
    ("tinyint(1)", 0, False), ("tinyint(1)", 1, True), ("tinyint(1)", True, True),
    ("varchar(64)", "日本語 café \U0001f680", "日本語 café \U0001f680"),
    ("double", 1.2345678901234567, 1.2345678901234567),
])
def test_lossless_scalar_values(source, value, expected):
    actual = ImportColumn("value", source, False).convert(value)
    assert actual == expected and type(actual) is type(expected)


@pytest.mark.parametrize(("source", "value"), [
    ("int(11)", 2 ** 31), ("int(11)", -(2 ** 31) - 1), ("int(11)", True),
    ("int(11)", "123"), ("bigint(20)", 2 ** 63), ("bigint(20)", -(2 ** 63) - 1),
    ("tinyint(1)", 2), ("tinyint(1)", -1), ("tinyint(1)", "false"),
    ("tinyint(1)", 0.0), ("double", float("nan")), ("double", float("inf")),
    ("double", 1), ("varchar(4)", "longer"), ("varchar(64)", b"binary-canary"),
    ("varchar(64)", "secret-canary\x00"), ("varchar(64)", "secret-canary\ud800"),
    ("bigint(20)", None),
])
def test_invalid_values_do_not_coerce_truncate_or_expose_payload(source, value):
    with pytest.raises(ImportValueError) as caught:
        ImportColumn("value", source, False).convert(value)
    assert "canary" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("raw", [
    '{"n":9007199254740993.1234567890123456789,"a":[true,null,"日本語"]}',
    "null", "true", "123456789012345678901234567890", '"hello"',
])
def test_json_keeps_exact_input_for_psycopg_without_float_conversion(raw):
    converted = ImportColumn("payload", "json", True).convert(raw)
    assert isinstance(converted, Jsonb)
    assert converted.dumps(converted.obj) == raw
    assert ImportColumn("payload", "json", True).convert(None) is None


@pytest.mark.parametrize("raw", [
    '{"canary":1,"canary":2}', '{"a":{"same":1,"same":2}}',
    "NaN", "Infinity", "-Infinity", '{"secret-canary":NaN}',
    '"secret-canary\\u0000"', '{"\\u0000":"secret-canary"}',
    '"secret-canary\\ud800"', "{'secret-canary':1}", "", {"already_decoded": 1.1},
    "[" * 1200 + "0" + "]" * 1200,
])
def test_json_refuses_silent_value_loss_and_unsafe_decoder_errors(raw):
    with pytest.raises(ImportValueError) as caught:
        ImportColumn("payload", "json", True).convert(raw)
    assert "secret-canary" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(("zone", "local", "expected"), [
    ("UTC", datetime(2026, 11, 11, 10, 15, 20, 123456),
     datetime(2026, 11, 11, 10, 15, 20, 123456, tzinfo=UTC)),
    ("Asia/Jakarta", datetime(2026, 11, 11, 10, 15), datetime(2026, 11, 11, 3, 15, tzinfo=UTC)),
    ("America/New_York", datetime(2026, 1, 1, 12), datetime(2026, 1, 1, 17, tzinfo=UTC)),
])
def test_timestamp_conversion_requires_explicit_writer_provenance(zone, local, expected):
    assert ImportColumn("created_at", "datetime", False, zone).convert(local) == expected


@pytest.mark.parametrize(("zone", "local"), [
    (None, datetime(2026, 11, 11)),
    ("UTC", datetime(2026, 11, 11, tzinfo=UTC)),
    ("UTC", "2026-11-11T10:15:00"),
    ("America/New_York", datetime(2026, 3, 8, 2, 30)),
    ("America/New_York", datetime(2026, 11, 1, 1, 30)),
    ("Australia/Lord_Howe", datetime(2026, 4, 5, 1, 45)),
])
def test_unproven_ambiguous_or_nonexistent_timestamps_are_refused(zone, local):
    with pytest.raises(ImportValueError):
        ImportColumn("created_at", "datetime", False, zone).convert(local)


@pytest.mark.parametrize("kwargs", [
    {"name": "unsafe.name"}, {"nullable": 1}, {"timestamp_zone": "/etc/passwd"},
    {"timestamp_zone": "missing-zone"}, {"source_type": "varchar(64)", "timestamp_zone": "UTC"},
])
def test_invalid_column_contract_is_refused(kwargs):
    fields = {"name": "created_at", "source_type": "datetime", "nullable": False, **kwargs}
    with pytest.raises(ImportContractError):
        ImportColumn(**fields)


def test_import_identity_retains_duplicate_occurrences_and_resumes_the_same_export():
    snapshot = uuid4()
    first = import_row_id(snapshot, "AUDIT_LOG", "a" * 64, 0)
    second = import_row_id(snapshot, "AUDIT_LOG", "a" * 64, 1)
    assert first != second
    assert first == import_row_id(snapshot, "AUDIT_LOG", "a" * 64, 0)
    variants = {
        import_row_id(uuid4(), "AUDIT_LOG", "a" * 64, 0),
        import_row_id(snapshot, "USAGE_QUERY_STATS", "a" * 64, 0),
        import_row_id(snapshot, "AUDIT_LOG", "b" * 64, 0),
    }
    assert first not in variants and len(variants) == 3


@pytest.mark.parametrize("change", [
    {"snapshot_id": UUID(int=0)}, {"snapshot_id": "not-a-uuid"},
    {"source_table": "AUDIT_LOG; DROP TABLE x"}, {"source_table": "audit_log"},
    {"chunk_sha256": "unknown"}, {"row_index": -1}, {"row_index": True},
    {"row_index": 2 ** 63},
])
def test_unverified_export_addresses_cannot_create_row_identity(change):
    fields = {"snapshot_id": uuid4(), "source_table": "AUDIT_LOG",
              "chunk_sha256": "a" * 64, "row_index": 0, **change}
    with pytest.raises(ImportContractError):
        import_row_id(**fields)


def test_column_mapping_covers_the_entire_observed_schema_without_assuming_timezones():
    base = Path(__file__).resolve().parents[3] / "docs/architecture/production-refactor"
    inventory = json.loads((base / "inventory.json").read_text())
    with (base / "COLUMN_TRANSFORMS.csv").open() as source:
        columns = list(csv.DictReader(source))
    observed = {
        (table, column["name"]): (column, definition["ddl_sha256"])
        for table, definition in inventory["live"]["tables"].items()
        for column in definition["columns"]
    }
    assert len(columns) == len(observed)
    assert {(c["source_table"], c["source_column"]) for c in columns} == set(observed)
    for contract in columns:
        column, digest = observed[contract["source_table"], contract["source_column"]]
        assert contract["source_type"] == column["type"]
        assert contract["source_nullable"] == column["nullable"]
        assert int(contract["source_ordinal"]) == column["ordinal"]
        assert contract["source_ddl_sha256"] == digest
        assert contract["target_pg_type"] == target_type(
            column["type"], source_table=contract["source_table"],
            source_column=contract["source_column"],
        )
        if column["type"] == "datetime":
            assert contract["timestamp_zone"] == "REVIEW_REQUIRED"
            assert contract["status"] == "TIMESTAMP_PROVENANCE_REQUIRED"
        else:
            assert contract["timestamp_zone"] == "NOT_APPLICABLE"
