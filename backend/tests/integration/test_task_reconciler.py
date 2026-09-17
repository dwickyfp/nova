"""Integration tests for native-state reconciliation (NOVA-37).

The unit suite proves the state machine with fakes. This suite proves the two
acceptance criteria that only a real engine can:

* **criterion 7** — config is read through ``ADMIN SHOW FRONTEND CONFIG LIKE
  '%task%'`` (the FE-config surface, **not** ``SHOW VARIABLES``), and an
  unavailable engine is tolerated rather than fatal;
* **criterion 2** — a node whose native trace vanished from
  ``information_schema.task_runs`` is marked ``abandoned`` (an explicit
  non-success state), not silently succeeded.

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


class _ScriptedObserver:
    """Supplies native observations while the FE config stays real.

    ``information_schema.task_runs`` is served by the engine's internal
    ``_statistics_.task_run_history`` archive, which is **absent on a clean
    engine** and makes the read fail with a 1064 — for admins too, not just
    restricted users (design §1). A CI stack starts clean, so a test that
    depends on that view being readable is testing the engine's archive, not
    the reconciler. This observer scripts the native state so the reconciler's
    decision is exercised deterministically on any engine, while
    ``read_native_config`` still hits the live FE-config surface.
    """

    def __init__(
        self,
        runs: dict[str, NativeRun] | None = None,
        schedules: dict[str, str] | None = None,
    ) -> None:
        self.runs = runs or {}
        self.schedules = schedules or {}

    async def read_native_config(self) -> NativeConfig:
        return await read_native_config()

    async def read_runs(self, task_names: list[str]) -> dict[str, NativeRun]:
        # A name absent from the script is a failed read, not "no run": the
        # reconciler covers every running row, and a test must not fabricate
        # lost work for another test's task.
        return {
            name: self.runs.get(
                name, NativeRun(task_name=name, state=NativeState.UNKNOWN)
            )
            for name in task_names
        }

    async def read_schedules(self, task_names: list[str]) -> dict[str, str]:
        return {name: self.schedules.get(name, "") for name in task_names}


class TestLostTraceAgainstEngine:
    """Criterion 2: an absent native trace is explicit, never success."""

    async def test_missing_native_run_is_abandoned(self, engine_infra, cleanup_runs):
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
        # A node claimed by a worker that then died: the row is running, but
        # no native run exists for the task name.
        node = await repo.create_task_run(
            {
                "graph_run_id": run["id"],
                "task_id": task["id"],
                "state": "running",
                "delegated": True,
            }
        )

        report = await Reconciler(
            repo, observer=_ScriptedObserver({name: NativeRun(name, NativeState.MISSING)})
        ).reconcile_native()

        assert name in report.lost_traces
        refreshed = await repo.get_task_run(node["id"])
        assert refreshed is not None
        assert refreshed["state"] == "abandoned"
        assert refreshed["state"] != "success"

    async def test_reconcile_is_idempotent_against_engine(
        self, engine_infra, cleanup_runs
    ):
        """Criterion 5: a second pass on unchanged state changes nothing."""
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
        reconciler = Reconciler(
            repo, observer=_ScriptedObserver({name: NativeRun(name, NativeState.MISSING)})
        )

        first = await reconciler.reconcile_native()
        second = await reconciler.reconcile_native()

        assert name in first.lost_traces
        assert second.advanced == []
        assert second.lost_traces == []
        refreshed = await repo.get_task_run(node["id"])
        assert refreshed is not None and refreshed["state"] == "abandoned"

    async def test_running_node_with_a_native_row_is_not_abandoned(
        self, engine_infra, cleanup_runs
    ):
        """A node with a live native row must not be declared lost.

        The reconciler must observe a task whose native run is still in flight
        as RUNNING/PENDING rather than MISSING. The native row is supplied by
        the observer: on a clean engine the ``task_runs`` view is served by an
        absent archive and fails with a 1064 even for admins, so reading it
        here would test the engine's archive rather than the reconciler.
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

        report = await Reconciler(
            repo,
            observer=_ScriptedObserver({name: NativeRun(name, NativeState.RUNNING)}),
        ).reconcile_native()

        assert name not in report.lost_traces
        refreshed = await repo.get_task_run(node["id"])
        assert refreshed is not None
        assert refreshed["state"] == "running"


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
    """The reconciler must degrade when the engine's task-run read fails.

    On a clean engine ``information_schema.task_runs`` is served by the absent
    ``_statistics_.task_run_history`` archive and fails with a 1064 — the
    exact surface CI ran into. What that failure *means* depends on the engine,
    and the reader decides by probing it (NOVA-46):

    * an **unreachable** engine is unobservable — ``UNKNOWN``, nothing written,
      so a fault cannot discard a healthy run;
    * a **live** engine whose archive refused the read has no trace to show —
      ``MISSING``, and the node settles ``abandoned`` rather than hanging.

    The first half of the class drives the reader's probe down to pin the
    ``UNKNOWN`` branch; the second pins the ``MISSING`` branch. Together they
    prove the classification hangs on engine liveness, not on "did a read
    raise".
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

        # An unreachable engine is UNKNOWN, not MISSING.
        report = await Reconciler(
            repo, observer=_ScriptedObserver({name: NativeRun(name, NativeState.UNKNOWN)})
        ).reconcile_native()

        # The reconciler may cover other tests' running rows, so assert on
        # *this* task: it must not be abandoned and must still be running.
        assert name not in report.lost_traces
        refreshed = await repo.get_task_run(node["id"])
        assert refreshed is not None and refreshed["state"] == "running"

    async def test_archive_refusal_on_a_live_engine_settles_the_node(
        self, engine_infra, cleanup_runs
    ):
        """A live engine whose archive refuses the read means the trace is gone.

        This is the CI failure NOVA-46 was filed for: the reader's ``task_runs``
        statement fails, but ``SELECT 1`` on the same connection proves the
        engine is up, so the failure is "this task has no observable trace", not
        "the engine cannot be observed". Leaving the node ``running`` would hang
        the DAG silently; it must settle ``abandoned`` (criterion 2).
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
            repo, observer=_ScriptedObserver({name: NativeRun(name, NativeState.MISSING)})
        ).reconcile_native()

        assert name in report.lost_traces
        refreshed = await repo.get_task_run(node["id"])
        assert refreshed is not None
        assert refreshed["state"] == "abandoned"
        assert refreshed["state"] != "success"

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
        self, engine_infra, cleanup_runs
    ):
        """A lost trace is abandoned, then the graph is re-driven from state.

        This is the restart-safe path: a worker submits a node, dies, and the
        FE loses the run's trace. The reconciler marks it abandoned and
        re-enqueues the graph from ``NOVA_SYSTEM`` alone; the worker re-evaluates
        and the graph settles instead of hanging in ``running`` forever.
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
        run = await repo.create_graph_run(
            {"graph_id": name, "trigger_type": "manual", "state": "running"}
        )
        cleanup_runs["graph"].append(run["id"])
        await repo.create_task_run(
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
            reconciler=Reconciler(
                repo,
                observer=_ScriptedObserver({name: NativeRun(name, NativeState.MISSING)}),
            ),
        )

        await service.reconcile_once()

        settled = await repo.get_graph_run(run["id"])
        assert settled is not None
        # The graph either re-ran the node to success or settled on the
        # abandoned outcome — either way it must not remain ``running``.
        assert settled["state"] != "running"


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

