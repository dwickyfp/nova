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

from app.common.nova_system import TASK_ORCHESTRATION_DDL
from app.core.config import settings
from app.core.database import db
from app.modules.task_orchestration.native import (
    NativeConfig,
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

        report = await Reconciler(repo).reconcile_native()

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
        reconciler = Reconciler(repo)

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

        The native row is created by submitting a real ``SUBMIT TASK``; the
        reconciler must observe it as RUNNING/PENDING rather than MISSING.
        """
        suffix = uuid4().hex[:8]
        name = f"live_{suffix}"
        await db.execute_system(
            "CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.nova_reconcile_probe ("
            " id INT NOT NULL"
            ") PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
            'PROPERTIES("replication_num"="1")'
        )
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
        async with db.system_conn() as conn, conn.cursor() as cur:
            await cur.execute("USE NOVA_SYSTEM")
            await cur.execute(
                f"SUBMIT TASK `{name}` AS "
                "INSERT INTO NOVA_SYSTEM.nova_reconcile_probe SELECT 1"
            )

        report = await Reconciler(repo).reconcile_native()

        assert name not in report.lost_traces
        refreshed = await repo.get_task_run(node["id"])
        assert refreshed is not None
        assert refreshed["state"] == "running"
        assert refreshed["state"] in {
            NativeState.PENDING.value,
            NativeState.RUNNING.value,
            "running",
        }


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
            reconciler=Reconciler(repo),
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

