"""Tests for audit rows on statements that never reach the engine — NOVA-27.

Two failure paths return or raise *before* ``QueryRepository`` is called: the
guard refusing a statement, and the ``@stage`` translation failing. The
success/exception audit pair in ``QueryService.execute`` wraps the repository
call, so neither path reached it and neither left a row.

The asymmetry that made this worth fixing was not the missing row on its own:
engine failures were recorded as ``status=ERROR``, so an operator reading
``AUDIT_LOG`` reasonably concluded that no ERROR rows meant no failed attempts.
A refused ``DROP ROLE ACCOUNTADMIN`` is the most security-relevant statement a
client can attempt through this pipeline, and it left no trace at all.

No engine is required — ``write_audit_log`` is replaced with a recorder, which
is also what proves the row is written rather than merely that the code runs.
"""

import pytest

from app.core.exceptions import ForbiddenSQLError
from app.modules.query.repository import QueryResult
from app.modules.query.service import QueryService


class AuditSink:
    """Captures every ``write_audit_log`` call instead of writing to StarRocks."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    async def __call__(self, **kwargs):
        self.entries.append(kwargs)
        return "query-id"

    @property
    def statuses(self) -> list[str]:
        return [entry["status"] for entry in self.entries]


class RecordingRepo:
    """Records the SQL the engine would receive, then succeeds."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_as_user(self, sql, **kwargs):
        self.calls.append(sql)
        return QueryResult(executed_sql=sql, columns=["v"], rows=[[1]], row_count=1)


@pytest.fixture
def wired(monkeypatch):
    """A service with a recording repo and audit sink, and no engine."""

    sink = AuditSink()
    repo = RecordingRepo()
    service = QueryService()
    service._repo = repo
    monkeypatch.setattr("app.modules.query.service.write_audit_log", sink)
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "pw")
    return service, repo, sink


class TestGuardRejectionIsAudited:
    """A refused statement is an event, not just an exception."""

    @pytest.mark.parametrize(
        ("sql", "expected_reason"),
        [
            ("DROP ROLE ACCOUNTADMIN", "ACCOUNTADMIN role cannot be dropped"),
            ("DROP ROLE IF EXISTS ACCOUNTADMIN", "ACCOUNTADMIN role cannot be dropped"),
            (
                "REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN",
                "Cannot revoke privileges from ACCOUNTADMIN",
            ),
            ("DROP USER root", "root user cannot be dropped"),
        ],
    )
    async def test_rejection_writes_one_error_row(self, wired, sql, expected_reason):
        service, repo, sink = wired

        with pytest.raises(ForbiddenSQLError):
            await service.execute(
                sql=sql,
                username="analyst",
                encrypted_password="enc",
                database="NOVA_DEMO",
            )

        assert sink.statuses == ["ERROR"]
        entry = sink.entries[0]
        assert entry["sql_text"] == sql
        assert entry["error_message"] == expected_reason
        assert entry["user_name"] == "analyst"
        assert entry["database_name"] == "NOVA_DEMO"
        assert repo.calls == [], "a refused statement must not reach the engine"

    async def test_refused_statement_does_not_claim_a_rewrite(self, wired):
        """``rewritten_sql`` stays empty: nothing ran, so nothing was rewritten."""
        service, _, sink = wired

        with pytest.raises(ForbiddenSQLError):
            await service.execute(
                sql="DROP ROLE ACCOUNTADMIN",
                username="analyst",
                encrypted_password="enc",
            )

        assert sink.entries[0]["rewritten_sql"] is None

    async def test_destructive_without_confirmation_is_audited(self, wired):
        """The confirmation gate is a refusal too, not only the role guard."""
        service, _, sink = wired

        with pytest.raises(ForbiddenSQLError):
            await service.execute(
                sql="DROP TABLE NOVA_DEMO.customers",
                username="analyst",
                encrypted_password="enc",
            )

        assert sink.statuses == ["ERROR"]
        assert "confirmation" in sink.entries[0]["error_message"].lower()

    async def test_audit_failure_does_not_replace_the_refusal(self, wired, monkeypatch):
        """A broken audit sink must not change what the client sees.

        The row is written on the failure path, so an exception from the sink
        would otherwise turn a precise "ACCOUNTADMIN role cannot be dropped"
        into an unrelated pool error — the client would lose the reason its
        statement was rejected.
        """
        service, _, _ = wired

        async def exploding_sink(**kwargs):
            raise RuntimeError("audit sink is down")

        monkeypatch.setattr("app.modules.query.service.write_audit_log", exploding_sink)

        with pytest.raises(ForbiddenSQLError) as excinfo:
            await service.execute(
                sql="DROP ROLE ACCOUNTADMIN",
                username="analyst",
                encrypted_password="enc",
            )

        assert "ACCOUNTADMIN role cannot be dropped" in str(excinfo.value)


