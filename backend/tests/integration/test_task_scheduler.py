"""Integration tests for nova-scheduler against real Redis + StarRocks (NOVA-35).

The unit suite proves the ordering and idempotency with fakes. This suite proves
the same properties against the real transports they will run on: a Real Redis
Stream must receive the job **after** the row exists in ``NOVA_SYSTEM``, a
second tick must not add a second stream entry, and a real ``SET NX`` leader lock
must keep a second instance from enqueueing.

Both services are optional: when Redis or StarRocks is unreachable the module
skips rather than fails, because a missing local prerequisite is not a
regression. Point at an already-running engine instead of the shared stack via::

    NOVA_ORCH_SR_PORT=9030 NOVA_ORCH_REDIS_URL=redis://localhost:6379/0 \\
        uv run pytest tests/integration/test_task_scheduler.py
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncmy
import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from app.common.nova_system import TASK_ORCHESTRATION_DDL
from app.core.config import settings
from app.core.database import db
from app.modules.task_orchestration.repository import (
    task_orchestration_repository as repo,
)
from app.modules.task_orchestration.schedule import resolve_timezone
from app.modules.task_orchestration.scheduler import SchedulerTick
from app.modules.task_orchestration.transport import (
    LeaderLock,
    RedisGraphRunTransport,
)
from tests.integration._stack import require_shared_stack

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = int(_EXPLICIT_PORT or "29030")
SR_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
SR_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD", "")
_USE_SHARED_STACK = _EXPLICIT_PORT is None

REDIS_URL = os.getenv("NOVA_ORCH_REDIS_URL", "redis://127.0.0.1:26379/0")


async def _sr_reachable() -> bool:
    try:
        conn = await asyncio.wait_for(
            asyncmy.connect(host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD),
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
async def scheduler_infra(request, monkeypatch: pytest.MonkeyPatch):
    require_shared_stack(request, enabled=_USE_SHARED_STACK)
    if not await _sr_reachable() or not await _has_live_backend():
        pytest.skip("StarRocks not reachable or has no live backend")
    if not await _redis_reachable(REDIS_URL):
        pytest.skip("Redis not reachable")

    monkeypatch.setattr(settings, "STARROCKS_HOST", SR_HOST)
    monkeypatch.setattr(settings, "STARROCKS_FE_MYSQL_PORT", SR_PORT)
    monkeypatch.setattr(settings, "STARROCKS_ROOT_USER", SR_USER)
    monkeypatch.setattr(settings, "STARROCKS_ROOT_PASSWORD", SR_PASSWORD)
    monkeypatch.setattr(settings, "REDIS_URL", REDIS_URL)

    await db.init_system_pool()
    await db.execute_system("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
    for ddl in TASK_ORCHESTRATION_DDL:
        await db.execute_system(ddl)

    # Pin the scheduler's engine-timezone setting to what the engine actually
    # reports, so a stale/incorrect default in config cannot mask a real bug.
    # An explicit env override is honoured for deployments that need it.
    #
    # ``monkeypatch`` (not a bare assignment): this used to write the detected
    # zone straight onto the module-level settings object and never restore it,
    # so a full-suite run left ``SCHEDULER_ENGINE_TIMEZONE`` set for every test
    # that ran afterwards. ``tests/unit/test_task_scheduler_tick.py::``
    # ``test_offset_engine_zone_still_fires`` sets it to ``""`` to force the
    # auto-detect path, and the leaked value made it fail only in a full run.
    detected = await repo.get_engine_timezone()
    monkeypatch.setattr(
        settings,
        "SCHEDULER_ENGINE_TIMEZONE",
        os.getenv("SCHEDULER_ENGINE_TIMEZONE", detected or "UTC"),
    )

    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()
    await db.close_system_pool()


@pytest_asyncio.fixture
async def clean_stream(scheduler_infra):
    """A unique stream key per test, so tests cannot see each other's jobs."""
    client: aioredis.Redis = scheduler_infra
    stream_key = f"nova:test:graph_runs:{uuid4().hex}"
    settings.TASK_STREAM_KEY = stream_key
    yield stream_key
    await client.delete(stream_key)


