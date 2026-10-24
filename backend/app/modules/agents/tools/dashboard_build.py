"""``dashboard_build`` — compose a dashboard artifact from the conversation.

Tableau authors a dashboard by placing worksheets on a canvas; this tool does
the same from chat: each chart request resolves through the ShowMe engine
into a worksheet over the turn's verified result, each worksheet is saved as
a chart artifact, and a zone layout composes them into one dashboard
artifact. Everything persists under the caller's ownership; refresh and
dashboard filters re-execute under the viewer's own StarRocks session.

The turn's verified result must carry its producing statement: a dashboard
without provenance could show data that cannot be rebuilt.
"""

from __future__ import annotations

import logging
from typing import Any

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.viz.showme import from_notional
from app.modules.viz.spec import (
    DashboardSpec,
    NotionalSpec,
    VizSourceSql,
    Zone,
    ZoneRect,
)

logger = logging.getLogger(__name__)

MAX_SHEETS = 8

_PARAMETERS = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "Dashboard title shown in the artifact library.",
        },
        "charts": {
            "type": "array",
            "maxItems": 8,
            "description": "One entry per chart sheet, in reading order.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "chart_type": {
                        "type": "string",
                        "enum": [
                            "auto",
                            "text_table",
                            "bar",
                            "bar_stacked",
                            "bar_side",
                            "line",
                            "area",
                            "scatter",
                            "pie",
                            "bubble",
                            "heatmap",
                        ],
                    },
                    "x": {"type": "string", "description": "Dimension column."},
                    "y": {
                        "description": "Measure column, or a list of them.",
                        "type": ["string", "array"],
                        "items": {"type": "string"},
                    },
                    "color": {"type": "string"},
                    "size": {"type": "string"},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "charts"],
    "additionalProperties": False,
}


def _zone_grid(count: int) -> list[ZoneRect]:
    """Two sheets per row on the 24-column grid (Tableau's tiled default)."""
    rects: list[ZoneRect] = []
    per_row = 2 if count > 1 else 1
    width = 24 // per_row
    for index in range(count):
        row, column = divmod(index, per_row)
        rects.append(ZoneRect(x=column * width, y=row * 2, w=width, h=2))
    return rects


class DashboardBuildTool:
    name = "dashboard_build"
    description = (
        "Compose a Nova Studio dashboard from the latest verified result in "
        "this conversation: one chart artifact per requested view, placed on a "
        "zone canvas. Call it after the data is fetched."
    )
    parameters = _PARAMETERS
    classification: ToolClassification = "session_change"
    #: Persists owned artifacts; the user approves before anything is saved.
    requires_consent = True

    def preview(self, invocation: ToolInvocation) -> str:
        title = invocation.arguments.get("title")
        charts = invocation.arguments.get("charts")
        count = len(charts) if isinstance(charts, list) else 0
        return f"dashboard_build: {title} ({count} charts)" if title else "dashboard_build"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.viz.service import VizError, viz_service

        user = getattr(context, "user", None)
        args = invocation.arguments
        title = str(args.get("title") or "").strip()[:256]
        requests = args.get("charts")
        if not isinstance(user, dict) or not title:
            return ToolOutcome(
                ok=False, summary="", error="A dashboard needs a title and a signed-in owner."
            )
        if not isinstance(requests, list) or not requests:
            return ToolOutcome(
                ok=False,
                summary="",
                error="A dashboard needs at least one chart. Describe each view to include.",
            )
        requests = requests[:MAX_SHEETS]

        last = getattr(context, "last_result", None)
        if not last or not last.get("columns"):
            return ToolOutcome(
                ok=False,
                summary="",
                error=(
                    "There is no recent data to build a dashboard from. Fetch "
                    "the data first, then compose the dashboard."
                ),
            )
        columns: list[str] = list(last.get("columns") or [])
        rows: list[list] = list(last.get("rows") or [])
        provenance_sql = str(last.get("sql") or "").strip()
        if not provenance_sql:
            return ToolOutcome(
                ok=False,
                summary="",
                error=(
                    "The latest result does not carry the statement that "
                    "produced it, so its charts cannot be saved. Re-run the "
                    "query, then build the dashboard."
                ),
            )
        source = VizSourceSql(sql=provenance_sql)

        saved: list[dict[str, Any]] = []
        for request in requests:
            if not isinstance(request, dict):
                continue
            chart_title = str(request.get("title") or "Chart").strip()[:256]
            y = request.get("y")
            try:
                notional = NotionalSpec(
                    chart_type=str(request.get("chart_type") or "auto"),  # type: ignore[arg-type]
                    x=_str_or_none(request.get("x")),
                    y=y if isinstance(y, (str, list)) else None,
                    color=_str_or_none(request.get("color")),
                    size=_str_or_none(request.get("size")),
                )
                spec = from_notional(notional, columns, rows, source=source, title=chart_title)
                artifact = await viz_service.save_chart(
                    user=user,
                    title=chart_title,
                    spec=spec,
                    agent_id=getattr(context, "agent_id", None),
                    thread_id=getattr(context, "thread_id", None),
                )
            except (ValueError, VizError) as exc:
                logger.warning("dashboard_build chart failed: %s", exc)
                continue
            saved.append(
                {
                    "artifact_id": artifact["artifact_id"],
                    "title": artifact["title"],
                    "chart_type": spec.marks.type,
                }
            )

        if not saved:
            return ToolOutcome(
                ok=False,
                summary="",
                error=(
                    "None of the requested charts could be built from the "
                    "current data. Check the column names and chart types."
                ),
            )

        rects = _zone_grid(len(saved))
        zones = [
            Zone(
                zone_id=f"zone_{index}",
                kind="sheet",
                artifact_id=chart["artifact_id"],
                rect=rect,
            )
            for index, (chart, rect) in enumerate(zip(saved, rects, strict=True))
        ]
        try:
            dashboard_spec = DashboardSpec(title=title, zones=zones)
            dashboard = await viz_service.save_dashboard(
                user=user,
                title=title,
                spec=dashboard_spec,
                agent_id=getattr(context, "agent_id", None),
                thread_id=getattr(context, "thread_id", None),
            )
        except (ValueError, VizError) as exc:
            return ToolOutcome(ok=False, summary="", error=str(exc))

        block = {
            "artifact_id": dashboard["artifact_id"],
            "title": title,
            "charts": saved,
            "fallback": "Open Artifacts to view the dashboard with live data.",
        }
        return ToolOutcome(
            ok=True,
            summary=f"Created dashboard: {title} ({len(saved)} charts)",
            data=block,
            metadata={"dashboard_artifact": block},
        )


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


dashboard_build_tool = DashboardBuildTool()
