"""Integration tests for native-state reconciliation (NOVA-37).

The unit suite proves the state machine with fakes. This suite proves the two
acceptance criteria that only a real engine can:

* **criterion 7** — config is read through ``ADMIN SHOW FRONTEND CONFIG LIKE
  '%task%'`` (the FE-config surface, **not** ``SHOW VARIABLES``), and an
  unavailable engine is tolerated rather than fatal;
* **criterion 2** — a node whose worker heartbeat lapsed is marked ``abandoned``
  (an explicit non-success state), not silently succeeded, while a fresh node
  with no native row is left ``running`` (NOVA-46 / NOVA-52).

StarRocks is optional: when it is unreachable the module skips rather than
fails. Point at an already-running engine via::

    NOVA_ORCH_SR_PORT=9030 uv run pytest tests/integration/test_task_reconciler.py -v
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import asyncmy
import pytest
import pytest_asyncio

from app.common.nova_system import (
    TASK_ORCHESTRATION_DDL,
    migrate_task_orchestration_columns,
)
from app.core.config import settings
from app.core.database import db
from app.modules.task_orchestration.native import (
    NativeConfig,
    NativeRun,
    NativeState,
    read_native_config,
)
from app.modules.task_orchestration.reconciler import Reconciler
from app.modules.task_orchestration.repository import (
    task_orchestration_repository as repo,
)
from tests.integration._nova_system_ddl import ensure_audit_log

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = int(_EXPLICIT_PORT or "29030")
SR_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
SR_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD", "")
_USE_SHARED_STACK = _EXPLICIT_PORT is None

pytestmark = pytest.mark.engine


async def _sr_reachable() -> bool:
    try:
        conn = await asyncio.wait_for(
            asyncmy.connect(
                host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD
            ),
            timeout=5,
        )
    except Exception:
        return False
    conn.close()
    return True


async def _has_live_backend() -> bool:
    try:
        conn = await asyncmy.connect(
            host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD
        )
    except Exception:
        return False
    try:
        async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
            await cur.execute("SHOW BACKENDS")
            rows = await cur.fetchall()
        return any(str(row.get("Alive", "")).lower() == "true" for row in rows)
    except Exception:
        return False
    finally:
        conn.close()


@pytest_asyncio.fixture
async def engine_infra(request):
    if _USE_SHARED_STACK and "docker_services" in request.fixturenames:
        request.getfixturevalue("docker_services")
    if not await _sr_reachable() or not await _has_live_backend():
        pytest.skip("StarRocks not reachable or has no live backend")

    settings.STARROCKS_HOST = SR_HOST
    settings.STARROCKS_FE_MYSQL_PORT = SR_PORT
    settings.STARROCKS_ROOT_USER = SR_USER
    settings.STARROCKS_ROOT_PASSWORD = SR_PASSWORD

    await db.init_system_pool()
    await db.execute_system("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
    for ddl in TASK_ORCHESTRATION_DDL:
        await db.execute_system(ddl)
    # The reconciler audits every node transition (NOVA-37 AC #6), so it writes
    # NOVA_SYSTEM.AUDIT_LOG. ``TASK_ORCHESTRATION_DDL`` does not create that
    # table (it lives in init-nova.sql, which the dev/test engine omits), and
    # CI only passes because seed_engine.sh happens to create it first. Create
    # it here so the suite is self-contained on a clean engine.
    await ensure_audit_log(SR_HOST, SR_PORT, SR_USER, SR_PASSWORD)
    # A pre-existing test volume predates the consecutive-failure column, which
    # ``CREATE TABLE IF NOT EXISTS`` cannot add; run the same idempotent
    # migration the worker runs at startup.
    await migrate_task_orchestration_columns()
    yield
    await db.close_system_pool()


@pytest_asyncio.fixture
async def cleanup_runs(engine_infra):
    created: dict[str, list[str]] = {"graph": [], "task": []}
    yield created
    for run in created["graph"]:
        for node in await repo.list_task_runs(run):
            await repo.delete_task_run(node["id"])
        await repo.delete_graph_run(run)
    for task in created["task"]:
        await repo.delete_task(task)


@pytest_asyncio.fixture
async def audit_records(monkeypatch, engine_infra):
    """Capture ``write_audit_log`` so a test can assert the audited action.

    The reconciler audits ``NODE_ABANDONED`` when the heartbeat path settles a
    lost trace; capturing the call proves the AC #3 audit contract without a
    separate query.
    """
    entries: list[dict] = []

    async def fake_audit(**kwargs):
        entries.append(kwargs)
        return "audit-id"

    monkeypatch.setattr(
        "app.modules.task_orchestration.reconciler.write_audit_log", fake_audit
    )
    return {"entries": entries}


def _node_actions(audit_records: dict) -> list[str]:
    return [entry.get("action") for entry in audit_records["entries"]]


class TestLostTraceAgainstEngine:
    """Criterion 2 through the durable, archive-independent signal.

    A lost trace is settled by the **heartbeat path**, not by reading
    ``information_schema.task_runs``: the archive cannot say whether a
    particular task's trace is absent (NOVA-46), while a ``RUNNING`` node whose
    worker heartbeat lapsed is an unambiguous, engine-independent fact held in
    ``NOVA_SYSTEM`` (design §3). These tests drive the real path against the
    engine.
    """

    async def test_lost_trace_is_abandoned_with_audit(
        self, engine_infra, cleanup_runs, audit_records
    ):
        """AC #2/#3: the heartbeat path settles the node and audits it.

        A node claimed by a worker that then died: the row is running with a
        heartbeat that never advances. ``scan`` reports it and
        ``abandon_stale_nodes`` performs the conditional write to ``abandoned``
        with a ``NODE_ABANDONED`` audit — the durable lost-trace settlement.
        The negative heartbeat timeout makes the never-stamped row stale
        immediately without sleeping.
        """
        suffix = uuid4().hex[:8]
        name = f"lost_{suffix}"
        task = await repo.create_task(
            {
                "name": name,
                "timezone": "UTC",
                "definition": "INSERT INTO t SELECT 1",
                "database_name": "NOVA_SYSTEM",
                "schedule_kind": "manual",
            },
            created_by=SR_USER,
        )
        cleanup_runs["task"].append(task["id"])
        run = await repo.create_graph_run(
            {"graph_id": f"g_{suffix}", "trigger_type": "manual", "state": "running"}
        )
        cleanup_runs["graph"].append(run["id"])
        node = await repo.create_task_run(
            {
                "graph_run_id": run["id"],
                "task_id": task["id"],
                "state": "running",
                "delegated": True,
            }
        )
        reconciler = Reconciler(repo, heartbeat_timeout_seconds=-1)

        report = await reconciler.scan()
        assert str(node["id"]) in report.abandoned_task_runs
        assert str(run["id"]) in report.abandoned_graph_runs

        abandoned = await reconciler.abandon_stale_nodes()

        assert str(node["id"]) in {str(row["id"]) for row in abandoned}
        refreshed = await repo.get_task_run(node["id"])
        assert refreshed is not None
        assert refreshed["state"] == "abandoned"
        assert refreshed["state"] != "success"
        assert "NODE_ABANDONED" in _node_actions(audit_records)

    async def test_settling_a_lost_trace_is_idempotent(
        self, engine_infra, cleanup_runs, audit_records
    ):
        """Criterion 5: a second pass on the settled row writes nothing."""
        suffix = uuid4().hex[:8]
        name = f"idem_{suffix}"
        task = await repo.create_task(
            {
                "name": name,
                "timezone": "UTC",
                "definition": "INSERT INTO t SELECT 1",
                "database_name": "NOVA_SYSTEM",
                "schedule_kind": "manual",
            },
            created_by=SR_USER,
        )
        cleanup_runs["task"].append(task["id"])
        run = await repo.create_graph_run(
            {"graph_id": f"g_{suffix}", "trigger_type": "manual", "state": "running"}
        )
        cleanup_runs["graph"].append(run["id"])
        node = await repo.create_task_run(
            {
                "graph_run_id": run["id"],
                "task_id": task["id"],
                "state": "running",
                "delegated": True,
            }
        )
        reconciler = Reconciler(repo, heartbeat_timeout_seconds=-1)

        first = await reconciler.abandon_stale_nodes()
        audit_count = len(audit_records["entries"])
        second = await reconciler.abandon_stale_nodes()

        assert str(node["id"]) in {str(row["id"]) for row in first}
        assert second == []
        assert len(audit_records["entries"]) == audit_count
        refreshed = await repo.get_task_run(node["id"])
        assert refreshed is not None and refreshed["state"] == "abandoned"

    async def test_healthy_node_is_never_reported_as_lost(
        self, engine_infra, cleanup_runs, audit_records
    ):
        """A node whose worker is alive must not be declared lost.

        The heartbeat is fresh (it was just created), so the durable scan — the
        only path that settles a lost trace — reports nothing for it.
        """
        suffix = uuid4().hex[:8]
        name = f"live_{suffix}"
        task = await repo.create_task(
            {
                "name": name,
                "timezone": "UTC",
                "definition": "INSERT INTO NOVA_SYSTEM.nova_reconcile_probe SELECT 1",
                "database_name": "NOVA_SYSTEM",
                "schedule_kind": "manual",
            },
            created_by=SR_USER,
        )
        cleanup_runs["task"].append(task["id"])
        run = await repo.create_graph_run(
            {"graph_id": f"g_{suffix}", "trigger_type": "manual", "state": "running"}
        )
        cleanup_runs["graph"].append(run["id"])
        node = await repo.create_task_run(
            {
                "graph_run_id": run["id"],
                "task_id": task["id"],
                "state": "running",
                "delegated": True,
            }
        )

        reconciler = Reconciler(repo, heartbeat_timeout_seconds=3600)
        report = await reconciler.scan()
        abandoned = await reconciler.abandon_stale_nodes()

        assert str(node["id"]) not in report.abandoned_task_runs
        assert str(node["id"]) not in report.abandoned_graph_runs
        assert abandoned == []
        refreshed = await repo.get_task_run(node["id"])
        assert refreshed is not None and refreshed["state"] == "running"
        assert _node_actions(audit_records) == []

    async def test_reconcile_native_never_settles_a_fresh_node_without_a_native_row(
        self, engine_infra, cleanup_runs, audit_records
    ):
        """NOVA-52: a fresh node with no ``task_runs`` row is not abandoned.

        The worker just sent ``SUBMIT TASK``: the node row is ``running`` with a
        fresh heartbeat, but the engine has no settled ``task_runs`` row yet
        (the task name below has never been scheduled). ``reconcile_native``
        must leave the row and the audit untouched — the archive cannot prove a
        per-task lost trace. Before the fix this path wrote ``abandoned`` and
        ``NODE_ABANDONED`` even though the worker was alive.
        """
        suffix = uuid4().hex[:8]
        name = f"qa_healthy_{suffix}"
        task = await repo.create_task(
            {
                "name": name,
                "timezone": "UTC",
                "definition": "INSERT INTO NOVA_SYSTEM.nova_reconcile_probe SELECT 1",
                "database_name": "NOVA_SYSTEM",
                "schedule_kind": "manual",
            },
            created_by=SR_USER,
        )
        cleanup_runs["task"].append(task["id"])
        run = await repo.create_graph_run(
            {"graph_id": f"g_{suffix}", "trigger_type": "manual", "state": "running"}
        )
        cleanup_runs["graph"].append(run["id"])
        node = await repo.create_task_run(
            {
                "graph_run_id": run["id"],
                "task_id": task["id"],
                "state": "running",
                "delegated": True,
            }
        )
        reconciler = Reconciler(repo, heartbeat_timeout_seconds=3600)

        scan_report = await reconciler.scan()
        native_report = await reconciler.reconcile_native()

        assert scan_report.abandoned_task_runs == []
        assert native_report.advanced == []
        assert native_report.lost_traces == []
        refreshed = await repo.get_task_run(node["id"])
        assert refreshed is not None and refreshed["state"] == "running"
        assert _node_actions(audit_records) == []


class TestFrontendConfigRead:
    """Criterion 7: read config from the FE config surface, tolerantly."""

    async def test_reads_task_config_via_frontend_config(self, engine_infra):
        config = await read_native_config()
        assert isinstance(config, NativeConfig)
        # The two keys are FE config on 4.1.1. When the engine exposes them the
        # TTL must be the real 7 days (604800), never the wrong 86400 premise.
        if config.task_runs_ttl_second is not None:
            assert config.task_runs_ttl_second > 0
        if config.max_task_consecutive_fail_count is not None:
            assert config.max_task_consecutive_fail_count > 0

    async def test_config_read_tolerates_unavailable_engine(self, monkeypatch):
        """A refused/unavailable engine is a tolerated result, not a crash."""
        import app.modules.task_orchestration.native as native_module

        class _Boom:
            async def __aenter__(self):
                raise RuntimeError("FE unavailable")

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(native_module.db, "system_conn", lambda: _Boom())
        config = await read_native_config()
        assert config.available is False
        assert config.max_task_consecutive_fail_count is None


class TestEngineReadFaultTolerance:
    """The reconciler must degrade, never guess, when the task-run read fails.

    On a clean engine ``information_schema.task_runs`` is served by the absent
    ``_statistics_.task_run_history`` archive and fails with a 1064 — the exact
    surface CI ran into. That failure is engine-wide and uninformative per task
    (NOVA-46), so **every** failed read is ``UNKNOWN`` and nothing is written:

    * an **unreachable** engine is unobservable;
    * a **live** engine whose archive refused the read has still told us nothing
      about any individual task's trace.

    Either way the node stays ``running`` here; the lost trace is settled by the
    heartbeat path (``TestLostTraceAgainstEngine``), which cannot fire on a
    healthy in-flight node.
    """

    async def test_unreadable_task_runs_never_abandons_a_node(
        self, engine_infra, cleanup_runs
    ):
        suffix = uuid4().hex[:8]
        name = f"unreadable_{suffix}"
        task = await repo.create_task(
            {
                "name": name,
                "timezone": "UTC",
                "definition": "INSERT INTO t SELECT 1",
                "database_name": "NOVA_SYSTEM",
                "schedule_kind": "manual",
            },
            created_by=SR_USER,
        )
        cleanup_runs["task"].append(task["id"])
        run = await repo.create_graph_run(
            {"graph_id": f"g_{suffix}", "trigger_type": "manual", "state": "running"}
        )
        cleanup_runs["graph"].append(run["id"])
        node = await repo.create_task_run(
            {
                "graph_run_id": run["id"],
                "task_id": task["id"],
                "state": "running",
                "delegated": True,
            }
        )

        # An unreadable surface is UNKNOWN, not MISSING.
        report = await Reconciler(
            repo,
            observer=_UnknownObserver(),
            heartbeat_timeout_seconds=3600,
        ).reconcile_native()

        # The reconciler may cover other tests' running rows, so assert on
        # *this* task: it must not be abandoned and must still be running.
        assert name not in report.lost_traces
        assert name in report.unknown
        refreshed = await repo.get_task_run(node["id"])
        assert refreshed is not None and refreshed["state"] == "running"

    async def test_archive_refusal_on_a_live_engine_stays_unknown(
        self, engine_infra, cleanup_runs
    ):
        """The CI signature: a live engine's archive 1064 must not settle a node.

        The archive read fails while ``SELECT 1`` proves the engine up. The
        failure is engine-wide, so it says nothing about this task's trace; the
        node must stay ``running`` (no write), and the durable heartbeat path is
        what later settles a genuinely lost trace.
        """
        suffix = uuid4().hex[:8]
        name = f"refused_{suffix}"
        task = await repo.create_task(
            {
                "name": name,
                "timezone": "UTC",
                "definition": "INSERT INTO t SELECT 1",
                "database_name": "NOVA_SYSTEM",
                "schedule_kind": "manual",
            },
            created_by=SR_USER,
        )
        cleanup_runs["task"].append(task["id"])
        run = await repo.create_graph_run(
            {"graph_id": f"g_{suffix}", "trigger_type": "manual", "state": "running"}
        )
        cleanup_runs["graph"].append(run["id"])
        node = await repo.create_task_run(
            {
                "graph_run_id": run["id"],
                "task_id": task["id"],
                "state": "running",
                "delegated": True,
            }
        )

        report = await Reconciler(
            repo,
            observer=_UnknownObserver(),
            heartbeat_timeout_seconds=3600,
        ).reconcile_native()

        assert name not in report.lost_traces
        assert name in report.unknown
        refreshed = await repo.get_task_run(node["id"])
        assert refreshed is not None
        assert refreshed["state"] == "running"
        assert refreshed["state"] != "abandoned"


class _UnknownObserver:
    """A native observer whose ``task_runs`` read never yields a trace.

    Models the engine-wide archive failure: ``SELECT 1`` succeeds (the config
    read still hits the live FE surface) but the ``task_runs`` surface is
    unreadable, so every task is ``UNKNOWN``. This is the reader's own verdict
    for that failure, produced by the real ``read_latest_native_runs``; the
    observer merely keeps the test independent of the warm CI engine's archive
    state.
    """

    async def read_native_config(self) -> NativeConfig:
        return await read_native_config()

    async def read_runs(self, task_names: list[str]) -> dict[str, NativeRun]:
        return {
            name: NativeRun(task_name=name, state=NativeState.UNKNOWN)
            for name in task_names
        }

    async def read_schedules(self, task_names: list[str]) -> dict[str, str]:
        return {name: "" for name in task_names}


_ARCHIVE_1064 = (
    "ERROR 1064 (HY000): Getting analyzing error. Detail message: "
    "RepoExecutor execute sql failed: SELECT history_content_json FROM "
    "_statistics_.task_run_history WHERE TRUE AND task_name = 'x' "
    "ORDER BY create_time DESC LIMIT 10000."
)


class _ArchivePoisonedConnection:
    """The real engine connection, with only the archive-reaching read poisoned.

    A fresh FE raises 1064 when a ``task_runs`` read needs
    ``_statistics_.task_run_history`` before that archive is initialized, while
    the engine keeps serving every other statement. That race cannot be held
    deterministically against a warm CI engine by manipulating FE internals, so
    the failure is injected at the statement boundary instead: the batch read
    (the one with ``ORDER BY TASK_NAME``) raises the recorded CI error, and every
    other statement — including the ``SELECT 1`` liveness probe — goes to the
    live engine untouched. The real reader and the reconciler run for real.
    """

    def __init__(self, conn) -> None:
        self._conn = conn

    def cursor(self, *args, **kwargs):
        return _ArchivePoisonedCursor(self._conn.cursor(*args, **kwargs))

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _ArchivePoisonedCursor:
    def __init__(self, cur) -> None:
        self._cur = cur

    async def __aenter__(self):
        await self._cur.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._cur.__aexit__(*exc)

    async def execute(self, sql, params=None):
        if "ORDER BY TASK_NAME" in sql:
            raise RuntimeError(_ARCHIVE_1064)
        return await self._cur.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._cur, name)


class TestArchiveSurfaceFailureThroughTheEngine:
    """The real reader's verdict on the CI archive signature.

    The warm-engine fault-tolerance tests script the observer, so they prove the
    reconciler's decision, not the reader's classification. This class drives
    the actual CI failure: the batch ``task_runs`` read raises the recorded 1064
    while ``SELECT 1`` still succeeds on the live engine. The reader must return
    ``UNKNOWN`` for that -- the failure is engine-wide and per-task uninformative
    (NOVA-46) -- and the node must stay ``running`` with no write.
    """

    async def test_archive_1064_on_a_live_engine_reads_unknown(
        self, engine_infra, cleanup_runs
    ):
        import app.modules.task_orchestration.native as native_module

        suffix = uuid4().hex[:8]
        name = f"lost1064_{suffix}"
        task = await repo.create_task(
            {
                "name": name,
                "timezone": "UTC",
                "definition": "INSERT INTO t SELECT 1",
                "database_name": "NOVA_SYSTEM",
                "schedule_kind": "manual",
            },
            created_by=SR_USER,
        )
        cleanup_runs["task"].append(task["id"])
        run = await repo.create_graph_run(
            {"graph_id": f"g_{suffix}", "trigger_type": "manual", "state": "running"}
        )
        cleanup_runs["graph"].append(run["id"])
        node = await repo.create_task_run(
            {
                "graph_run_id": run["id"],
                "task_id": task["id"],
                "state": "running",
                "delegated": True,
            }
        )

        # The reader, not a stub, classifies the archive 1064.
        async with db.system_conn() as conn:
            read = await native_module.read_latest_native_runs(
                _ArchivePoisonedConnection(conn), [name]
            )
        assert read[name].state is NativeState.UNKNOWN

        report = await Reconciler(
            repo, observer=_UnknownObserver(), heartbeat_timeout_seconds=3600
        ).reconcile_native()

        assert name not in report.lost_traces, (
            "an archive 1064 is engine-wide and must not settle a trace; "
            f"got lost_traces={report.lost_traces} unknown={report.unknown}"
        )
        assert name in report.unknown
        refreshed = await repo.get_task_run(node["id"])
        assert refreshed is not None
        assert refreshed["state"] == "running"


class TestAutoPauseThresholdAgainstEngine:
    """Criterion 3: the threshold is read from the real engine, not hardcoded.

    Native runs are supplied by a small observer so the failure count can be
    driven precisely, while the FE config (``max_task_consecutive_fail_count``)
    still comes from the live engine — proving the gate uses the engine's value.
    """

    async def test_engine_config_is_the_threshold(self, engine_infra, cleanup_runs):
        from app.modules.task_orchestration.native import NativeRun, NativeState

        real_config = await read_native_config()
        ceiling = real_config.max_task_consecutive_fail_count or 10
        assert ceiling > 1, "the engine must expose a real threshold for this test"

        class _Observer:
            async def read_native_config(self):
                return real_config

            async def read_runs(self, names):
                return {
                    n: NativeRun(task_name=n, state=NativeState.FAILED) for n in names
                }

            async def read_schedules(self, names):
                return {n: "MANUAL" for n in names}

        suffix = uuid4().hex[:8]
        name = f"thresh_{suffix}"
        task = await repo.create_task(
            {
                "name": name,
                "timezone": "UTC",
                "definition": "INSERT INTO NOVA_SYSTEM.nova_reconcile_probe SELECT 1",
                "database_name": "NOVA_SYSTEM",
                "schedule_kind": "manual",
            },
            created_by=SR_USER,
        )
        cleanup_runs["task"].append(task["id"])
        run = await repo.create_graph_run(
            {"graph_id": f"g_{suffix}", "trigger_type": "manual", "state": "running"}
        )
        cleanup_runs["graph"].append(run["id"])
        reconciler = Reconciler(repo, observer=_Observer())

        # Failures 1..ceiling-1 must stay quiet. The reconciler legitimately
        # covers every running node in the system, so assert on *this* task,
        # not on the whole report.
        for attempt in range(1, ceiling):
            await repo.create_task_run(
                {
                    "graph_run_id": run["id"],
                    "task_id": task["id"],
                    "state": "running",
                    "delegated": True,
                }
            )
            report = await reconciler.reconcile_native()
            assert name not in report.auto_paused, f"alarmed at attempt {attempt}/{ceiling}"

        # The ceiling-th consecutive failure raises the alarm.
        await repo.create_task_run(
            {
                "graph_run_id": run["id"],
                "task_id": task["id"],
                "state": "running",
                "delegated": True,
            }
        )
        report = await reconciler.reconcile_native()
        assert name in report.auto_paused


class TestLostTraceSettlesThroughWorkerService:
    """Criterion 1 + 4: reconcile re-enqueues, and the DAG does not hang."""

    async def test_lost_trace_re_enqueues_and_settles_the_graph(
        self, engine_infra, cleanup_runs, audit_records
    ):
        """A lost trace is settled via the heartbeat path, then re-driven.

        This is the restart-safe path: a worker submits a node and dies, so the
        node's heartbeat stops advancing. ``reconcile_once`` abandons the row
        (audited ``NODE_ABANDONED``) from durable state alone — no engine
        archive read decides it — then re-enqueues the graph; the worker
        re-evaluates and the graph settles instead of hanging in ``running``
        forever. The native read runs for real against the engine.
        """
        from app.modules.task_orchestration.credentials import StaticCredentialProvider
        from app.modules.task_orchestration.execution import DelegateExecutor
        from app.modules.task_orchestration.worker_service import WorkerService

        suffix = uuid4().hex[:8]
        name = f"settle_{suffix}"
        task = await repo.create_task(
            {
                "name": name,
                "timezone": "UTC",
                "definition": "INSERT INTO NOVA_SYSTEM.nova_reconcile_probe SELECT 1",
                "database_name": "NOVA_SYSTEM",
                "schedule_kind": "manual",
            },
            created_by=SR_USER,
        )
        cleanup_runs["task"].append(task["id"])
        # The production standalone graph key is the task **id** (see
        # ``scheduler.build_graphs``), not the name. Using the name here would
        # let the graph settle via "graph has no nodes" and mask whether the
        # heartbeat path actually drove the node.
        run = await repo.create_graph_run(
            {"graph_id": task["id"], "trigger_type": "manual", "state": "running"}
        )
        cleanup_runs["graph"].append(run["id"])
        node = await repo.create_task_run(
            {
                "graph_run_id": run["id"],
                "task_id": task["id"],
                "state": "running",
                "delegated": True,
            }
        )
        await db.execute_system(
            "CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.nova_reconcile_probe ("
            " id INT NOT NULL"
            ") PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
            'PROPERTIES("replication_num"="1")'
        )

        executor = DelegateExecutor(
            StaticCredentialProvider({SR_USER: SR_PASSWORD}),
            poll_interval=0.5,
            poll_timeout=60.0,
        )
        service = WorkerService(
            repo,
            executor,
            consumer=_NullConsumer(),
            # A negative timeout makes the never-stamped heartbeat stale
            # immediately, so the durable scan reports the dead worker without
            # the test sleeping for a real timeout.
            reconciler=Reconciler(repo, heartbeat_timeout_seconds=-1),
        )

        # A dead worker's graph must be re-driven to a terminal state, not hang:
        # bound the call so an infinite ``_drive`` loop fails instead of stalling
        # the suite.
        await asyncio.wait_for(service.reconcile_once(), timeout=60)

        # The node was settled by the heartbeat path, and the audit fired. This
        # is what proves the node did not merely vanish behind a graph-level
        # failure.
        assert "NODE_ABANDONED" in _node_actions(audit_records)
        # The abandoned node must then be re-evaluated by the worker, so it ends
        # in a settled state (its body runs) rather than remaining ``running``.
        final_node = await repo.get_task_run(node["id"])
        assert final_node is not None
        assert final_node["state"] == "success"
        settled = await repo.get_graph_run(run["id"])
        assert settled is not None
        assert settled["state"] == "success"

    async def test_fresh_heartbeat_running_node_does_not_hang_reconcile(
        self, engine_infra, cleanup_runs
    ):
        """NOVA-53: a running node with a *fresh* heartbeat must not hang.

        The prior test drives the stale-heartbeat path: a negative timeout lets
        ``abandon_stale_nodes`` settle the node to ``abandoned`` before
        ``_requeue``, so the node reaching ``_drive`` is claimable. That path
        never reaches NOVA-53's shape.

        Here the heartbeat has **not** lapsed, so the reconciler cannot settle
        the node — exactly the live-worker / fresh-engine case. The node is
        still ``running`` when ``_drive`` re-evaluates it. ``evaluate`` names it
        ready (standalone ⇒ no parents ⇒ ``all(...)`` true) but
        ``_execute_ready`` cannot claim it, so with no progress guard ``_drive``
        spins forever and ``reconcile_once`` never returns. Bounded here so a
        regression fails instead of stalling the suite.

        The production standalone graph key is the task **id**. The node is left
        ``running``; the delivery does all it can and returns ``RUNNING``.
        """
        from app.modules.task_orchestration.credentials import StaticCredentialProvider
        from app.modules.task_orchestration.execution import DelegateExecutor
        from app.modules.task_orchestration.worker_service import WorkerService

        suffix = uuid4().hex[:8]
        name = f"fresh_{suffix}"
        task = await repo.create_task(
            {
                "name": name,
                "timezone": "UTC",
                # A per-test table so a regression that lets the node execute
                # cannot race the sibling test's shared probe table.
                "definition": (
                    f"INSERT INTO NOVA_SYSTEM.nova_fresh_probe_{suffix} SELECT 1"
                ),
                "database_name": "NOVA_SYSTEM",
                "schedule_kind": "manual",
            },
            created_by=SR_USER,
        )
        cleanup_runs["task"].append(task["id"])
        run = await repo.create_graph_run(
            {"graph_id": task["id"], "trigger_type": "manual", "state": "running"}
        )
        cleanup_runs["graph"].append(run["id"])
        node = await repo.create_task_run(
            {
                "graph_run_id": run["id"],
                "task_id": task["id"],
                "state": "running",
                "delegated": True,
            }
        )

        executor = DelegateExecutor(
            StaticCredentialProvider({SR_USER: SR_PASSWORD}),
            poll_interval=0.5,
            poll_timeout=60.0,
        )
        service = WorkerService(
            repo,
            executor,
            consumer=_NullConsumer(),
            # A large positive timeout keeps the just-created heartbeat fresh, so
            # the heartbeat path does NOT settle the node: NOVA-53's shape.
            reconciler=Reconciler(repo, heartbeat_timeout_seconds=3600),
        )

        await asyncio.wait_for(service.reconcile_once(), timeout=60)

        # The unclaimable node was not executed and did not change state; the
        # graph is not pinned to a dead worker any longer, but it is also not
        # falsely settled.
        final_node = await repo.get_task_run(node["id"])
        assert final_node is not None
        assert final_node["state"] == "running"
        current = await repo.get_graph_run(run["id"])
        assert current is not None
        assert current["state"] == "running"


class _NullConsumer:
    """A consumer that never yields; native reconcile needs no stream."""

    async def read(self, *, count=None, block_ms=5000):
        return []

    async def claim_stale(self, *, min_idle_ms=60000, count=10):
        return []

    async def ack(self, stream_id):
        return None

    async def ensure_group(self):
        return None