async def _seed_interval_task(name: str, *, minutes: int = 5):
    return await repo.create_task(
        {
            "name": name,
            "timezone": "UTC",
            "schedule_kind": "interval",
            "schedule_expr": f"EVERY(INTERVAL {minutes} MINUTE)",
        },
        created_by="scheduler-test",
    )


async def _cleanup_task(task: dict) -> None:
    await repo.delete_task(task["id"])


def _after_first_interval(minutes: int = 5) -> datetime:
    """A tick time that is past the seeded task's first interval fire.

    Interval tasks fire one interval after creation, so a tick at "now" would
    correctly find nothing due. Advance past the first occurrence instead.
    """
    return datetime.now(UTC) + timedelta(minutes=minutes + 1)


async def _entries_for_graph(client: aioredis.Redis, stream_key: str, graph_id: str):
    """Stream entries whose payload names ``graph_id``.

    The test database is shared, so other due tasks may also publish during a
    tick; assertions must look only at the graph under test.
    """
    entries = await client.xrange(stream_key)
    return [(sid, fields) for sid, fields in entries if fields.get("graph_id") == graph_id]


class TestRealTransportEndToEnd:
    async def test_due_task_persists_row_then_pushes_to_stream(
        self, scheduler_infra, clean_stream
    ):
        client: aioredis.Redis = scheduler_infra
        name = f"e2e_{uuid4().hex[:8]}"
        task = await _seed_interval_task(name)
        try:
            transport = RedisGraphRunTransport(client)
            tick = SchedulerTick(repo, transport)

            now = _after_first_interval()
            await tick.tick(now)

            entries = await _entries_for_graph(client, clean_stream, task["id"])
            assert len(entries) == 1
            _, fields = entries[0]
            run_id = fields["graph_run_id"]

            row = await repo.get_graph_run(run_id)
            assert row is not None, "stream job must reference a persisted graph run"
            assert row["graph_id"] == task["id"]
            assert fields["task_ids"] == task["id"]
        finally:
            runs = await repo.list_graph_runs(task["id"])
            for run in runs:
                await repo.delete_graph_run(run["id"])
            await _cleanup_task(task)

    async def test_second_tick_does_not_push_a_duplicate(self, scheduler_infra, clean_stream):
        client: aioredis.Redis = scheduler_infra
        name = f"idem_{uuid4().hex[:8]}"
        task = await _seed_interval_task(name)
        try:
            tick = SchedulerTick(repo, RedisGraphRunTransport(client))
            now = _after_first_interval()
            await tick.tick(now)
            await tick.tick(now)

            entries = await _entries_for_graph(client, clean_stream, task["id"])
            assert len(entries) == 1
        finally:
            runs = await repo.list_graph_runs(task["id"])
            assert len(runs) == 1, "one due-time must produce exactly one graph run"
            for run in runs:
                await repo.delete_graph_run(run["id"])
            await _cleanup_task(task)

    async def test_dag_root_produces_one_run_covering_all_nodes(
        self, scheduler_infra, clean_stream
    ):
        client: aioredis.Redis = scheduler_infra
        suffix = uuid4().hex[:8]
        graph_id = f"g_{suffix}"
        tasks = [
            await _seed_interval_task(f"a_{suffix}"),
            await repo.create_task(
                {"name": f"b_{suffix}", "timezone": "UTC", "schedule_kind": "manual"},
                created_by="scheduler-test",
            ),
            await repo.create_task(
                {"name": f"c_{suffix}", "timezone": "UTC", "schedule_kind": "manual"},
                created_by="scheduler-test",
            ),
            await repo.create_task(
                {"name": f"d_{suffix}", "timezone": "UTC", "schedule_kind": "manual"},
                created_by="scheduler-test",
            ),
        ]
        edges = [
            await repo.create_edge(
                graph_id, {"parent_task": f"a_{suffix}", "child_task": f"b_{suffix}"}
            ),
            await repo.create_edge(
                graph_id, {"parent_task": f"b_{suffix}", "child_task": f"c_{suffix}"}
            ),
            await repo.create_edge(
                graph_id, {"parent_task": f"b_{suffix}", "child_task": f"d_{suffix}"}
            ),
        ]
        try:
            await SchedulerTick(repo, RedisGraphRunTransport(client)).tick(
                _after_first_interval()
            )
            entries = await _entries_for_graph(client, clean_stream, graph_id)
            assert len(entries) == 1
            run_id = entries[0][1]["graph_run_id"]
            row = await repo.get_graph_run(run_id)
            assert row is not None and row["graph_id"] == graph_id
            assert set(entries[0][1]["task_ids"].split(",")) == {t["id"] for t in tasks}
        finally:
            for edge in edges:
                await repo.delete_edge(edge["id"])
            for task in tasks:
                for run in await repo.list_graph_runs(task["id"]):
                    await repo.delete_graph_run(run["id"])
                await _cleanup_task(task)

    async def test_real_leader_lock_serialises_two_instances(self, scheduler_infra):
        client: aioredis.Redis = scheduler_infra
        key = f"nova:test:leader:{uuid4().hex}"
        first = LeaderLock(client, key=key, ttl_seconds=30)
        second = LeaderLock(client, key=key, ttl_seconds=30)
        try:
            assert await first.acquire() is True
            assert await second.acquire() is False
            await first.release()
            assert await second.acquire() is True
        finally:
            await first.release()
            await second.release()
            await client.delete(key)

    async def test_stream_payload_has_no_credential(self, scheduler_infra, clean_stream):
        client: aioredis.Redis = scheduler_infra
        name = f"cred_{uuid4().hex[:8]}"
        task = await _seed_interval_task(name)
        try:
            await SchedulerTick(repo, RedisGraphRunTransport(client)).tick(
                _after_first_interval()
            )
            entries = await _entries_for_graph(client, clean_stream, task["id"])
            assert entries
            serialized = str(entries[0][1]).lower()
            for bad in ("password", "secret", "token", "credential"):
                assert bad not in serialized
        finally:
            for run in await repo.list_graph_runs(task["id"]):
                await repo.delete_graph_run(run["id"])
            await _cleanup_task(task)


