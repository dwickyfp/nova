"""End-to-end native task lifecycle against a real StarRocks engine.

The Nova ``CREATE TASK`` surface is metadata only (see
``test_task_orchestration_metadata.py``). This module exercises the **native**
engine surface — ``SUBMIT TASK`` / ``ALTER TASK … SUSPEND|RESUME`` / ``DROP
TASK`` — through ``TaskService`` on a real connection, so the whole lifecycle a
user drives from the Task Manager UI is proven against the engine, not a fake.

It exists because the native path is easy to break in a way unit tests miss:
``ALTER``/``DROP`` and an unqualified ``SUBMIT`` body need a **session
database**, which is a runtime connection concern, not a string-assembly one.

Runs against a real StarRocks. Point at a running engine with::

    NOVA_ORCH_SR_PORT=9030 uv run pytest tests/integration/test_tasks_native_lifecycle.py

When StarRocks is unreachable the module skips rather than fails.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from uuid import uuid4

import asyncmy
import pytest
import pytest_asyncio

from app.modules.tasks.service import task_service
from tests.integration._stack import engine_port, require_shared_stack

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = engine_port(_EXPLICIT_PORT, "NOVA_TEST_FE_MYSQL_PORT", 29030)
SR_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
SR_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD", "")
_USE_SHARED_STACK = _EXPLICIT_PORT is None

pytestmark = pytest.mark.engine


async def _reachable() -> bool:
    try:
        conn = await asyncio.wait_for(
            asyncmy.connect(host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD),
            timeout=5,
        )
    except Exception:
        return False
    conn.close()
    return True


async def _admin_execute(sql: str) -> None:
    conn = await asyncmy.connect(
        host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
    finally:
        conn.close()


@pytest_asyncio.fixture
async def engine(request):
    require_shared_stack(request, enabled=_USE_SHARED_STACK)
    if not await _reachable():
        pytest.skip("StarRocks not reachable")

    suffix = uuid4().hex[:8]
    database = f"nova_native_{suffix}"
    await _admin_execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
    await _admin_execute(
        f"CREATE TABLE IF NOT EXISTS `{database}`.`t` (id INT) "
        'PROPERTIES("replication_num"="1")'
    )
    yield database
    with contextlib.suppress(Exception):
        await _admin_execute(f"DROP DATABASE IF EXISTS `{database}`")


async def _user_conn(database: str | None = None):
    """A fresh connection, like ``get_user_connection`` opens per request."""
    return await asyncmy.connect(
        host=SR_HOST,
        port=SR_PORT,
        user=SR_USER,
        password=SR_PASSWORD,
        database=database,
        autocommit=True,
    )


async def _task_exists(database: str, name: str) -> bool:
    conn = await _user_conn(database)
    try:
        async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
            await cur.execute(
                "SELECT TASK_NAME FROM information_schema.tasks WHERE TASK_NAME = %s",
                (name,),
            )
            return await cur.fetchone() is not None
    finally:
        conn.close()


class TestNativeTaskLifecycle:
    async def test_submit_alter_drop_round_trip(self, engine):
        """SUBMIT → SUSPEND → RESUME → DROP, all on a caller connection.

        The submit body is **unqualified** (``INSERT INTO t``) and the connection
        has no default database, so this only succeeds if the service switches to
        the task's database first — the regression this test guards.
        """
        database = engine
        name = f"etl_{uuid4().hex[:6]}"
        conn = await _user_conn()  # no default database
        try:
            created = await task_service.create_task(
                conn,
                {
                    "name": name,
                    "database": database,
                    "sql": "INSERT INTO t SELECT 1",
                    "schedule_type": "once",
                },
            )
            assert created["success"] is True
            assert created["sql"].startswith("SUBMIT TASK")
            assert await _task_exists(database, name)

            suspended = await task_service.suspend_task(conn, name, database=database)
            assert suspended["action"] == "suspended"

            resumed = await task_service.resume_task(conn, name, database=database)
            assert resumed["action"] == "resumed"

            dropped = await task_service.drop_task(conn, name, database=database)
            assert dropped["action"] == "dropped"
            assert not await _task_exists(database, name)
        finally:
            conn.close()

    async def test_unqualified_body_without_use_is_rejected_by_the_engine(self, engine):
        """Pins the reason the service switches database: a bare submit fails.

        Executing ``SUBMIT TASK`` directly on a connection with no default
        database and an unqualified body raises ``No database selected``. The
        service must therefore run ``USE`` first (asserted by the round trip
        above); this documents the engine behaviour the fix is built on.
        """
        database = engine
        name = f"etl_{uuid4().hex[:6]}"
        conn = await _user_conn()  # no default database
        try:
            with pytest.raises(Exception, match="database"):
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"SUBMIT TASK `{database}`.`{name}` AS INSERT INTO t SELECT 1"
                    )
        finally:
            conn.close()

    async def test_periodic_task_schedules_and_suspends(self, engine):
        """A periodic task is created with a schedule and can be paused."""
        database = engine
        name = f"hourly_{uuid4().hex[:6]}"
        conn = await _user_conn()
        try:
            created = await task_service.create_task(
                conn,
                {
                    "name": name,
                    "database": database,
                    "sql": "INSERT INTO t SELECT 1",
                    "schedule_type": "periodic",
                    "interval": "1 HOUR",
                },
            )
            assert "EVERY(INTERVAL 1 HOUR)" in created["sql"]
            assert await _task_exists(database, name)

            tasks = await task_service.list_tasks(conn)
            assert any(t.name == name for t in tasks)

            await task_service.drop_task(conn, name, database=database)
        finally:
            conn.close()
