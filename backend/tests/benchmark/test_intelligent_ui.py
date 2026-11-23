"""Offline cost of checking a page answer statement by statement as it streams."""

import statistics
from time import perf_counter

import pytest

from app.modules.assistant.ui_layout import LayoutStream
from tests.unit.test_ui_layout import ANSWER, _ctx

pytestmark = pytest.mark.benchmark


async def test_layout_check_overhead(capsys):
    elapsed = []
    for _ in range(20):
        stream = LayoutStream(_ctx())
        start = perf_counter()
        for offset in range(0, len(ANSWER), 16):
            await stream.feed(ANSWER[offset:offset + 16])
        await stream.finish()
        elapsed.append((perf_counter() - start) * 1000)
        assert len(stream.published) == 10
    with capsys.disabled():
        print(f"Page check: median={statistics.median(elapsed):.1f}ms "
              f"p95={sorted(elapsed)[18]:.1f}ms per answer (in process, 16-char deltas)")
