"""Semantic Views as Superset datasets (docs/arch-17-superset.md, Phase 5).

When a Semantic View version is published, each of its fact datasets (the
base dataset of at least one metric) becomes one Superset virtual dataset:

- its SQL selects the fact's fields, and the fields of datasets it reaches
  through many-to-one relationships, under their semantic names;
- the metrics on that fact become the dataset's metrics, rewritten over the
  dataset's own columns;
- time fields are temporal columns, and a metric's default time dimension is
  the dataset's main time column.

The dataset is created as the publisher, so Superset's author role applies,
and like every Nova dataset its queries run as the viewer: Ranger decides the
rows. Its ``extra`` names the Semantic View and version; Superset refuses edits
to such a dataset unless they come from this sync (the
``X-Nova-Semantic-Sync`` header carries the service token, which only Nova
holds), so it cannot drift from the published definition. A private, deprecated
or dropped view has no datasets.

Metrics BI cannot express faithfully are left out and reported: derived
metrics (built on other metrics), metrics with filters, semi-additive metrics
and metrics that reach a dataset the fact does not join.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.common.audit import write_audit_log
from app.core.config import settings
from app.core.database import db
from app.modules.agents.semantic.compiler import (
    quote_semantic_identifier,
    quote_semantic_source,
)
from app.modules.agents.semantic.expressions import qualify_expression
from app.modules.agents.semantic.ir import (
    Additivity,
    SemanticDatasetIR,
    SemanticFieldIR,
    SemanticModelIR,
)

from .client import BiError, SupersetClient

logger = logging.getLogger(__name__)

SYNC_HEADER = "X-Nova-Semantic-Sync"
MARKER = "nova_semantic_view"
MAPPING_TABLE = "NOVA_SYSTEM.CONFIG_SEMANTIC_BI_DATASETS"
MAPPING_DDL = f"""
CREATE TABLE IF NOT EXISTS {MAPPING_TABLE} (
    view_id VARCHAR(64) NOT NULL,
    dataset_name VARCHAR(128) NOT NULL,
    bi_dataset_id BIGINT,
    owner_name VARCHAR(128) NOT NULL,
    version INT,
    status VARCHAR(16) NOT NULL,
    message VARCHAR(1024),
    synced_at DATETIME NOT NULL
) PRIMARY KEY(view_id, dataset_name)
DISTRIBUTED BY HASH(view_id, dataset_name) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

_QUALIFIED = re.compile(r"`([^`]+)`\.`([^`]+)`")
_JOINABLE = {"many_to_one", "one_to_one"}


@dataclass
class DatasetPlan:
    """One Superset dataset for one fact dataset of a Semantic View."""

    name: str
    fact: str
    sql: str
    temporal: list[str] = field(default_factory=list)
    descriptions: dict[str, str] = field(default_factory=dict)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    main_time: str | None = None
    left_out: list[str] = field(default_factory=list)


def _q(identifier: str) -> str:
    return quote_semantic_identifier(identifier)


def _field_sql(item: SemanticFieldIR) -> str:
    expression = item.expression.strip().strip("`")
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$ ]*", expression):
        return f"{_q(item.dataset)}.{_q(expression)}"
    return f"({qualify_expression(item.expression, item.dataset)})"


