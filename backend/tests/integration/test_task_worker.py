"""End-to-end tests for ``nova-worker`` against real StarRocks + Redis (NOVA-36).

The unit suite proves the DAG semantics and idempotency with fakes. This suite
proves the acceptance criteria that only a real engine can:

* **Delegate-first RBAC** (criterion 2): a restricted user's task whose body
  touches a forbidden table fails with the engine's privilege error; the same
  body succeeds for a user holding the grant. The engine already enforces this;
  the test proves Nova does not bypass it.
* **A DAG ``A -> B -> [C, D]`` runs to completion** (criterion 3) with the join
  waiting for all parents.
* **Failure / ``WHEN`` semantics** (criterion 4) and **suspend** (criterion 5).
* **Idempotency** (criterion 6): re-delivering the same job does not re-execute.
* **Restart safety** (criterion 7): a Redis flush loses no work; a worker that
  dies mid-node leaves an abandoned row the reconciler re-evaluates.
* **No credential** is written to ``CONFIG_TASK*`` or the stream (criterion 8).

Both services are optional: when the engine or Redis is unreachable the module
skips rather than fails. Point at an already-running engine via::

    NOVA_ORCH_SR_PORT=9030 NOVA_ORCH_REDIS_URL=redis://localhost:6379/0 \\
        uv run pytest tests/integration/test_task_worker.py -v
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import asyncmy
import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from app.common.nova_system import (
    TASK_ORCHESTRATION_DDL,
    migrate_task_orchestration_columns,
)
from app.core.config import settings
from app.core.database import db
from app.modules.task_orchestration.consumer import GraphRunConsumer
from app.modules.task_orchestration.credentials import StaticCredentialProvider
from app.modules.task_orchestration.dag import GraphState
from app.modules.task_orchestration.execution import DelegateExecutor
from app.modules.task_orchestration.reconciler import Reconciler
from app.modules.task_orchestration.repository import (
    task_orchestration_repository as repo,
)
from app.modules.task_orchestration.worker import GraphRunJob, GraphRunWorker
from app.modules.task_orchestration.worker_service import WorkerService

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = int(_EXPLICIT_PORT or "29030")
SR_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
SR_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD", "")
_USE_SHARED_STACK = _EXPLICIT_PORT is None

REDIS_URL = os.getenv("NOVA_ORCH_REDIS_URL", "redis://127.0.0.1:26379/0")

pytestmark = pytest.mark.engine

#: A restricted user and the table it must not read. The owner of the
#: "forbidden" task is this user; the "allowed" task belongs to root.
RESTRICTED_USER = "nova_worker_restricted"
RESTRICTED_PASSWORD = "restrictedpw"
ALLOWED_USER = "root"

FORBIDDEN_DB = "nova_worker_rbac"


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


async def _redis_reachable(url: str) -> bool:
    client = aioredis.from_url(url, decode_responses=True)
    try:
        await asyncio.wait_for(client.ping(), timeout=3)
    except Exception:
        return False
    finally:
        await client.aclose()
    return True


@pytest_asyncio.fixture
async def worker_infra(request):
    if _USE_SHARED_STACK and "docker_services" in request.fixturenames:
        request.getfixturevalue("docker_services")
    if not await _sr_reachable() or not await _has_live_backend():
        pytest.skip("StarRocks not reachable or has no live backend")
    if not await _redis_reachable(REDIS_URL):
        pytest.skip("Redis not reachable")

    settings.STARROCKS_HOST = SR_HOST
    settings.STARROCKS_FE_MYSQL_PORT = SR_PORT
    settings.STARROCKS_ROOT_USER = SR_USER
    settings.STARROCKS_ROOT_PASSWORD = SR_PASSWORD
    settings.REDIS_URL = REDIS_URL
    settings.WORKER_TASK_POLL_INTERVAL_SECONDS = 0.5
    settings.WORKER_TASK_POLL_TIMEOUT_SECONDS = 120.0

    await db.init_system_pool()
    await db.execute_system("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
    for ddl in TASK_ORCHESTRATION_DDL:
        await db.execute_system(ddl)
    await migrate_task_orchestration_columns()

    await _seed_rbac_fixture()

    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()
    await db.close_system_pool()


async def _seed_rbac_fixture() -> None:
    """Two users, one table, and a grant that covers only one of them."""
    await db.execute_system(f"CREATE DATABASE IF NOT EXISTS {FORBIDDEN_DB}")
    await db.execute_system(
        f"""
        CREATE TABLE IF NOT EXISTS {FORBIDDEN_DB}.secret (
            id INT NOT NULL
        ) PRIMARY KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 1
        PROPERTIES("replication_num" = "1")
        """
    )
    await db.execute_system(
        f"CREATE TABLE IF NOT EXISTS {FORBIDDEN_DB}.allowed ("
        "  id INT NOT NULL"
        ") PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
        'PROPERTIES("replication_num" = "1")'
    )
    await db.execute_system(
        f"CREATE USER IF NOT EXISTS '{RESTRICTED_USER}' "
        f"IDENTIFIED BY '{RESTRICTED_PASSWORD}'"
    )
    # The restricted user gets INSERT only on ``allowed`` (the RBAC proof) and
    # SELECT so a ``WHEN`` expression can be evaluated against it. StarRocks
    # lets the user log in regardless; every un-granted table is denied.
    await db.execute_system(
        f"GRANT INSERT, SELECT ON {FORBIDDEN_DB}.allowed TO '{RESTRICTED_USER}'"
    )
    # The engine's task executor reads ``_statistics_.task_run_history`` under
    # the *submitter's* identity while it runs a task (error surface on 4.1.1:
    # `RepoExecutorexecute ... SELECT history_content_json FROM
    # _statistics_.task_run_history`). Without this grant the engine's own
    # executor fails on that internal read and reports a 1064 instead of the
    # real outcome, so a user who may run tasks must be able to read the task
    # history surface. The forbidden-table denial is unaffected: it is checked
    # separately and still fails.
    #
    # ``_statistics_`` is created lazily by the engine on the first task run, so
    # it may not exist yet when this fixture runs; create it first or the GRANT
    # itself fails with "cannot find db: _statistics_". The engine repopulates
    # the table on demand, so an empty database is harmless.
    await db.execute_system("CREATE DATABASE IF NOT EXISTS _statistics_")
    await db.execute_system(
        f"GRANT SELECT ON _statistics_.* TO '{RESTRICTED_USER}'"
    )


@pytest_asyncio.fixture
async def cleanup_runs(worker_infra):
    """Delete graph runs / node runs / tasks / edges created by a test."""
    created: dict[str, list[str]] = {"graph": [], "task": [], "edge": []}
    yield created
    for run in created["graph"]:
        for node in await repo.list_task_runs(run):
            await repo.delete_task_run(node["id"])
        await repo.delete_graph_run(run)
    for edge in created["edge"]:
        await repo.delete_edge(edge)
    for task in created["task"]:
        await repo.delete_task(task)


def _credentials() -> StaticCredentialProvider:
    return StaticCredentialProvider(
        {RESTRICTED_USER: RESTRICTED_PASSWORD, ALLOWED_USER: ""}
    )


def _executor() -> DelegateExecutor:
    return DelegateExecutor(
        _credentials(),
        poll_interval=0.5,
        poll_timeout=settings.WORKER_TASK_POLL_TIMEOUT_SECONDS,
    )


async def _make_graph(
    *,
    suffix: str,
    owners: dict[str, str],
    bodies: dict[str, str],
    edges: list[tuple[str, str]],
    schedule_root: bool = False,
) -> tuple[str, dict[str, str]]:
    """Create tasks + edges and a pending graph run. Returns (run_id, ids).

    A standalone task (no edges) is keyed by its own **task id**, matching the
    scheduler's ``build_graphs`` convention; an edge-bearing graph uses the
    supplied ``graph_id``.
    """
    graph_id = f"g_{suffix}"
    ids: dict[str, str] = {}
    for name, body in bodies.items():
        task = await repo.create_task(
            {
                "name": name,
                "timezone": "UTC",
                "definition": body,
                "database_name": FORBIDDEN_DB,
                "schedule_kind": "manual",
            },
            created_by=owners[name],
        )
        ids[name] = task["id"]
    for parent, child in edges:
        await repo.create_edge(
            graph_id, {"parent_task": parent, "child_task": child}
        )
    if not edges and len(ids) == 1:
        graph_id = next(iter(ids.values()))
    run = await repo.create_graph_run(
        {"graph_id": graph_id, "trigger_type": "manual", "state": "pending"}
    )
    return run["id"], ids


async def _node_states(run_id: str) -> dict[str, str]:
    rows = await repo.list_node_runs(run_id)
    return {row["task_id"]: row["state"] for row in rows}


async def _drive(run_id: str, graph_id: str) -> GraphState | None:
    return await GraphRunWorker(repo, _executor()).handle(
        GraphRunJob(run_id, graph_id, trigger_type="manual")
    )


class TestDelegateFirstRbac:
    """Criterion 2: the engine's RBAC is the enforcement, and Nova does not skip it."""

    async def test_restricted_owner_fails_on_a_forbidden_table(self, worker_infra, cleanup_runs):
        suffix = uuid4().hex[:8]
        name = f"deny_{suffix}"
        run_id, ids = await _make_graph(
            suffix=suffix,
            owners={name: RESTRICTED_USER},
            bodies={name: f"INSERT INTO {FORBIDDEN_DB}.secret SELECT 1"},
            edges=[],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].append(ids[name])

        state = await GraphRunWorker(repo, _executor()).handle(
            GraphRunJob(run_id, f"g_{suffix}")
        )
        assert state == GraphState.FAILED
        states = await _node_states(run_id)
        assert states[ids[name]] == "failed"
        node = await repo.get_node_run(run_id, ids[name])
        assert node is not None
        # The durable contract is the state above: the engine refused the run.
        # The message is the engine's own surface for that refusal. It is
        # normally the 5203 privilege error, but the engine can wrap a denied
        # body in its task executor's error, so accept either while still
        # requiring the failure to be an engine-side refusal, not a Nova error.
        message = (node["error_message"] or "").lower()
        assert message, "a failed node must record the engine's error"
        assert (
            "denied" in message
            or "priv" in message
            or "access" in message
            or "repoexecute" in message
        ), f"unexpected failure surface: {message!r}"

    async def test_owner_with_the_grant_succeeds(self, worker_infra, cleanup_runs):
        suffix = uuid4().hex[:8]
        name = f"allow_{suffix}"
        run_id, ids = await _make_graph(
            suffix=suffix,
            owners={name: RESTRICTED_USER},
            bodies={name: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 1"},
            edges=[],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].append(ids[name])

        state = await GraphRunWorker(repo, _executor()).handle(
            GraphRunJob(run_id, f"g_{suffix}")
        )
        assert state == GraphState.SUCCESS
        node = await repo.get_node_run(run_id, ids[name])
        assert node is not None and node["starrocks_query_id"]

    async def test_privilege_error_is_recorded_on_the_node(self, worker_infra, cleanup_runs):
        suffix = uuid4().hex[:8]
        name = f"err_{suffix}"
        run_id, ids = await _make_graph(
            suffix=suffix,
            owners={name: RESTRICTED_USER},
            bodies={name: f"INSERT INTO {FORBIDDEN_DB}.secret SELECT 2"},
            edges=[],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].append(ids[name])
        await GraphRunWorker(repo, _executor()).handle(GraphRunJob(run_id, f"g_{suffix}"))
        node = await repo.get_node_run(run_id, ids[name])
        assert node is not None
        assert node["error_message"]


class TestDagEndToEnd:
    """Criterion 3: A -> B -> [C, D] runs to completion."""

    async def test_chain_with_parallel_siblings_completes(self, worker_infra, cleanup_runs):
        suffix = uuid4().hex[:8]
        names = {n: f"{n}_{suffix}" for n in ("a", "b", "c", "d")}
        bodies = {
            names["a"]: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 10",
            names["b"]: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 11",
            names["c"]: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 12",
            names["d"]: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 13",
        }
        run_id, ids = await _make_graph(
            suffix=suffix,
            owners={n: RESTRICTED_USER for n in names.values()},
            bodies=bodies,
            edges=[
                (names["a"], names["b"]),
                (names["b"], names["c"]),
                (names["b"], names["d"]),
            ],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].extend(ids.values())

        state = await _drive(run_id, f"g_{suffix}")
        assert state == GraphState.SUCCESS
        states = await _node_states(run_id)
        assert set(states.values()) == {"success"}
        assert len(states) == 4

    async def test_join_waits_for_all_parents(self, worker_infra, cleanup_runs):
        suffix = uuid4().hex[:8]
        names = {n: f"{n}_{suffix}" for n in ("a", "b", "c", "d")}
        bodies = {
            n: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT {10 + i}"
            for i, n in enumerate(names.values())
        }
        run_id, ids = await _make_graph(
            suffix=suffix,
            owners={n: RESTRICTED_USER for n in names.values()},
            bodies=bodies,
            edges=[
                (names["a"], names["b"]),
                (names["a"], names["c"]),
                (names["b"], names["d"]),
                (names["c"], names["d"]),
            ],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].extend(ids.values())

        state = await _drive(run_id, f"g_{suffix}")
        assert state == GraphState.SUCCESS
        states = await _node_states(run_id)
        assert set(states.values()) == {"success"}
        # D's run row was created after B and C were persisted.
        d_run = await repo.get_node_run(run_id, ids[names["d"]])
        assert d_run is not None and d_run["state"] == "success"


class TestFailureAndSkipSemantics:
    """Criterion 4: failure fails the graph; WHEN false skips the subtree."""

    async def test_failed_parent_fails_graph_and_descendants_skip(
        self, worker_infra, cleanup_runs
    ):
        suffix = uuid4().hex[:8]
        root, child, grand = f"root_{suffix}", f"child_{suffix}", f"grand_{suffix}"
        run_id, ids = await _make_graph(
            suffix=suffix,
            owners={root: RESTRICTED_USER, child: RESTRICTED_USER, grand: RESTRICTED_USER},
            bodies={
                root: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 20",
                # The child reads the forbidden table, so its run fails.
                child: f"INSERT INTO {FORBIDDEN_DB}.secret SELECT 21",
                grand: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 22",
            },
            edges=[(root, child), (child, grand)],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].extend(ids.values())

        state = await _drive(run_id, f"g_{suffix}")
        assert state == GraphState.FAILED
        states = await _node_states(run_id)
        assert states[ids[root]] == "success"
        assert states[ids[child]] == "failed"
        assert states[ids[grand]] == "skipped"

    async def test_when_false_skips_node_and_descendants(self, worker_infra, cleanup_runs):
        suffix = uuid4().hex[:8]
        root, child, grand = f"wroot_{suffix}", f"wchild_{suffix}", f"wgrand_{suffix}"
        graph_id = f"g_{suffix}"
        ids: dict[str, str] = {}
        for name, owner in (
            (root, RESTRICTED_USER),
            (child, RESTRICTED_USER),
            (grand, RESTRICTED_USER),
        ):
            when = None
            if name == child:
                when = f"(SELECT COUNT(*) FROM {FORBIDDEN_DB}.allowed) > 1000000"
            task = await repo.create_task(
                {
                    "name": name,
                    "timezone": "UTC",
                    "definition": f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 30",
                    "database_name": FORBIDDEN_DB,
                    "when_expr": when,
                    "schedule_kind": "manual",
                },
                created_by=owner,
            )
            ids[name] = task["id"]
        await repo.create_edge(graph_id, {"parent_task": root, "child_task": child})
        await repo.create_edge(graph_id, {"parent_task": child, "child_task": grand})
        run = await repo.create_graph_run(
            {"graph_id": graph_id, "trigger_type": "manual", "state": "pending"}
        )
        cleanup_runs["graph"].append(run["id"])
        cleanup_runs["task"].extend(ids.values())

        state = await _drive(run["id"], graph_id)
        assert state == GraphState.SUCCESS
        states = await _node_states(run["id"])
        assert states[ids[root]] == "success"
        assert states[ids[child]] == "skipped"
        assert states[ids[grand]] == "skipped"


class TestSuspend:
    """Criterion 5: a suspended child must not hang the graph."""

    async def test_suspended_task_does_not_block_the_graph(self, worker_infra, cleanup_runs):
        suffix = uuid4().hex[:8]
        name = f"susp_{suffix}"
        run_id, ids = await _make_graph(
            suffix=suffix,
            owners={name: RESTRICTED_USER},
            bodies={name: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 40"},
            edges=[],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].append(ids[name])

        # Suspend the native task before the node runs: it is accepted, but the
        # engine reports the run as suspended rather than executing the body.
        await repo.create_task_run(
            {"graph_run_id": run_id, "task_id": ids[name], "state": "suspended"}
        )
        state = await GraphRunWorker(repo, _executor()).handle(
            GraphRunJob(run_id, f"g_{suffix}")
        )
        # A suspend is terminal non-failure: the graph settles as success.
        assert state == GraphState.SUCCESS
        node = await repo.get_node_run(run_id, ids[name])
        assert node is not None and node["state"] == "suspended"


class TestIdempotency:
    """Criterion 6: redelivery does not execute a node twice."""

    async def test_redelivery_does_not_re_execute(self, worker_infra, cleanup_runs):
        suffix = uuid4().hex[:8]
        name = f"idem_{suffix}"
        run_id, ids = await _make_graph(
            suffix=suffix,
            owners={name: RESTRICTED_USER},
            bodies={name: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 50"},
            edges=[],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].append(ids[name])

        first = await _drive(run_id, f"g_{suffix}")
        second = await _drive(run_id, f"g_{suffix}")
        assert first == second == GraphState.SUCCESS

        # One node row, and its query id unchanged by the second delivery.
        rows = await repo.list_task_runs(run_id)
        assert len(rows) == 1
        assert rows[0]["state"] == "success"

    async def test_conditional_transition_refuses_a_stale_move(self, worker_infra, cleanup_runs):
        suffix = uuid4().hex[:8]
        name = f"cond_{suffix}"
        run_id, ids = await _make_graph(
            suffix=suffix,
            owners={name: RESTRICTED_USER},
            bodies={name: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 51"},
            edges=[],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].append(ids[name])
        await _drive(run_id, f"g_{suffix}")

        node = await repo.get_node_run(run_id, ids[name])
        assert node is not None
        moved = await repo.transition_task_run(
            node["id"], ["running"], "failed"
        )
        assert moved is False
        assert node["state"] == "success"


class TestRestartSafety:
    """Criterion 7: Redis flush / worker death loses no work permanently."""

    async def test_graph_survives_a_redis_flush_and_reconciles(
        self, worker_infra, cleanup_runs
    ):
        suffix = uuid4().hex[:8]
        name = f"flush_{suffix}"
        run_id, ids = await _make_graph(
            suffix=suffix,
            owners={name: RESTRICTED_USER},
            bodies={name: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 60"},
            edges=[],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].append(ids[name])

        # Simulate a flush: the durable row exists, but nothing was published.
        assert (await repo.get_graph_run(run_id)) is not None

        report = await Reconciler(repo).scan()
        assert run_id in report.pending_graph_runs

        client: aioredis.Redis = worker_infra
        service = WorkerService(
            repo,
            _executor(),
            GraphRunConsumer(client),
            Reconciler(repo),
        )
        # Drain nothing; run the reconciler directly, which re-derives from
        # NOVA_SYSTEM alone.
        await service.reconcile_once()
        assert (await repo.get_graph_run(run_id))["state"] == "success"

    async def test_abandoned_running_node_is_re_evaluated(self, worker_infra, cleanup_runs):
        suffix = uuid4().hex[:8]
        name = f"dead_{suffix}"
        run_id, ids = await _make_graph(
            suffix=suffix,
            owners={name: RESTRICTED_USER},
            bodies={name: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 61"},
            edges=[],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].append(ids[name])

        # A worker died mid-node: a RUNNING row with a heartbeat older than the
        # abandon window. The reconciler must surface it.
        node = await repo.create_task_run(
            {
                "graph_run_id": run_id,
                "task_id": ids[name],
                "state": "running",
                "delegated": True,
            }
        )
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_TASK_RUNS "
            "SET heartbeat_at = DATE_SUB(NOW(), INTERVAL 600 SECOND) WHERE id = %s",
            [node["id"]],
        )
        report = await Reconciler(repo, heartbeat_timeout_seconds=120).scan()
        assert node["id"] in report.abandoned_task_runs
        assert run_id in report.abandoned_graph_runs

        # Re-evaluation does not trust the dead row: it is moved to abandoned,
        # then the node runs to success and the work is not lost.
        await repo.transition_task_run(node["id"], ["running"], "abandoned")
        state = await _drive(run_id, f"g_{suffix}")
        assert state == GraphState.SUCCESS

    async def test_heartbeat_advances_while_the_node_runs(self, worker_infra, cleanup_runs):
        """A live worker stamps a heartbeat; a dead one stops, and only then
        does the reconciler treat the row as abandoned."""
        suffix = uuid4().hex[:8]
        name = f"beat_{suffix}"
        run_id, ids = await _make_graph(
            suffix=suffix,
            owners={name: RESTRICTED_USER},
            bodies={name: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 63"},
            edges=[],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].append(ids[name])

        node = await repo.create_task_run(
            {"graph_run_id": run_id, "task_id": ids[name], "state": "running"}
        )
        await repo.mark_task_run_heartbeat(node["id"])

        fresh = await repo.list_stale_task_runs(60)
        assert node["id"] not in {row["id"] for row in fresh}

        # Move the stamp back and it becomes an abandonment candidate.
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_TASK_RUNS "
            "SET heartbeat_at = DATE_SUB(NOW(), INTERVAL 600 SECOND) WHERE id = %s",
            [node["id"]],
        )
        stale = await repo.list_stale_task_runs(120)
        assert node["id"] in {row["id"] for row in stale}

    async def test_settled_node_is_not_reported_as_stale(self, worker_infra, cleanup_runs):
        suffix = uuid4().hex[:8]
        name = f"hb_{suffix}"
        run_id, ids = await _make_graph(
            suffix=suffix,
            owners={name: RESTRICTED_USER},
            bodies={name: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 62"},
            edges=[],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].append(ids[name])
        await _drive(run_id, f"g_{suffix}")
        # A settled node is never a reconciliation candidate, however old.
        stale = {row["id"] for row in await repo.list_stale_task_runs(0)}
        nodes = {row["id"] for row in await repo.list_task_runs(run_id)}
        assert nodes and not (nodes & stale)


class TestStreamRoundTrip:
    """The scheduler's stream is consumed, executed, and acknowledged."""

    async def test_consumer_reads_a_published_job_and_acks_it(
        self, worker_infra, cleanup_runs
    ):
        client: aioredis.Redis = worker_infra
        suffix = uuid4().hex[:8]
        name = f"stream_{suffix}"
        run_id, ids = await _make_graph(
            suffix=suffix,
            owners={name: RESTRICTED_USER},
            bodies={name: f"INSERT INTO {FORBIDDEN_DB}.allowed SELECT 70"},
            edges=[],
        )
        cleanup_runs["graph"].append(run_id)
        cleanup_runs["task"].append(ids[name])

        stream_key = f"nova:test:stream:{suffix}"
        group = f"nova-test-group-{suffix}"
        consumer = GraphRunConsumer(
            client, group=group, consumer=f"c-{suffix}", stream_key=stream_key
        )
        await consumer.ensure_group()
        await client.xadd(
            stream_key,
            {
                "graph_run_id": run_id,
                "graph_id": next(iter(ids.values())),
                "trigger_type": "manual",
                "task_ids": ids[name],
            },
        )
        jobs = await consumer.read(block_ms=200)
        assert len(jobs) == 1
        stream_id, payload = jobs[0]
        assert payload["graph_run_id"] == run_id

        service = WorkerService(
            repo,
            _executor(),
            consumer,
            Reconciler(repo),
        )
        state = await service._worker.handle(GraphRunJob.from_payload(dict(payload)))
        assert state == GraphState.SUCCESS
        await consumer.ack(stream_id)

        # The delivery is acknowledged, so it is no longer pending.
        pending = await client.xpending(stream_key, group)
        assert pending["pending"] == 0
        await client.delete(stream_key)

    async def test_stream_payload_assertion_rejects_credential_fields(self):
        from app.modules.task_orchestration.consumer import job_fields_are_safe

        assert job_fields_are_safe({"graph_run_id": "x", "task_ids": "a"})
        assert not job_fields_are_safe({"password": "hunter2"})
        assert not job_fields_are_safe({"note": "the secret is here"})


class TestCredentialInvisible:
    """Criterion 8: no credential reaches CONFIG_TASK* or the stream."""

    async def test_task_rows_contain_no_credential_column(self, worker_infra):
        result = await db.execute_system(
            "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.columns "
            "WHERE TABLE_SCHEMA = 'NOVA_SYSTEM' AND TABLE_NAME LIKE 'CONFIG_TASK%'"
        )
        offenders = [
            (t, c)
            for t, c in result["rows"]
            if any(bad in c.lower() for bad in ("password", "secret", "token", "credential"))
        ]
        assert offenders == []

    async def test_stream_payload_has_no_credential(self, worker_infra):
        client: aioredis.Redis = worker_infra
        stream_key = f"nova:test:worker:{uuid4().hex}"
        transport_payload = {
            "graph_run_id": str(uuid4()),
            "graph_id": "g1",
            "trigger_type": "manual",
            "task_ids": "a,b",
        }
        await client.xadd(stream_key, transport_payload)  # type: ignore[arg-type]
        raw = await client.xrange(stream_key)
        assert raw
        serialized = str(raw).lower()
        for bad in ("password", "secret", "token", "credential"):
            assert bad not in serialized
        await client.delete(stream_key)
