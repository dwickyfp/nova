"""Conservative, security-scoped fingerprints for ephemeral ML reuse."""

from __future__ import annotations

import hashlib
import json
import re

from app.modules.ml_engine.spec import MLExecutionSpec


def execution_fingerprint(spec: MLExecutionSpec) -> str:
    normalized_sql = re.sub(r"\s+", " ", spec.input_sql.strip()).rstrip(";")
    payload = {
        "scope": spec.security.scope_key,
        "task": spec.task.value,
        "sql": normalized_sql,
        "features": list(spec.feature_columns),
        "target": spec.target_column,
        "timestamp": spec.timestamp_column,
        "series": spec.series_column,
        "horizon": spec.horizon,
        "frequency": spec.frequency,
        "mode": spec.mode.value,
        "algorithm": spec.algorithm,
        "parameters": spec.parameters,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()