class TestEngineTimezoneFromEngine:
    """NOVA-39: the shipped default (auto-detect) must fire on a non-UTC engine.

    QA found the integration suite passing only because it happened to run
    against a UTC engine while the configured default was UTC. These tests force
    the auto-detect path (``SCHEDULER_ENGINE_TIMEZONE=""``) against the engine
    that is actually running, and assert the scheduler reads its zone from
    ``SELECT @@time_zone`` rather than assuming UTC.
    """

    async def test_reads_a_real_timezone_from_the_engine(self, scheduler_infra):
        detected = await repo.get_engine_timezone()
        assert detected, "engine did not report a session timezone"

        # StarRocks may report an IANA key (Asia/Jakarta) or an offset (+07:00);
        # both must resolve. ZoneInfo alone only handles the IANA form, which is
        # the assumption that made the offset engine a per-tick failure.
        assert resolve_timezone(detected) is not None

    async def test_interval_fires_with_autodetected_engine_zone(
        self, scheduler_infra, clean_stream, monkeypatch
    ):
        client: aioredis.Redis = scheduler_infra
        detected = await repo.get_engine_timezone()
        monkeypatch.setattr(settings, "SCHEDULER_ENGINE_TIMEZONE", "")

        name = f"autoz_{uuid4().hex[:8]}"
        task = await _seed_interval_task(name)
        try:
            plan = await SchedulerTick(repo, RedisGraphRunTransport(client)).tick(
                _after_first_interval()
            )
            entries = await _entries_for_graph(client, clean_stream, task["id"])
            assert len(entries) == 1, (
                f"interval task did not fire with engine tz {detected!r} "
                f"(due={len(plan.due)})"
            )
            assert (await repo.get_graph_run(entries[0][1]["graph_run_id"])) is not None
        finally:
            for run in await repo.list_graph_runs(task["id"]):
                await repo.delete_graph_run(run["id"])
            await _cleanup_task(task)
