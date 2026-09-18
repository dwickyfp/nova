"""Engine-integrated benchmark for the read-only ``query_execute`` path (NOVA-92).

Marked ``@pytest.mark.engine``. It measures a real ``SELECT`` round-trip through
``query_execute`` → ``QueryService.execute_statements`` against the StarRocks
stack from ``docker-compose.test.yml``.

This is the one number the offline suite cannot give: engine latency. It is
**optional** and skips cleanly (never fails) when the stack is unreachable, so
CI without Docker stays green. It also skips when no delegate-first user
connection can be assembled — the tool refuses rather than falling back to a
service identity, and this benchmark must not fabricate a credential.

Run with the stack up:

    cd backend && docker compose -f docker-compose.test.yml up -d --wait
    cd backend && uv run pytest tests/benchmark -m engine -q -s

Point at an already-running engine with ``NOVA_ORCH_SR_HOST`` /
``NOVA_ORCH_SR_PORT`` (default ``127.0.0.1:29030``).
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time

import asyncmy
import pytest

from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.query_execute import QueryExecuteTool

pytestmark = [pytest.mark.engine, pytest.mark.benchmark]

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = int(_EXPLICIT_PORT or "29030")
SR_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
SR_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD", "")

REPETITIONS = 30


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


@pytest.fixture
async def engine_available():
    if not await _sr_reachable():
        pytest.skip("StarRocks not reachable — run docker-compose.test.yml")
    return True


def _context() -> LoopContext:
    """A delegate-first context.

    The tool reads ``user['username']`` / ``encrypted_password`` and passes them
    to ``QueryService.execute_statements``. The test stack's root connection is
    passwordless; the empty password is the compose default, not a secret. If
    the environment provides a password it must be supplied by the operator via
    ``NOVA_ORCH_SR_PASSWORD`` — never committed.
    """
    return LoopContext(
        user_name=SR_USER,
        database=None,
        schema_name=None,
        role=None,
        session_id="bench-engine-session",
        thread_id="bench-engine-thread",
        user={"username": SR_USER, "encrypted_password": SR_PASSWORD},
    )


async def test_select_round_trip_through_query_execute(engine_available):
    """Median / p95 of a ``SELECT 1`` through the full tool → service path."""
    tool = QueryExecuteTool()
    invocation = ToolInvocation(
        tool_call_id="bench-1",
        tool_name="query_execute",
        arguments={"sql": "SELECT 1 AS n"},
    )
    context = _context()

    warmup = await tool.run(invocation, context)
    if not warmup.ok:
        pytest.skip(f"engine tool path unavailable: {warmup.error}")

    samples: list[int] = []
    for _ in range(REPETITIONS):
        start = time.perf_counter_ns()
        outcome = await tool.run(invocation, context)
        samples.append(time.perf_counter_ns() - start)
        assert outcome.ok is True

    ordered = sorted(samples)
    rank = max(1, round(0.95 * len(ordered)))
    p95 = ordered[rank - 1]
    print(
        f"\n| engine SELECT round-trip | min (ns) | median (ns) | p95 (ns) |\n"
        f"| --- | ---: | ---: | ---: |\n"
        f"| query_execute → execute_statements | {min(samples)} "
        f"| {int(statistics.median(samples))} | {p95} |"
    )
