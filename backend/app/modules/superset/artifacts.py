"""Studio artifacts published to Superset (docs/arch-17-superset.md, Phases 3-4).

A chart artifact becomes a Superset virtual dataset over the artifact's
read-only SQL plus a chart on it; a dashboard artifact becomes a Superset
dashboard over BI chart artifacts the same user owns. Every Superset call runs
as the caller (``SupersetClient.for_user``), so Superset's role for the active
Nova role decides whether the user may author, and Ranger decides what each
query returns. Before a chart is saved its query runs through Superset's chart
data API, so a chart that cannot draw is never stored.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import uuid4

from app.common.audit import write_audit_log
from app.modules.agents.artifact_repository import artifact_repository, validate_artifact_sql

from .charts import ChartPlan, table_plan, to_superset
from .client import BiError, SupersetClient

logger = logging.getLogger(__name__)

#: Columns per row and grid width of a Superset dashboard.
_PER_ROW = 2
_GRID_WIDTH = 12
_CHART_HEIGHT = 50


def _dataset_name(title: str, suffix: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:60] or "result"
    return f"nova_{slug}_{suffix}"


async def _audit(user: dict[str, Any], action: str, object_type: str, name: str) -> None:
    await write_audit_log(
        event_type="bi_artifact",
        user_name=user["username"],
        action=action,
        object_type=object_type,
        object_name=name,
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
        security_context_version=user.get("security_context_version"),
    )


async def _dataset_columns(bi: SupersetClient, dataset_id: int) -> tuple[list[str], set[str]]:
    body = await bi.request("GET", f"/api/v1/dataset/{dataset_id}")
    columns = body.get("result", {}).get("columns", [])
    names = [str(column["column_name"]) for column in columns]
    temporal = {str(column["column_name"]) for column in columns if column.get("is_dttm")}
    return names, temporal


async def _chart_payload(
    bi: SupersetClient, plan: ChartPlan, dataset_id: int
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Translate ``plan``, falling back to a table when its fields are gone,
    and prove the query draws by running it through the chart data API."""
    columns, temporal = await _dataset_columns(bi, dataset_id)
    try:
        viz_type, form, query_context = to_superset(
            plan, dataset_id=dataset_id, columns=columns, temporal=temporal
        )
    except ValueError:
        viz_type, form, query_context = to_superset(
            table_plan(columns), dataset_id=dataset_id, columns=columns, temporal=temporal
        )
    await bi.request("POST", "/api/v1/chart/data", body=query_context)
    return viz_type, form, query_context


async def _create_dataset(
    bi: SupersetClient, *, title: str, sql: str, schema: str | None, suffix: str
) -> int:
    body: dict[str, Any] = {
        "database": await bi.nova_database_id(),
        "catalog": "default_catalog",
        "table_name": _dataset_name(title, suffix),
        "sql": sql,
    }
    if schema:
        body["schema"] = schema
    created = await bi.request("POST", "/api/v1/dataset/", body=body)
    return int(created["id"])


