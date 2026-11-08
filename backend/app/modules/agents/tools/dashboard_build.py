"""``dashboard_build`` — compose a BI dashboard from the conversation.

Each chart request resolves through the ShowMe engine into a worksheet over
the turn's verified result, is published to Superset as a dataset and chart
(docs/arch-17-superset.md), and the charts are placed on one Superset
dashboard. Every Superset call runs as the caller, so the caller's BI role
decides whether they may author, and Ranger decides what each chart returns
to whoever views it.

Each chart names the verified result it draws (``source``, an evidence id of
this turn), so a dashboard over several questions keeps each chart on its own
query (docs/arch-19-studio-dataviz.md). A chart may omit ``source`` only when
exactly one result is available; with several, the call is refused rather than
guessing. A result must carry its producing statement: a dashboard without
provenance could show data that cannot be rebuilt.
"""

from __future__ import annotations

import logging
from typing import Any

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.viz.showme import from_intent
from app.modules.viz.spec import ChartIntent, VizSourceSql

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
                    "source": {
                        "type": "string",
                        "description": (
                            "Evidence id of the verified result this chart draws, for example "
                            "evidence_2. Required when this turn has more than one result."
                        ),
                    },
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "charts"],
    "additionalProperties": False,
}


class DashboardBuildTool:
    #: Model calls, if any, go through the in-process ``AssistantProviderClient``,
    #: so a measured attempt counts their tokens.
    nested_requests_measured = True
    name = "dashboard_build"
    description = (
        "Compose a new BI dashboard from verified results in this conversation: "
        "one BI chart per requested view, each drawn from the result named by its "
        "source, placed on one BI dashboard. Call it after the data is fetched. "
        "To add one result to an existing dashboard use dataviz_plan_add."
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
        from app.modules.superset import artifacts as bi_artifacts
        from app.modules.superset.charts import plan_from_worksheet
        from app.modules.superset.client import BiError

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

        sources = _sources(context)
        if not sources:
            return ToolOutcome(
                ok=False,
                summary="",
                error=(
                    "There is no recent data to build a dashboard from. Fetch "
                    "the data first, then compose the dashboard."
                ),
            )
        saved: list[dict[str, Any]] = []
        published: list[dict[str, Any]] = []
        for request in requests:
            if not isinstance(request, dict):
                continue
            chart_title = str(request.get("title") or "Chart").strip()[:256]
            named = request.get("source")
            if isinstance(named, str) and named:
                chosen = sources.get(named)
                if chosen is None:
                    return ToolOutcome(
                        ok=False, summary="",
                        error=f"No verified result {named!r} in this turn. Use an evidence id.",
                    )
            elif len(sources) == 1:
                chosen = next(iter(sources.values()))
            else:
                return ToolOutcome(
                    ok=False, summary="",
                    error=(
                        "This turn has several results; name the source evidence id of "
                        f"each chart ({', '.join(sorted(sources))})."
                    ),
                )
            columns, rows, provenance_sql = chosen
            if not provenance_sql:
                return ToolOutcome(
                    ok=False,
                    summary="",
                    error=(
                        "That result does not carry the statement that produced it, so "
                        "its chart cannot be saved. Re-run the query, then build the dashboard."
                    ),
                )
            source = VizSourceSql(sql=provenance_sql)
            y = request.get("y")
            try:
                chart_intent = ChartIntent(
                    chart_type=str(request.get("chart_type") or "auto"),  # type: ignore[arg-type]
                    x=_str_or_none(request.get("x")),
                    y=y if isinstance(y, (str, list)) else None,
                    color=_str_or_none(request.get("color")),
                    size=_str_or_none(request.get("size")),
                )
                spec = from_intent(chart_intent, columns, rows, source=source, title=chart_title)
                artifact = await bi_artifacts.publish_chart(
                    user,
                    title=chart_title,
                    sql_text=provenance_sql,
                    plan=plan_from_worksheet(spec.model_dump(mode="json")),
                    database_name=getattr(context, "database", None),
                    schema_name=getattr(context, "schema_name", None),
                    agent_id=getattr(context, "agent_id", None),
                    thread_id=getattr(context, "thread_id", None),
                )
            except BiError as exc:
                if exc.status in (401, 403):
                    # The caller cannot author BI content; no other chart can
                    # succeed either.
                    return ToolOutcome(ok=False, summary="", error=exc.message)
                logger.warning("dashboard_build chart failed: %s", exc.message)
                continue
            except ValueError as exc:
                logger.warning("dashboard_build chart failed: %s", exc)
                continue
            published.append(artifact)
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

        try:
            dashboard = await bi_artifacts.publish_dashboard(
                user,
                title=title,
                chart_artifacts=published,
                agent_id=getattr(context, "agent_id", None),
                thread_id=getattr(context, "thread_id", None),
            )
        except BiError as exc:
            return ToolOutcome(ok=False, summary="", error=exc.message)

        block = {
            "artifact_id": dashboard["artifact_id"],
            "title": title,
            "charts": saved,
            "bi_view_path": dashboard.get("bi_view_path"),
            "fallback": "Open Artifacts to view the dashboard in BI with live data.",
        }
        return ToolOutcome(
            ok=True,
            summary=f"Created dashboard: {title} ({len(saved)} charts)",
            data=block,
            metadata={"dashboard_artifact": block},
        )


def _sources(context: Any) -> dict[str, tuple[list[str], list[list], str]]:
    """Verified results by evidence id: this turn's, else the thread's latest.

    Each keeps its own columns, rows and producing statement; nothing is
    borrowed from another result.
    """
    out: dict[str, tuple[list[str], list[list], str]] = {}
    statements = getattr(context, "evidence_sql", None) or {}
    for evidence_id, table in (getattr(context, "evidence_tables", None) or {}).items():
        if not isinstance(table, dict) or not table.get("columns"):
            continue
        sql = str(table.get("sql") or statements.get(evidence_id) or "").strip()
        out[str(evidence_id)] = (
            list(table.get("columns") or []), list(table.get("rows") or []), sql
        )
    if out:
        return out
    last = getattr(context, "last_result", None)
    if last and last.get("columns"):
        out["previous_result"] = (
            list(last.get("columns") or []), list(last.get("rows") or []),
            str(last.get("sql") or "").strip(),
        )
    return out


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


dashboard_build_tool = DashboardBuildTool()
