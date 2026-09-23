"""Engine-integrated benchmark for ``query_execute`` (NOVA-92, deliverable 3).

Measures a read-only ``SELECT`` round-trip through the real delegate-first path
— ``QueryExecuteTool.run`` → ``QueryService.execute_statements`` → StarRocks —
against this repository's stack from ``docker-compose.test.yml``.

This module is marked ``@pytest.mark.engine`` and **skips cleanly** whenever the
full runnable path is not available, so ``uv run pytest`` stays green on a
machine without the stack (including CI). It never fakes an engine number: if
the stack is present *and* the app is bootstrapped it reports a real
measurement; otherwise it skips without a value.

Two conditions must both hold before the test runs, because satisfying only one
is what made an earlier revision fail instead of skip:

1. **The stack on the port must be ours.** A TCP port that answers proves
   nothing — a foreign Compose project (another worktree, a QA sandbox) can
   publish the same port. The guard matches the container's
   ``com.docker.compose.project.working_dir`` label against this checkout, so a
   stranger's stack yields a skip, not a result measured against the wrong
   engine.
2. **The app system pool must be bootstrapped.** ``QueryExecuteTool.run`` writes
   an audit row through ``db.execute_system``, which raises
   ``System pool not initialized`` unless ``init_system_pool()`` has run. A
   reachable-but-unbootstrapped stack must skip, never hard-fail.

Run it explicitly with:

    cd backend
    uv run pytest tests/benchmark -q -s -m engine
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from app.core.security import encrypt_password
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.query_execute import QueryExecuteTool

pytestmark = pytest.mark.engine

#: The test stack exposes StarRocks root with an empty password on this port
#: (see ``docker-compose.test.yml``); the same values ``tests/conftest.py``
#: uses for its session fixtures. Disposable Compose credentials, not secrets.
#: Overridable so a machine whose default port is occupied by another project
#: can run this checkout's stack elsewhere (``NOVA_ORCH_SR_PORT`` etc. mirror
#: the integration suite's variables).
_ENGINE_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
_ENGINE_PORT = int(os.getenv("NOVA_ORCH_SR_PORT", "29030"))
_ENGINE_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
_ENGINE_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD", "")
_BENCH_USER = os.getenv("NOVA_ORCH_BENCH_USER", "nova_admin")
_BENCH_PASSWORD = os.getenv(
    "NOVA_ORCH_BENCH_PASSWORD", os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!")
)

#: The compose file the integration fixtures bring up. Its absence means the
#: engine suite cannot run here at all.
_COMPOSE_FILE = "docker-compose.test.yml"

#: This checkout's ``backend/`` directory — the working dir a Compose run of
#: ``docker-compose.test.yml`` from here records on its containers. Used to tell
#: our stack from a foreign project publishing the same port.
_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _engine_available() -> bool:
    has_docker = shutil.which("docker") is not None
    has_compose = (_BACKEND_DIR / _COMPOSE_FILE).is_file()
    return has_docker and has_compose


if not _engine_available():
    pytest.skip(
        "engine benchmark needs Docker + docker-compose.test.yml",
        allow_module_level=True,
    )


def _select_context() -> LoopContext:
    # No active database: ``SELECT 1`` needs none, and naming ``default_catalog``
    # as a database does not exist on the test stack.
    return LoopContext(
        user_name=_BENCH_USER,
        thread_id="bench-engine",
        user={
            "username": _BENCH_USER,
            "encrypted_password": encrypt_password(_BENCH_PASSWORD),
            "active_role": "ACCOUNTADMIN",
            "assigned_roles": ["ACCOUNTADMIN"],
        },
    )


def _port_publisher_is_ours() -> bool:
    """True when the container on the engine port belongs to this checkout.

    The port alone is not identity: a foreign Compose project (another
    worktree, a QA sandbox) can publish ``127.0.0.1:29030``. This reads the
    publishing container's ``com.docker.compose.project.working_dir`` label and
    compares it with this checkout's ``backend/`` directory. Anything
    unverifiable reads as *not ours* — the test then skips rather than measure
    the wrong engine.
    """
    try:
        listing = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"publish={_ENGINE_PORT}",
                "--format",
                '{{.ID}}\t{{.Label "com.docker.compose.project.working_dir"}}',
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        return False

    for line in listing.stdout.splitlines():
        _, _, working_dir = line.partition("\t")
        if not working_dir:
            continue
        try:
            if Path(working_dir).resolve() == _BACKEND_DIR:
                return True
        except OSError:
            continue
    return False


async def _pool_ready() -> bool:
    """Initialise the app's system pool against the test stack, or report False.

    ``QueryExecuteTool.run`` audits via ``db.execute_system``, so the pool must
    exist. The benchmark drives the app directly (no FastAPI lifespan), so it
    initialises the pool itself. Any failure — connection refused, auth, a
    stack that is not ours — is a skip condition, not an error.
    """
    from app.core import config as cfg
    from app.core.database import db

    cfg.settings.STARROCKS_HOST = _ENGINE_HOST
    cfg.settings.STARROCKS_FE_MYSQL_PORT = _ENGINE_PORT
    cfg.settings.STARROCKS_ROOT_USER = _ENGINE_USER
    cfg.settings.STARROCKS_ROOT_PASSWORD = _ENGINE_PASSWORD

    with contextlib.suppress(Exception):
        # A stale pool from another run must not mask the real readiness check.
        await db.close_system_pool()
    try:
        await db.init_system_pool()
        await db.execute_system("SELECT 1")
    except Exception:  # noqa: BLE001 - any failure means "not usable here"
        return False
    return True


async def _provision_system_schema() -> None:
    """Create ``NOVA_SYSTEM.AUDIT_LOG`` on the test stack.

    ``query_execute`` writes an audit row on every call, so the tool cannot run
    until the audit table exists. The dev/test engine ships without
    ``init-nova.sql``; this reuses the integration suite's idempotent helper
    rather than inventing a second one.
    """
    from tests.integration._nova_system_ddl import ensure_audit_log

    await ensure_audit_log(
        host=_ENGINE_HOST,
        port=_ENGINE_PORT,
        user=_ENGINE_USER,
        password=_ENGINE_PASSWORD,
    )


async def test_benchmark_query_execute_round_trip():
    """Time one read-only SELECT through the tool against the real engine."""
    if not _port_publisher_is_ours():
        pytest.skip(
            "no StarRocks stack from this checkout publishes the benchmark port "
            "(a foreign compose project or none)"
        )
    if not await _pool_ready():
        pytest.skip("app system pool is not bootstrapped against the test stack")

    await _provision_system_schema()

    tool = QueryExecuteTool(max_rows=100)
    invocation = ToolInvocation(
        tool_call_id="bench-engine-c1",
        tool_name="query_execute",
        arguments={"sql": "SELECT 1 AS one"},
    )
    context = _select_context()

    samples: list[float] = []
    for _ in range(20):
        start = time.perf_counter_ns()
        outcome = await tool.run(invocation, context)
        samples.append((time.perf_counter_ns() - start) / 1_000)
        assert outcome.ok, f"engine round-trip failed: {outcome.error}"

    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    print(
        "BENCHMARK "
        + json.dumps(
            {
                "benchmark": "engine_query_execute_round_trip",
                "iterations": len(samples),
                "min_us": ordered[0],
                "median_us": ordered[len(ordered) // 2],
                "p95_us": p95,
                "max_us": ordered[-1],
                "statement": "SELECT 1 AS one",
            },
            sort_keys=True,
        )
    )
    assert ordered[-1] > 0
