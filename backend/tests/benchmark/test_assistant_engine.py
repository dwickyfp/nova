"""Engine-integrated benchmark for ``query_execute`` (NOVA-92, deliverable 3).

Measures a read-only ``SELECT`` round-trip through the real delegate-first path
— ``QueryExecuteTool.run`` → ``QueryService.execute_statements`` → StarRocks —
against the stack from ``docker-compose.test.yml``.

This module is marked ``@pytest.mark.engine`` and **skips cleanly** when Docker
or the compose stack is unavailable, so ``uv run pytest`` stays green on a
machine without the stack (including CI). It never fakes an engine number: if
the stack is present it reports a real measurement; if it is not, it skips
without a value. Run it explicitly with:

    cd backend
    uv run pytest tests/benchmark -q -s -m engine
"""

from __future__ import annotations

import json
import shutil
import time

import pytest

from app.core.security import encrypt_password
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.query_execute import QueryExecuteTool

pytestmark = pytest.mark.engine

#: The test stack exposes StarRocks root with an empty password on this port
#: (see ``docker-compose.test.yml``); the same values ``tests/conftest.py``
#: uses for its session fixtures. Disposable Compose credentials, not secrets.
_ENGINE_HOST = "127.0.0.1"
_ENGINE_PORT = 29030
_ENGINE_USER = "root"
_ENGINE_PASSWORD = ""

#: The compose file the integration fixtures bring up. Its absence means the
#: engine suite cannot run here at all.
_COMPOSE_FILE = "docker-compose.test.yml"


def _engine_available() -> bool:
    from pathlib import Path

    has_docker = shutil.which("docker") is not None
    has_compose = (Path(__file__).resolve().parents[2] / _COMPOSE_FILE).is_file()
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
        user_name=_ENGINE_USER,
        thread_id="bench-engine",
        user={
            "username": _ENGINE_USER,
            "encrypted_password": encrypt_password(_ENGINE_PASSWORD),
        },
    )


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


async def _reachable() -> bool:
    """True when StarRocks answers on the test port.

    The engine benchmark must not depend on the heavy ``app`` fixture (which
    attempts to start the whole Compose stack and errors if it cannot). A direct
    probe lets the module skip cleanly on a machine where the stack is absent,
    down, or occupied by another Compose project — the task requires a clean
    skip, not a fixture error.
    """
    import asyncmy

    try:
        conn = await asyncmy.connect(
            host=_ENGINE_HOST,
            port=_ENGINE_PORT,
            user=_ENGINE_USER,
            password=_ENGINE_PASSWORD,
        )
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return False
    conn.close()
    return True


async def test_benchmark_query_execute_round_trip():
    """Time one read-only SELECT through the tool against the real engine."""
    if not await _reachable():
        pytest.skip("StarRocks test stack is not reachable on the benchmark port")

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
