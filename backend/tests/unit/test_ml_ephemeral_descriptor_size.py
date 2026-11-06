"""A cached ML run always fits its StarRocks STRING column."""

import json
import time

from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.ephemeral.cache import EphemeralEntry
from app.modules.ml_engine.ephemeral.repository import (
    MAX_DESCRIPTOR_BYTES,
    decode_entry,
    encode_entry,
)


def _entry(results):
    return EphemeralEntry(
        run_id="run", fingerprint="f" * 64, scope_key="scope", artifact_sha256="s",
        expires_at=time.monotonic() + 60, task="route_optimization",
        output=TrainingOutput(bundle={}, engine="nova_routing", algorithm="nn+2opt",
                              metrics={}, feature_columns=[], training_rows=len(results),
                              results=results),
    )


def test_a_large_result_preview_is_trimmed_to_fit_and_marked():
    rows = [{"route": f"Area {i}", "route_line": "LINESTRING(" + "98.6 3.5, " * 40 + ")"}
            for i in range(1000)]
    encoded = encode_entry(_entry(rows))
    assert len(encoded.encode()) <= MAX_DESCRIPTOR_BYTES
    decoded = decode_entry(encoded, time.time() + 60).output
    assert 0 < len(decoded.results) < 1000
    assert decoded.results == rows[: len(decoded.results)]
    assert decoded.metrics["cached_results_truncated"] is True


def test_a_small_preview_is_kept_whole():
    rows = [{"route": "Area 1", "km": 4.2}]
    value = json.loads(encode_entry(_entry(rows)))
    assert value["output"]["results"] == rows
    assert "cached_results_truncated" not in value["output"]["metrics"]