def _table_name(view_name: str, fact: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", f"{view_name}_{fact}".lower()).strip("_")
    return f"semantic_{slug}"[:250]


def dataset_plans(model: SemanticModelIR, view_name: str) -> list[DatasetPlan]:
    """The Superset datasets for ``model``, one per fact dataset."""
    facts: dict[str, list] = {}
    for metric in model.metrics:
        if metric.base_dataset:
            facts.setdefault(metric.base_dataset, []).append(metric)
    plans = []
    for fact_name, metrics in facts.items():
        fact = model.dataset(fact_name)
        if fact is None:
            continue
        plans.append(_plan(model, view_name, fact, metrics))
    return plans


def _plan(
    model: SemanticModelIR, view_name: str, fact: SemanticDatasetIR, metrics: list
) -> DatasetPlan:
    # Output column -> its SELECT expression; references -> output column.
    outputs: dict[str, str] = {}
    by_reference: dict[tuple[str, str], str] = {}
    plan = DatasetPlan(name=_table_name(view_name, fact.name), fact=fact.name, sql="")

    def add(dataset: str, item: SemanticFieldIR) -> None:
        name = item.name if item.name not in outputs else f"{dataset}_{item.name}"
        outputs[name] = _field_sql(item)
        by_reference[(dataset, item.name)] = name
        if item.is_time:
            plan.temporal.append(name)
        if item.description:
            plan.descriptions[name] = item.description

    for item in fact.fields:
        add(fact.name, item)
    joins: list[str] = []
    joined = {fact.name}
    for relationship in model.relationships:
        target = model.dataset(relationship.to_dataset)
        if (
            relationship.from_dataset != fact.name
            or relationship.cardinality not in _JOINABLE
            or target is None
            or target.name in joined
        ):
            continue
        conditions = []
        for left, right in zip(relationship.from_columns, relationship.to_columns, strict=True):
            left_field, right_field = fact.field(left), target.field(right)
            left_sql = _field_sql(left_field) if left_field else f"{_q(fact.name)}.{_q(left)}"
            right_sql = _field_sql(right_field) if right_field else f"{_q(target.name)}.{_q(right)}"
            conditions.append(f"{left_sql} = {right_sql}")
        joins.append(
            f"LEFT JOIN {quote_semantic_source(target.source)} AS {_q(target.name)} "
            f"ON {' AND '.join(conditions)}"
        )
        joined.add(target.name)
        for item in target.fields:
            if item.kind == "dimension" or item.is_time:
                add(target.name, item)

    for metric in metrics:
        reason = _unsupported(metric)
        expression = None
        if reason is None:
            expression, reason = _metric_sql(model, metric, outputs, by_reference, joined)
        if reason:
            plan.left_out.append(f"{metric.name}: {reason}")
            continue
        plan.metrics.append(
            {
                "metric_name": metric.name,
                "expression": expression,
                "description": metric.description or None,
                "d3format": None,
            }
        )
        default_time = metric.default_time_dimension
        if plan.main_time is None and default_time:
            plan.main_time = by_reference.get((fact.name, default_time.split(".")[-1]))
    if plan.main_time is None and plan.temporal:
        plan.main_time = plan.temporal[0]
    select = ",\n  ".join(f"{sql} AS {_q(name)}" for name, sql in outputs.items())
    plan.sql = "\n".join(
        [
            f"SELECT\n  {select}",
            f"FROM {quote_semantic_source(fact.source)} AS {_q(fact.name)}",
            *joins,
        ]
    )
    return plan


def _unsupported(metric) -> str | None:
    if metric.dependencies:
        return "built on other metrics"
    if metric.filters:
        return "has metric filters"
    if metric.additivity == Additivity.SEMI_ADDITIVE:
        return "semi-additive, so BI would add it up across time"
    if metric.visibility != "public":
        return "not public"
    return None


def _metric_sql(
    model: SemanticModelIR,
    metric,
    outputs: dict[str, str],
    by_reference: dict[tuple[str, str], str],
    joined: set[str],
) -> tuple[str | None, str | None]:
    """``metric``'s expression over the dataset's columns, or why it has none."""
    try:
        qualified = qualify_expression(metric.expression, metric.base_dataset, model)
    except Exception as exc:  # noqa: BLE001 - reported per metric
        return None, f"its expression cannot be read ({exc})"
    missing: list[str] = []

    def column(match: re.Match[str]) -> str:
        dataset, name = match.group(1), match.group(2)
        if dataset not in joined:
            missing.append(dataset)
            return match.group(0)
        known = by_reference.get((dataset, name))
        if known and outputs[known] == f"{_q(dataset)}.{_q(name)}":
            return _q(known)
        # A raw column the metric reads: expose it under a reserved name.
        alias = f"__{dataset}__{name}"
        outputs.setdefault(alias, f"{_q(dataset)}.{_q(name)}")
        return _q(alias)

    rewritten = _QUALIFIED.sub(column, qualified)
    if missing:
        return None, f"reads {', '.join(sorted(set(missing)))}, which this dataset does not join"
    return rewritten, None


# ── Sync ──────────────────────────────────────────────────────────────


async def ensure_schema() -> None:
    await db.execute_system(MAPPING_DDL)


async def synced_datasets(view_id: str) -> list[dict[str, Any]]:
    result = await db.execute_system(
        f"SELECT dataset_name, bi_dataset_id, version, status, message, synced_at "
        f"FROM {MAPPING_TABLE} WHERE view_id = %s ORDER BY dataset_name",
        [view_id],
    )
    return [
        {
            "dataset": row[0],
            "bi_dataset_id": int(row[1]) if row[1] else None,
            "version": int(row[2]) if row[2] is not None else None,
            "status": row[3],
            "message": row[4],
            "synced_at": row[5],
            "bi_path": f"/bi/tablemodelview/edit/{int(row[1])}" if row[1] else None,
        }
        for row in result["rows"]
    ]


async def _record(view_id: str, owner: str, name: str, **values: Any) -> None:
    await db.execute_system(
        f"INSERT INTO {MAPPING_TABLE} (view_id, dataset_name, bi_dataset_id, owner_name, "
        "version, status, message, synced_at) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())",
        [
            view_id,
            name,
            values.get("bi_dataset_id"),
            owner,
            values.get("version"),
            values["status"],
            (values.get("message") or "")[:1024] or None,
        ],
    )


async def _forget(view_id: str, name: str) -> None:
    await db.execute_system(
        f"DELETE FROM {MAPPING_TABLE} WHERE view_id = %s AND dataset_name = %s", [view_id, name]
    )


def _sync_headers() -> dict[str, str]:
    return {SYNC_HEADER: settings.SUPERSET_SERVICE_TOKEN}


async def _apply(bi: SupersetClient, dataset_id: int, plan: DatasetPlan, marker: dict) -> None:
    """Make dataset ``dataset_id`` match ``plan`` exactly."""
    headers = _sync_headers()
    await bi.request(
        "PUT",
        f"/api/v1/dataset/{dataset_id}?override_columns=true",
        body={"sql": plan.sql},
        headers=headers,
    )
    await bi.request("PUT", f"/api/v1/dataset/{dataset_id}/refresh", headers=headers)
    current = (await bi.request("GET", f"/api/v1/dataset/{dataset_id}"))["result"]
    columns = [
        {
            "id": column["id"],
            "column_name": column["column_name"],
            "type": column.get("type"),
            "is_dttm": column["column_name"] in plan.temporal,
            "description": plan.descriptions.get(column["column_name"]),
            "groupby": not column["column_name"].startswith("__"),
            "filterable": not column["column_name"].startswith("__"),
        }
        for column in current.get("columns", [])
    ]
    existing = {metric["metric_name"]: metric["id"] for metric in current.get("metrics", [])}
    metrics = [
        {
            **metric,
            **(
                {"id": existing[metric["metric_name"]]} if metric["metric_name"] in existing else {}
            ),
        }
        for metric in plan.metrics
    ]
    await bi.request(
        "PUT",
        f"/api/v1/dataset/{dataset_id}",
        body={
            "columns": columns,
            "metrics": metrics,
            "main_dttm_col": plan.main_time,
            "description": f"Follows the Semantic View {marker['name']} "
            f"(version {marker['version']}). Change it in Nova.",
            "extra": json.dumps({MARKER: marker}),
        },
        headers=headers,
    )


async def sync_semantic_view(
    user: dict[str, Any], view: dict[str, Any], model: SemanticModelIR | None
) -> list[dict[str, Any]]:
    """Make BI's datasets for ``view`` match its active version (or remove them).

    ``model`` is the active version's model, or ``None`` when the view has no
    datasets in BI (private, deprecated or dropped). Runs as ``user``, the
    view's owner. Never raises: each dataset's outcome is recorded and returned.
    """
    view_id, owner = view["id"], view["owner_name"]
    previous = {item["dataset"]: item for item in await synced_datasets(view_id)}
    plans = dataset_plans(model, view["name"]) if model is not None else []
    wanted = {plan.fact: plan for plan in plans}
    if not plans and not previous:
        return []
    try:
        async with SupersetClient.for_user(user, author=bool(plans)) as bi:
            for name, item in previous.items():
                if name not in wanted and item["bi_dataset_id"]:
                    await _delete_dataset(bi, item["bi_dataset_id"])
                if name not in wanted:
                    await _forget(view_id, name)
            for plan in plans:
                await _sync_one(bi, view, plan, previous.get(plan.fact))
    except BiError as exc:
        logger.info("Semantic View %s not synced to BI: %s", view["name"], exc.message)
        for plan in plans:
            await _record(
                view_id, owner, plan.fact, version=view.get("active_version"),
                bi_dataset_id=(previous.get(plan.fact) or {}).get("bi_dataset_id"),
                status="FAILED", message=exc.message,
            )  # fmt: skip
    await write_audit_log(
        event_type="bi_semantic",
        user_name=user["username"],
        action="SYNC_SEMANTIC_DATASETS",
        object_type="SEMANTIC_VIEW",
        object_name=view["name"],
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )
    return await synced_datasets(view_id)


async def _sync_one(
    bi: SupersetClient, view: dict[str, Any], plan: DatasetPlan, previous: dict | None
) -> None:
    marker = {
        "id": view["id"],
        "name": view["name"],
        "version": view.get("active_version"),
        "dataset": plan.fact,
    }
    dataset_id = (previous or {}).get("bi_dataset_id")
    try:
        if dataset_id and not await _exists(bi, dataset_id):
            dataset_id = None
        if not dataset_id:
            body: dict[str, Any] = {
                "database": await bi.nova_database_id(),
                "catalog": view.get("catalog_name") or "default_catalog",
                "schema": view["database_name"],
                "table_name": plan.name,
                "sql": plan.sql,
            }
            dataset_id = int((await bi.request("POST", "/api/v1/dataset/", body=body))["id"])
        await _apply(bi, dataset_id, plan, marker)
    except BiError as exc:
        if exc.status in (401, 403):
            raise
        await _record(
            view["id"], view["owner_name"], plan.fact, version=view.get("active_version"),
            bi_dataset_id=dataset_id, status="FAILED", message=exc.message,
        )  # fmt: skip
        return
    message = "; ".join(plan.left_out) or None
    await _record(
        view["id"], view["owner_name"], plan.fact, version=view.get("active_version"),
        bi_dataset_id=dataset_id, status="SYNCED",
        message=f"Left out: {message}" if message else None,
    )  # fmt: skip


async def _exists(bi: SupersetClient, dataset_id: int) -> bool:
    try:
        await bi.request("GET", f"/api/v1/dataset/{dataset_id}")
    except BiError as exc:
        if exc.status == 404:
            return False
        raise
    return True


async def _delete_dataset(bi: SupersetClient, dataset_id: int) -> None:
    try:
        await bi.request("DELETE", f"/api/v1/dataset/{dataset_id}", headers=_sync_headers())
    except BiError as exc:
        if exc.status != 404:
            logger.warning("BI dataset %s was not removed: %s", dataset_id, exc.message)
