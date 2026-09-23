"""Offline retrieval cost for a bounded agent memory set."""

from __future__ import annotations

import statistics
import time

import pytest

from app.modules.agents.memory import memory_prompt, select_memories

pytestmark = pytest.mark.benchmark


def test_memory_selection_over_200_facts(capsys):
    memories = [
        {
            "fact_key": f"business_rule_{index}",
            "fact": f"Aturan bisnis {index}: gunakan nilai invoice bulan {index}.",
        }
        for index in range(200)
    ]
    memories[170] = {
        "fact_key": "omzet_definition",
        "fact": "Omzet adalah invoice lunas dikurangi retur, tanpa PPN.",
    }
    samples = []
    for _ in range(500):
        start = time.perf_counter()
        selected = select_memories(memories, "Bagaimana hitung omzet?", limit=8)
        prompt = memory_prompt(selected)
        samples.append((time.perf_counter() - start) * 1000)
    assert selected[0]["fact_key"] == "omzet_definition"
    assert len(prompt) < 2800
    samples.sort()
    print(
        f"memory_selection p50_ms={statistics.median(samples):.3f} "
        f"p95_ms={samples[int(0.95 * len(samples))]:.3f} "
        f"prompt_chars={len(prompt)}"
    )
