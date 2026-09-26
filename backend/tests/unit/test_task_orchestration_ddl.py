"""Always-on guardrails for the Phase 9 CONFIG_TASK* DDL.

The executable proof lives in ``tests/integration/test_task_orchestration_metadata.py``.
These tests run without a database and pin the properties that must hold in the
DDL text itself: idempotent statements, Primary-Key layout, and the absence of any
credential-bearing column name.
"""

from __future__ import annotations

import inspect
import json
import re

import pytest
from asyncmy.errors import ProgrammingError

from app.common import nova_system
from app.common.nova_system import (
    TASK_ORCHESTRATION_COLUMN_MIGRATIONS,
    TASK_ORCHESTRATION_DDL,
)
from app.modules.task_orchestration import repository as repo

CREDENTIAL_SUBSTRINGS = ("password", "secret", "token", "credential")

EXPECTED_TABLES = {
    "CONFIG_TASK_ROLE_BINDINGS",
    "CONFIG_TASKS",
    "CONFIG_TASK_EDGES",
    "CONFIG_TASK_GRAPH_RUNS",
    "CONFIG_TASK_RUNS",
}


def _declared_tables() -> set[str]:
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS NOVA_SYSTEM\.(\w+)", "\n".join(
        TASK_ORCHESTRATION_DDL
    )))


class TestDdlStatements:
    def test_declares_exactly_the_four_tables(self):
        assert _declared_tables() == EXPECTED_TABLES

    def test_every_statement_is_create_table_if_not_exists(self):
        for ddl in TASK_ORCHESTRATION_DDL:
            assert ddl.strip().upper().startswith(
                "CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_TASK"
            ), ddl[:80]

    def test_every_table_is_a_primary_key_table(self):
        for ddl in TASK_ORCHESTRATION_DDL:
            assert "PRIMARY KEY(" in ddl.upper()
            assert "DUPLICATE KEY" not in ddl.upper()

    def test_every_table_uses_buckets_one_and_persistent_index(self):
        for ddl in TASK_ORCHESTRATION_DDL:
            assert 'DISTRIBUTED BY HASH(' in ddl
            assert "BUCKETS 1" in ddl
            assert '"enable_persistent_index"="true"' in ddl


class TestConcurrentColumnMigration:
    async def test_accepts_column_added_between_check_and_alter(self, monkeypatch):
        class RacingDatabase:
            def __init__(self) -> None:
                self.metadata_reads = 0

            async def execute_system(self, sql, params=None):
                if sql.startswith("SELECT COLUMN_NAME"):
                    self.metadata_reads += 1
                    rows = [] if self.metadata_reads == 1 else [["heartbeat_at"]]
                    return {"columns": ["COLUMN_NAME"], "rows": rows, "row_count": len(rows)}
                raise ProgrammingError(
                    1064,
                    "Can not add column which already exists in base table: heartbeat_at",
                )

        racing_db = RacingDatabase()
        monkeypatch.setattr(nova_system, "db", racing_db)
        monkeypatch.setattr(
            nova_system,
            "TASK_ORCHESTRATION_COLUMN_MIGRATIONS",
            (("CONFIG_TASK_RUNS", "heartbeat_at", "DATETIME"),),
        )

        await nova_system.migrate_task_orchestration_columns()

        assert racing_db.metadata_reads == 2

    async def test_reraises_when_the_column_is_still_absent(self, monkeypatch):
        class BrokenDatabase:
            async def execute_system(self, sql, params=None):
                if sql.startswith("SELECT COLUMN_NAME"):
                    return {"columns": ["COLUMN_NAME"], "rows": [], "row_count": 0}
                raise ProgrammingError(1064, "syntax error")

        monkeypatch.setattr(nova_system, "db", BrokenDatabase())
        monkeypatch.setattr(
            nova_system,
            "TASK_ORCHESTRATION_COLUMN_MIGRATIONS",
            (("CONFIG_TASK_RUNS", "heartbeat_at", "DATETIME"),),
        )

        with pytest.raises(ProgrammingError, match="syntax error"):
            await nova_system.migrate_task_orchestration_columns()