async def publish_chart(
    user: dict[str, Any],
    *,
    title: str,
    sql_text: str,
    plan: ChartPlan | None,
    database_name: str | None,
    schema_name: str | None,
    agent_id: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Create the dataset and chart in Superset, then the Studio artifact."""
    sql = validate_artifact_sql(sql_text)
    async with SupersetClient.for_user(user) as bi:
        dataset_id, chart_id = await create_chart_objects(
            bi, title=title, sql=sql, plan=plan, schema=schema_name or database_name
        )
    artifact = await artifact_repository.create_bi(
        owner_name=user["username"],
        title=title,
        artifact_type="chart",
        sql_text=sql,
        database_name=database_name,
        schema_name=schema_name,
        bi_kind="chart",
        bi_object_id=chart_id,
        bi_dataset_id=dataset_id,
        agent_id=agent_id,
        thread_id=thread_id,
    )
    await _audit(user, "CREATE_BI_CHART", "SUPERSET_CHART", str(chart_id))
    return artifact


async def create_chart_objects(
    bi: SupersetClient, *, title: str, sql: str, plan: ChartPlan | None, schema: str | None
) -> tuple[int, int]:
    """Create a dataset over ``sql`` and a validated chart on it; ``(dataset, chart)``."""
    dataset_id = await _create_dataset(
        bi, title=title, sql=sql, schema=schema, suffix=uuid4().hex[:8]
    )
    try:
        columns, _temporal = await _dataset_columns(bi, dataset_id)
        viz_type, form, query_context = await _chart_payload(
            bi, plan or table_plan(columns), dataset_id
        )
        chart = await bi.request(
            "POST",
            "/api/v1/chart/",
            body={
                "slice_name": title,
                "viz_type": viz_type,
                "datasource_id": dataset_id,
                "datasource_type": "table",
                "params": json.dumps(form),
                "query_context": json.dumps(query_context),
            },
        )
    except BiError:
        await _delete_quietly(bi, f"/api/v1/dataset/{dataset_id}")
        raise
    return dataset_id, int(chart["id"])


def _position(
    title: str, charts: list[tuple[int, str]], texts: list[str] | None = None
) -> dict[str, Any]:
    """A Superset grid layout: two charts per row, then text blocks, in order."""
    width = _GRID_WIDTH // _PER_ROW if len(charts) > 1 else _GRID_WIDTH
    layout: dict[str, Any] = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": title}},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
    }
    for start in range(0, len(charts), _PER_ROW):
        row_id = f"ROW-{start // _PER_ROW}"
        layout["GRID_ID"]["children"].append(row_id)
        row: dict[str, Any] = {
            "type": "ROW",
            "id": row_id,
            "children": [],
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
        for chart_id, name in charts[start : start + _PER_ROW]:
            node_id = f"CHART-{chart_id}"
            row["children"].append(node_id)
            layout[node_id] = {
                "type": "CHART",
                "id": node_id,
                "children": [],
                "parents": ["ROOT_ID", "GRID_ID", row_id],
                "meta": {
                    "chartId": chart_id,
                    "width": width,
                    "height": _CHART_HEIGHT,
                    "sliceName": name,
                },
            }
        layout[row_id] = row
    for index, text in enumerate(texts or []):
        row_id, node_id = f"ROW-TEXT-{index}", f"MARKDOWN-{index}"
        layout["GRID_ID"]["children"].append(row_id)
        layout[row_id] = {
            "type": "ROW",
            "id": row_id,
            "children": [node_id],
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
        layout[node_id] = {
            "type": "MARKDOWN",
            "id": node_id,
            "children": [],
            "parents": ["ROOT_ID", "GRID_ID", row_id],
            "meta": {"width": _GRID_WIDTH, "height": 20, "code": text},
        }
    return layout


async def owned_bi_charts(user: dict[str, Any], artifact_ids: list[str]) -> list[dict[str, Any]]:
    """The caller's BI chart artifacts among ``artifact_ids``, in that order.

    A dashboard never reaches another owner's chart: an id the caller does not
    own, or a legacy (non-BI) chart, is refused.
    """
    charts = []
    for artifact_id in artifact_ids:
        artifact = await artifact_repository.get(artifact_id, owner_name=user["username"])
        if artifact is None or artifact["artifact_type"] != "chart" or not artifact["bi_object_id"]:
            raise ValueError("A dashboard can only place BI charts you own.")
        charts.append(artifact)
    return charts


async def publish_dashboard(
    user: dict[str, Any],
    *,
    title: str,
    chart_artifacts: list[dict[str, Any]],
    agent_id: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Create a Superset dashboard placing ``chart_artifacts``, then the artifact."""
    async with SupersetClient.for_user(user) as bi:
        dashboard_id = await create_dashboard_object(
            bi, title=title, chart_artifacts=chart_artifacts
        )
    artifact = await artifact_repository.create_bi(
        owner_name=user["username"],
        title=title,
        artifact_type="dashboard",
        sql_text="",
        database_name=None,
        schema_name=None,
        bi_kind="dashboard",
        bi_object_id=dashboard_id,
        bi_dataset_id=None,
        agent_id=agent_id,
        thread_id=thread_id,
    )
    await _audit(user, "CREATE_BI_DASHBOARD", "SUPERSET_DASHBOARD", str(dashboard_id))
    return artifact


async def create_dashboard_object(
    bi: SupersetClient,
    *,
    title: str,
    chart_artifacts: list[dict[str, Any]],
    texts: list[str] | None = None,
) -> int:
    """Create a dashboard placing the BI charts (and text blocks); its id."""
    placed = [(int(chart["bi_object_id"]), str(chart["title"])) for chart in chart_artifacts]
    dashboard = await bi.request(
        "POST",
        "/api/v1/dashboard/",
        body={
            "dashboard_title": title,
            "position_json": json.dumps(_position(title, placed, texts)),
            "published": False,
        },
    )
    dashboard_id = int(dashboard["id"])
    for chart_id, _name in placed:
        await bi.request("PUT", f"/api/v1/chart/{chart_id}", body={"dashboards": [dashboard_id]})
    return dashboard_id


async def update_chart_source(
    user: dict[str, Any],
    artifact: dict[str, Any],
    *,
    sql_text: str,
    plan: ChartPlan | None,
    expected_updated_at: Any,
) -> dict[str, Any] | None:
    """Apply an edit: new dataset SQL and, when given, a new chart plan.

    Without a plan the chart keeps its own definition when the new columns
    still carry it, and becomes a table over them otherwise.
    """
    sql = validate_artifact_sql(sql_text)
    dataset_id = int(artifact["bi_dataset_id"])
    chart_id = int(artifact["bi_object_id"])
    async with SupersetClient.for_user(user) as bi:
        await bi.request("PUT", f"/api/v1/dataset/{dataset_id}", body={"sql": sql})
        await bi.request("PUT", f"/api/v1/dataset/{dataset_id}/refresh")
        if plan is None:
            current = await bi.request("GET", f"/api/v1/chart/{chart_id}")
            stored = current.get("result", {}).get("query_context")
            try:
                await bi.request("POST", "/api/v1/chart/data", body=json.loads(stored or "{}"))
                payload = None
            except (BiError, ValueError):
                columns, _ = await _dataset_columns(bi, dataset_id)
                payload = await _chart_payload(bi, table_plan(columns), dataset_id)
        else:
            payload = await _chart_payload(bi, plan, dataset_id)
        if payload is not None:
            viz_type, form, query_context = payload
            await bi.request(
                "PUT",
                f"/api/v1/chart/{chart_id}",
                body={
                    "viz_type": viz_type,
                    "params": json.dumps(form),
                    "query_context": json.dumps(query_context),
                },
            )
    updated = await artifact_repository.update_bi_source(
        artifact["artifact_id"],
        owner_name=user["username"],
        expected_updated_at=expected_updated_at,
        sql_text=sql,
    )
    if updated is not None:
        await _audit(user, "UPDATE_BI_CHART", "SUPERSET_CHART", str(chart_id))
    return updated


async def _delete_quietly(bi: SupersetClient, path: str) -> None:
    try:
        await bi.request("DELETE", path)
    except BiError as exc:
        logger.warning("Superset cleanup of %s failed: %s", path, exc.message)


async def delete_bi_objects(user: dict[str, Any], artifact: dict[str, Any]) -> None:
    """Remove the artifact's Superset chart or dashboard (and a chart's dataset).

    Best effort: the Studio artifact is deleted either way, and a Superset
    object left behind stays owned by the same user, visible in BI.
    """
    kind, object_id = artifact.get("bi_kind"), artifact.get("bi_object_id")
    if not kind or not object_id:
        return
    try:
        async with SupersetClient.for_user(user, author=False) as bi:
            await _delete_quietly(bi, f"/api/v1/{kind}/{object_id}")
            if artifact.get("bi_dataset_id"):
                await _delete_quietly(bi, f"/api/v1/dataset/{artifact['bi_dataset_id']}")
    except BiError as exc:
        logger.warning("Superset cleanup for %s failed: %s", artifact["artifact_id"], exc.message)
