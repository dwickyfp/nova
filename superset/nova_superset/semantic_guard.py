"""Datasets that follow a Nova Semantic View change only through Nova.

Nova syncs each published Semantic View into Superset datasets and marks them
in ``extra`` (docs/arch-17-superset.md, Phase 5). Editing or deleting such a
dataset in Superset would make it disagree with the published definition, so
those requests are refused unless they carry Nova's sync header: the service
token, which only Nova's backend holds and a browser never sees. A dataset
cannot claim the marker on its own either.
"""

from __future__ import annotations

import hmac
import json
import os
import re
from typing import Any

from flask import jsonify, request

SYNC_HEADER = "X-Nova-Semantic-Sync"
MARKER = "nova_semantic_view"
REFUSAL = "This dataset follows a Semantic View in Nova. Change the Semantic View instead."

_DATASET = re.compile(r"^/api/v1/dataset/(\d+)(?:/(column|metric)/\d+)?/?$")
_WRITES = {"PUT", "DELETE", "PATCH"}


def _from_nova() -> bool:
    secret = os.environ.get("NOVA_SUPERSET_SERVICE_TOKEN", "")
    sent = request.headers.get(SYNC_HEADER, "")
    return bool(secret) and hmac.compare_digest(sent.encode(), secret.encode())


def _claims_marker(body: Any) -> bool:
    extra = body.get("extra") if isinstance(body, dict) else None
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except ValueError:
            return False
    return isinstance(extra, dict) and MARKER in extra


def _targets() -> list[int]:
    """Dataset ids this request would change, or ``[]``."""
    path, method = request.path, request.method
    match = _DATASET.match(path)
    if match and method in _WRITES:
        return [int(match.group(1))]
    if path.rstrip("/") == "/api/v1/dataset" and method == "DELETE":
        # Bulk delete: ``?q=!(1,2,3)`` in rison.
        return [int(value) for value in re.findall(r"\d+", request.args.get("q", ""))]
    if path.rstrip("/") == "/datasource/save" and method == "POST":
        try:
            data = json.loads(request.form.get("data", "{}"))
        except ValueError:
            return []
        value = data.get("id") or data.get("datasource_id")
        return [int(value)] if str(value or "").isdigit() else []
    return []


def follows_semantic_view(dataset_id: int) -> bool:
    from superset import db
    from superset.connectors.sqla.models import SqlaTable

    table = db.session.get(SqlaTable, dataset_id)
    if table is None or not table.extra:
        return False
    try:
        return MARKER in (json.loads(table.extra) or {})
    except (TypeError, ValueError):
        return False


def guard():
    """``before_request``: refuse changes to Semantic View datasets."""
    if request.method not in {"POST", *_WRITES} or _from_nova():
        return None
    body = request.get_json(silent=True) if request.is_json else None
    if request.path.startswith("/api/v1/dataset") and _claims_marker(body):
        return jsonify({"message": REFUSAL}), 403
    if any(follows_semantic_view(dataset_id) for dataset_id in _targets()):
        return jsonify({"message": REFUSAL}), 403
    return None