class TestTranslationFailureIsAudited:
    """A ``@stage`` reference that cannot be resolved never reaches the engine."""

    async def test_unknown_stage_writes_one_error_row(self, wired, monkeypatch):
        service, repo, sink = wired

        async def no_stages(database, schema):
            return {}

        monkeypatch.setattr(service, "_load_stage_configs", no_stages)

        result = await service.execute(
            sql="SELECT * FROM @qa_audit_t1.file.csv",
            username="analyst",
            encrypted_password="enc",
            database="NOVA_ANALYTICS",
        )

        assert result.error == "Stage 'qa_audit_t1' not found"
        assert sink.statuses == ["ERROR"]
        entry = sink.entries[0]
        assert entry["sql_text"] == "SELECT * FROM @qa_audit_t1.file.csv"
        assert entry["error_message"] == "Stage 'qa_audit_t1' not found"
        assert entry["database_name"] == "NOVA_ANALYTICS"
        assert repo.calls == []

    async def test_the_result_contract_is_unchanged(self, wired, monkeypatch):
        """Auditing is additive: the failure still reports itself the same way."""
        service, _, _ = wired

        async def no_stages(database, schema):
            return {}

        monkeypatch.setattr(service, "_load_stage_configs", no_stages)

        result = await service.execute(
            sql="SELECT * FROM @missing.file.csv",
            username="analyst",
            encrypted_password="enc",
        )

        assert result.error is not None
        assert result.success is False
        assert result.columns == []
        assert result.rows == []

    async def test_audit_failure_does_not_swallow_the_translation_error(
        self, wired, monkeypatch
    ):
        service, _, _ = wired

        async def no_stages(database, schema):
            return {}

        async def exploding_sink(**kwargs):
            raise RuntimeError("audit sink is down")

        monkeypatch.setattr(service, "_load_stage_configs", no_stages)
        monkeypatch.setattr("app.modules.query.service.write_audit_log", exploding_sink)

        result = await service.execute(
            sql="SELECT * FROM @missing.file.csv",
            username="analyst",
            encrypted_password="enc",
        )

        assert result.error == "Stage 'missing' not found"


class TestEnginePathsStillAuditExactlyOnce:
    """The new rows must not double-count the paths that already worked."""

    async def test_success_writes_one_row_and_no_extra(self, wired):
        service, repo, sink = wired

        result = await service.execute(
            sql="SELECT 1",
            username="analyst",
            encrypted_password="enc",
        )

        assert result.success is True
        assert sink.statuses == ["SUCCESS"]
        assert repo.calls == ["SELECT 1"]

    async def test_engine_failure_writes_one_error_row(self, wired):
        service, repo, sink = wired

        class ExplodingRepo:
            async def execute_as_user(self, sql, **kwargs):
                raise RuntimeError("engine is down")

        service._repo = ExplodingRepo()

        with pytest.raises(RuntimeError):
            await service.execute(
                sql="SELECT 1",
                username="analyst",
                encrypted_password="enc",
            )

        assert sink.statuses == ["ERROR"]
        assert sink.entries[0]["error_message"] == "engine is down"

    async def test_execute_statements_audits_once_per_rejection(self, wired):
        """The proxy path goes through ``execute_statements``; no doubling."""
        service, _, sink = wired

        results = await service.execute_statements(
            sql="DROP ROLE ACCOUNTADMIN",
            username="analyst",
            encrypted_password="enc",
        )

        assert results[0].error is not None
        assert sink.statuses == ["ERROR"]

    async def test_multi_statement_script_audits_the_refused_statement(self, wired):
        """A refusal mid-script stops there and is recorded."""
        service, repo, sink = wired

        results = await service.execute_statements(
            sql="SELECT 1; DROP ROLE ACCOUNTADMIN",
            username="analyst",
            encrypted_password="enc",
        )

        assert results[-1].error is not None
        # The first statement succeeded and is audited; the second was refused
        # and is audited. Two rows, in order.
        assert sink.statuses == ["SUCCESS", "ERROR"]
        assert sink.entries[-1]["sql_text"] == "DROP ROLE ACCOUNTADMIN"


class TestPreEngineRowShape:
    """The row must be shaped like every other query row, not a special case."""

    async def test_event_type_and_action_match_a_normal_query(self, wired):
        service, _, sink = wired

        with pytest.raises(ForbiddenSQLError):
            await service.execute(
                sql="DROP ROLE ACCOUNTADMIN",
                username="analyst",
                encrypted_password="enc",
                session_id="sess-1",
                file_id="file-1",
            )

        entry = sink.entries[0]
        assert entry["event_type"] == "query"
        assert entry["action"] == "execute"
        assert entry["object_type"] == "sql"
        assert entry["session_id"] == "sess-1"
        assert entry["file_id"] == "file-1"

    async def test_object_name_falls_back_to_workspace_without_a_database(self, wired):
        service, _, sink = wired

        with pytest.raises(ForbiddenSQLError):
            await service.execute(
                sql="DROP ROLE ACCOUNTADMIN",
                username="analyst",
                encrypted_password="enc",
            )

        assert sink.entries[0]["object_name"] == "workspace"

    async def test_no_credential_reaches_the_row(self, wired, monkeypatch):
        """A ``@stage`` statement carries injected credentials; the row must not.

        The translation never ran here — that is the point of the path — so the
        only statement text available is the user's own, which is what
        ``sql_text`` records. This pins that no credential-bearing variant
        leaks in through the new call site.
        """
        service, _, sink = wired

        async def no_stages(database, schema):
            return {}

        monkeypatch.setattr(service, "_load_stage_configs", no_stages)

        await service.execute(
            sql="SELECT * FROM @stage1.data.csv",
            username="analyst",
            encrypted_password="enc",
        )

        entry = sink.entries[0]
        assert "access_key" not in (entry["sql_text"] or "")
        assert "secret_key" not in (entry["sql_text"] or "")
        assert entry["rewritten_sql"] is None
