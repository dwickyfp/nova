from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.query.repository import QueryRepository
from app.modules.query_autopilot.telemetry import EXECUTION, Collector, ExecutionIdentity, purpose
from app.sql_frontend.session_functions import QueryCorrelationSession, preserve_last_query_id


@pytest.mark.asyncio
async def test_queue_is_bounded_and_failure_does_not_retry_user_work():
    c = Collector(1)
    args = dict(
        identity=ExecutionIdentity(),
        sql="select id from t",
        source="web",
        status="success",
        elapsed_ms=1,
        kwargs={"username": "alice", "role": "analyst"},
    )
    c.observe(**args)
    c.observe(**args)
    assert c.accepted == 1 and c.dropped == 1
    with purpose("diagnostic"):
        c.observe(**args)
    assert c.dropped == 1
    repo = AsyncMock()
    repo.observations.side_effect = RuntimeError("offline")
    await c.flush(repo)
    assert repo.observations.call_count == 1
    assert c.dropped == 2 and c.queue.empty()


def test_session_function_rewrite_is_structural_and_preserves_labels():
    identity = str(uuid4())
    session = QueryCorrelationSession(identity, True)
    sql, labels = preserve_last_query_id(
        "select last_query_id(), 'last_query_id()' as label", session
    )
    assert sql == f"select '{identity}', 'last_query_id()' as label"
    assert labels == {0: "last_query_id()"}
    assert preserve_last_query_id("select last_query_id()", None) == ("select last_query_id()", {})


@pytest.mark.asyncio
async def test_correlation_is_after_unbuffered_drain_and_is_not_invented():
    events = []
    qid = str(uuid4())

    class Cursor:
        description = [("id", 3, None, None, None, None, True)]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            events.append("drain")

        async def execute(self, sql):
            events.append(sql)

        async def fetchmany(self, n):
            return [(1,), (2,)]

        async def fetchone(self):
            return {
                "nova_engine_query_id": qid,
                "nova_catalog": "external_catalog",
                "nova_database": "external_database",
            }

    class Connection:
        def cursor(self, *_):
            return Cursor()

    token = EXECUTION.set(ExecutionIdentity())
    try:
        result = await QueryRepository._execute_on(
            Connection(), "select id from t", role=None, max_rows=1, start=0
        )
        assert result.engine_query_ids == [qid]
        assert events.index("drain") < events.index(
            "SELECT LAST_QUERY_ID() AS nova_engine_query_id, "
            "CATALOG() AS nova_catalog, DATABASE() AS nova_database"
        )
        assert result.truncated
        assert EXECUTION.get().catalog == "external_catalog"
        assert EXECUTION.get().database == "external_database"
    finally:
        EXECUTION.reset(token)


def test_native_role_unavailable_is_observed_but_cannot_be_enrolled():
    from app.modules.query_autopilot.models import Enrollment
    from tests.unit.query_autopilot.test_policy_experiments import fixtures

    collector = Collector()
    collector.observe(
        identity=ExecutionIdentity(),
        sql="SELECT 1",
        source="web",
        status="success",
        elapsed_ms=1,
        kwargs={"username": "alice"},
    )
    observation = collector.queue.get_nowait()
    assert observation.scope.active_role is None
    _, enrollment, _ = fixtures()
    with pytest.raises(ValueError, match="explicit named active role"):
        Enrollment.model_validate({**enrollment.model_dump(), "scope": observation.scope})


async def test_audit_optional_metadata_upgrade_failure_keeps_original_audit(monkeypatch):
    from asyncmy.errors import ProgrammingError

    from app.common.audit import write_audit_log

    write = AsyncMock(
        side_effect=[ProgrammingError(1054, "Unknown column 'nova_execution_id'"), {"affected": 1}]
    )
    monkeypatch.setattr("app.common.audit.db.execute_system", write)
    token = EXECUTION.set(ExecutionIdentity())
    try:
        query_id = await write_audit_log(
            event_type="QUERY",
            user_name="alice",
            action="SELECT",
            object_type="QUERY",
            object_name="query",
            status="SUCCESS",
        )
        assert query_id != EXECUTION.get().id
        assert write.call_count == 2
        legacy, args = write.call_args.args
        assert "nova_execution_id" not in legacy
        assert legacy.count("%s") == len(args)
        assert args[0] == query_id
    finally:
        EXECUTION.reset(token)


async def test_cancelled_cursor_is_closed_without_resubmitting_workload():
    import asyncio

    calls = []

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def execute(self, statement):
            calls.append(statement)
            raise asyncio.CancelledError()

    class Connection:
        closed = False

        def cursor(self, *_):
            return Cursor()

        def close(self):
            self.closed = True

    connection = Connection()
    with pytest.raises(asyncio.CancelledError):
        await QueryRepository._execute_on(connection, "SELECT 1", role=None, max_rows=1, start=0)
    assert calls == ["SELECT 1"]
    assert connection.closed


@pytest.mark.parametrize("catalog", ["external_catalog", None])
def test_observed_engine_scope_overrides_request_defaults_and_blocks_native_replay(catalog):
    from app.modules.query_autopilot.payloads import eligible_sample
    from tests.unit.query_autopilot.test_policy_experiments import fixtures

    _, enrollment, _ = fixtures()
    c = Collector()
    c.enrollments = [enrollment]
    identity = ExecutionIdentity(
        scope_checked=True, catalog=catalog, database="external_database",
        query_ids=[str(uuid4())],
    )
    c.observe(
        identity=identity, sql="SELECT id FROM orders", source="mysql_proxy",
        status="success", elapsed_ms=1,
        kwargs={
            "username": enrollment.scope.principal,
            "role": enrollment.scope.active_role,
            "database": enrollment.scope.database,
        },
    )
    observed = c.queue.get_nowait()
    assert observed.scope.catalog == (catalog or "")
    assert observed.scope.database == "external_database"
    assert observed.scope.cohort_id != enrollment.scope.cohort_id
    assert not eligible_sample("SELECT id FROM orders", observed.scope, enrollment)
    assert not c.samples


def test_policy_epoch_is_frozen_at_query_start_and_isolates_cohorts():
    c = Collector()
    kwargs = {"username": "alice", "role": "analyst", "database": "analytics"}
    first = ExecutionIdentity()
    c.policy_revision = "3:58:2"
    assert not c.request_profile(first, "SELECT id FROM orders", kwargs)
    c.policy_revision = "3:59:2"
    c.observe(identity=first, sql="SELECT id FROM orders", source="web", status="success",
              elapsed_ms=1, kwargs=kwargs)
    earlier = c.queue.get_nowait()
    second = ExecutionIdentity()
    c.request_profile(second, "SELECT id FROM orders", kwargs)
    c.observe(identity=second, sql="SELECT id FROM orders", source="web", status="success",
              elapsed_ms=1, kwargs=kwargs)
    later = c.queue.get_nowait()
    assert earlier.scope.policy_revision == "3:58:2"
    assert later.scope.policy_revision == "3:59:2"
    assert earlier.family_id == later.family_id
    assert earlier.scope.cohort_id != later.scope.cohort_id
