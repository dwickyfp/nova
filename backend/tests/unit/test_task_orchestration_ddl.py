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

from app.common.nova_system import TASK_ORCHESTRATION_DDL
from app.modules.task_orchestration import repository as repo

CREDENTIAL_SUBSTRINGS = ("password", "secret", "token", "credential")

EXPECTED_TABLES = {
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