class TestNoCredentialColumnsInDdl:
    def test_no_credential_bearing_column_name(self):
        text = "\n".join(TASK_ORCHESTRATION_DDL).lower()
        offenders = [bad for bad in CREDENTIAL_SUBSTRINGS if bad in text]
        assert offenders == [], f"credential-bearing names in DDL: {offenders}"


class TestRepositoryNeverSelectsCredentialColumns:
    def test_repository_targets_only_the_four_tables(self):
        source = inspect.getsource(repo)
        tables = set(re.findall(r"NOVA_SYSTEM\.CONFIG_TASK\w*", source))
        assert tables <= {
            "NOVA_SYSTEM.CONFIG_TASK_ROLE_BINDINGS",
            "NOVA_SYSTEM.CONFIG_TASKS",
            "NOVA_SYSTEM.CONFIG_TASK_EDGES",
            "NOVA_SYSTEM.CONFIG_TASK_GRAPH_RUNS",
            "NOVA_SYSTEM.CONFIG_TASK_RUNS",
        }

    def test_wal_marks_encoder_rejects_nothing_and_emits_valid_json(self):
        encoded = repo.TaskOrchestrationRepository._encode_wal_marks({"p1": 1, "p2": 2})
        assert json.loads(encoded) == {"p1": 1, "p2": 2}

    def test_wal_marks_decoder_tolerates_corrupt_values(self):
        decode = repo.TaskOrchestrationRepository._decode_wal_marks
        assert decode("not json") is None
        assert decode("[1,2]") is None
        assert decode(None) is None
        assert decode('{"p1": 3}') == {"p1": 3}


class TestUpdateColumnWhitelist:
    """The SET clause interpolates keys, so keys must be validated before writing."""

    def test_accepts_whitelisted_columns(self):
        clause, values = repo._assignments("task", {"name": "x", "timezone": "UTC"})
        assert clause == "name = %s, timezone = %s"
        assert values == ["x", "UTC"]

    def test_rejects_unknown_column(self):
        with pytest.raises(repo.UnknownUpdateColumnError):
            repo._assignments("task", {"name": "x", "password": "leak"})

    def test_rejects_injection_shaped_key(self):
        with pytest.raises(repo.UnknownUpdateColumnError):
            repo._assignments("task", {"version = 0 WHERE 1=1 --": "boom"})

    def test_rejects_empty_payload(self):
        with pytest.raises(ValueError):
            repo._assignments("edge", {})

    def test_every_entity_whitelist_is_non_empty(self):
        for entity, allowed in repo._UPDATABLE_COLUMNS.items():
            assert allowed, entity


class TestEdgeKindColumn:
    """NOVA-54 / 9b: an edge must be able to record a ``FINALIZE`` semantic.

    The schema has to distinguish a normal dependency from a finalizer, or the
    lowering would have to drop the flag and a finalizer would be
    indistinguishable from an ordinary ``AFTER``.
    """

    def test_edges_table_declares_edge_kind(self):
        edges_ddl = next(
            ddl for ddl in TASK_ORCHESTRATION_DDL if "CONFIG_TASK_EDGES" in ddl
        )
        assert "edge_kind" in edges_ddl

    def test_edge_kind_has_a_default_so_existing_rows_are_after(self):
        edges_ddl = next(
            ddl for ddl in TASK_ORCHESTRATION_DDL if "CONFIG_TASK_EDGES" in ddl
        )
        assert "DEFAULT 'after'" in edges_ddl

    def test_edge_kind_is_migrated_for_existing_tables(self):
        migrations = dict(
            ((table, column), column_type)
            for table, column, column_type in TASK_ORCHESTRATION_COLUMN_MIGRATIONS
        )
        assert ("CONFIG_TASK_EDGES", "edge_kind") in migrations
