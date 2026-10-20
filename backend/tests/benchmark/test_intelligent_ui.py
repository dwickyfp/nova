"""Offline validator overhead, including its process isolation boundary."""

import statistics
from time import perf_counter

import pytest

from app.modules.assistant.intelligent_ui import parse_program
from tests.unit.test_intelligent_ui import CALCULATOR

pytestmark = pytest.mark.benchmark


async def test_validator_overhead(capsys):
    elapsed = []
    for _ in range(20):
        start = perf_counter()
        result = await parse_program(CALCULATOR)
        elapsed.append((perf_counter() - start) * 1000)
        assert result["state"]["$units"] == 5
    with capsys.disabled():
        print(f"UI parser: median={statistics.median(elapsed):.1f}ms "
              f"p95={sorted(elapsed)[18]:.1f}ms (20 isolated Node processes)")
