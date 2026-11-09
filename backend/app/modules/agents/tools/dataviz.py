"""Studio tools for Dataviz (docs/arch-19-studio-dataviz.md, D6).

They run inside the shared bounded ``AssistantLoop``; no second loop. Every
tool reads the caller's own session and role from the loop context, takes
explicit ids, and is schema-bound: there is no tool that reaches an arbitrary
Superset URL.

* ``dataviz_list_results`` names the results in this conversation with stable
  ids (``R1``, ``R2``…) taken from Nova's stored messages, so "the second
  chart" resolves to one exact result and never to a guessed query.
* ``dataviz_check_result`` asks the server resolver whether a result can be a
  Dataviz chart, and if not, what a consented alternative would keep and lose.
* ``dataviz_context`` / ``dataviz_chart_source`` describe the dashboard on
  screen and where a chart's numbers come from, linked to the Supernova
  Business Ontology.
* ``dataviz_plan_add`` prepares an addition and returns its diff and an action
  token; nothing changes.
* ``dataviz_apply`` applies exactly that plan. It needs the user's consent and
  checks role, revision and unsaved edits again.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from typing import Any
from uuid import uuid4

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

logger = logging.getLogger(__name__)

MAX_RESULTS = 60
_PREVIEWS: OrderedDict[str, str] = OrderedDict()
_PREVIEW_CAP = 256


def remember_preview(token: str, text: str) -> None:
    """Keep a prepared change's description for the consent prompt."""
    _remember_preview(token, text)


def _remember_preview(token: str, text: str) -> None:
    """Keep a plan's human diff for the consent card in this process.

    Studio turns are pinned to one process per session (docs/arch-15), so the
    consent prompt for ``dataviz_apply`` can show the exact diff the plan
    produced. Redis stays the authority on apply.
    """
    _PREVIEWS[token] = text
    _PREVIEWS.move_to_end(token)
    while len(_PREVIEWS) > _PREVIEW_CAP:
        _PREVIEWS.popitem(last=False)


def _user(context: Any) -> dict[str, Any] | None:
    user = getattr(context, "user", None)
    return user if isinstance(user, dict) else None


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


async def _results(context: Any, user: dict[str, Any]) -> list[dict[str, Any]]:
    """Publishable results in this thread, oldest first, with stable ids."""
    from app.modules.assistant.repository import assistant_repository
    from app.modules.superset.openui_nodes import OpenUiParseError, data_nodes

    thread_id = getattr(context, "thread_id", None)
    if not thread_id:
        return []
    messages = await assistant_repository.list_messages(thread_id, user_name=user["username"])
    found: list[dict[str, Any]] = []
    turn = 0
    for message in messages:
        if message["role"] != "assistant":
            continue
        turn += 1
        for step in message.get("steps") or []:
            kind = step.get("kind") if isinstance(step, dict) else None
            content_id = step.get("content_id") if kind else None
            if not content_id:
                continue
            base = {
                "thread_id": thread_id,
                "message_id": message["message_id"],
                "content_id": content_id,
                "turn": turn,
            }
            if kind == "table":
                found.append(
                    {**base, "kind": "table", "title": step.get("title") or "Result table"}
                )
            elif kind == "chart":
                spec = _json(step.get("chart_spec")) or {}
                found.append(
                    {
                        **base,
                        "kind": "chart",
                        "title": spec.get("title") if isinstance(spec, dict) else "Chart",
                    }
                )
            elif kind == "ui":
                try:
                    nodes = data_nodes(str(step.get("program") or ""))
                except OpenUiParseError:
                    continue
                for node in nodes:
                    if not node.evidence_id:
                        continue
                    options = node.prop("options") if isinstance(node.prop("options"), dict) else {}
                    route = node.component == "GeoMap" and options.get("layer") == "route"
                    locator_node = {"component": node.component, "evidence_id": node.evidence_id}
                    for key in ("x", "y", "type", "title", "column", "longitude", "latitude"):
                        value = node.prop(key)
                        if isinstance(value, str):
                            locator_node[key] = value
                    found.append(
                        {
                            **base,
                            "node": locator_node,
                            "kind": "route_map"
                            if route
                            else {
                                "Chart": "chart",
                                "Table": "table",
                                "Kpi": "kpi",
                                "GeoMap": "map",
                            }[node.component],
                            "title": node.prop("title") or node.prop("label") or node.component,
                            "chart_type": node.prop("type") if node.component == "Chart" else None,
                        }
                    )
    for index, item in enumerate(found[-MAX_RESULTS:], start=1):
        item["result_id"] = f"R{index}"
    return found[-MAX_RESULTS:]


async def _locator(context: Any, user: dict[str, Any], result_id: str):
    from app.modules.superset.previews import Locator

    for item in await _results(context, user):
        if item["result_id"] == str(result_id).strip().upper():
            return Locator(
                item["thread_id"], item["message_id"], item["content_id"], item.get("node")
            ), item
    return None, None


def _fail(message: str) -> ToolOutcome:
    return ToolOutcome(ok=False, summary="", error=message)


class _Tool:
    nested_requests_measured = True
    classification: ToolClassification = "read_only"
    requires_consent = False
    name = ""
    description = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}

    def preview(self, invocation: ToolInvocation) -> str:
        return self.name


class ListResultsTool(_Tool):
    name = "dataviz_list_results"
    description = (
        "List the tables, charts, KPIs and maps shown earlier in this conversation, each with a "
        "stable id (R1, R2, ...), its kind and title, newest last. Use it to find the exact result "
        "a user means by 'the map above' or 'the second chart' before checking or adding it."
    )

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        user = _user(context)
        if not user:
            return _fail("A signed-in owner is required.")
        items = await _results(context, user)
        public = [
            {key: item.get(key) for key in ("result_id", "kind", "title", "chart_type", "turn")}
            for item in items
        ]
        return ToolOutcome(
            ok=True, summary=f"{len(public)} results in this conversation", data={"results": public}
        )


class CheckResultTool(_Tool):
    name = "dataviz_check_result"
    #: A result that cannot be added as it is settles the add steps: the
    #: honest answer is the explanation, not a forced plan.
    may_settle = frozenset({"dataviz_plan_add", "dataviz_apply"})
    description = (
        "Ask Nova's Dataviz resolver whether one result (an id from dataviz_list_results) can be "
        "added to a Dataviz dashboard as it is. Returns SUPPORTED, CONVERTIBLE, UNSUPPORTED or "
        "UNKNOWN, the reason, and for a convertible result the alternatives with what each keeps "
        "and loses. Never claim a result is supported without this check."
    )
    parameters = {
        "type": "object",
        "properties": {"result_id": {"type": "string", "description": "Id such as R2."}},
        "required": ["result_id"],
        "additionalProperties": False,
    }

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.superset.dataviz import check_locator

        user = _user(context)
        if not user:
            return _fail("A signed-in owner is required.")
        locator, item = await _locator(context, user, invocation.arguments.get("result_id", ""))
        if locator is None:
            return _fail("No result has that id. List the results first.")
        result = await check_locator(locator, user)
        result.pop("preview_ref", None)
        settles = [] if result.get("can_add") else ["dataviz_plan_add", "dataviz_apply"]
        if result.get("status") == "SUPPORTED" and not result.get("can_add"):
            result["message"] = (
                "Supported, but the active role can view dashboards and cannot add charts."
            )
        return ToolOutcome(
            ok=True,
            summary=f"{item['result_id']} is {result['status']}",
            data={"result_id": item["result_id"], "title": item.get("title"), **result},
            metadata={"settles_capabilities": settles},
        )


class ContextTool(_Tool):
    name = "dataviz_context"
    description = (
        "Describe the Dataviz dashboard the user has open: title, owners, charts with their "
        "metrics, dimensions and sources, native filters and their verified current values "
        "(filter_state_status), edit permission, and the business concepts from the Supernova "
        "Business Ontology each chart measures. Use before answering about the dashboard. When "
        "filter_state_status is not 'verified', say the current filter selection is unknown."
    )

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.superset.dashboard_context import dashboard_context

        user = _user(context)
        dataviz = getattr(context, "dataviz", None) or {}
        if not user:
            return _fail("A signed-in owner is required.")
        if not dataviz.get("dashboard_id"):
            return ToolOutcome(
                ok=True,
                summary="No dashboard is open in Dataviz",
                data={
                    "available": False,
                    "reason": "No Dataviz dashboard is open in this conversation.",
                },
            )
        from app.modules.superset.client import BiError

        try:
            described = await dashboard_context(
                user,
                int(dataviz["dashboard_id"]),
                native_filters_key=dataviz.get("native_filters_key"),
                edit_state=dataviz.get("edit_state") or "unknown",
            )
        except BiError as exc:
            return _fail(f"The dashboard could not be read right now: {exc.message}")
        return ToolOutcome(ok=True, summary=f"Dashboard {described.get('title')!r}", data=described)


class QueryTool(_Tool):
    name = "dataviz_query"
    #: A governed result from the dashboard's own dataset answers a data
    #: question the planner routed to the semantic layer; with a comparison
    #: period it is also the change breakdown. Its facts carry the approved
    #: ontology concepts bound to the dataset, which ground the terms.
    may_settle = frozenset(
        {"semantic_query", "diagnose_change", "compute_metrics", "ontology_resolve"}
    )
    description = (
        "The first tool for any question about the numbers on the open Dataviz dashboard: "
        "totals, who or where leads (rankings by a dimension), when (a period), and why a number "
        "changed (pass period and compare_period plus one non-time dimension; it returns each "
        "member's change and share of the change, with an exact Total row). Its numbers match "
        "the dashboard's charts and apply its verified filters. Give a dataset_id from the "
        "dashboard brief, metrics (saved metric names or SUM/AVG/MIN/MAX/COUNT/COUNT_DISTINCT"
        "(column)), optional dimensions (a column, or a business word its description names), "
        "time_grain for a time dimension (default month), and periods as "
        "'YYYY-MM-DD : YYYY-MM-DD' (end excluded)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "dataset_id": {"type": "integer", "minimum": 1},
            "metrics": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6},
            "dimensions": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
            "period": {"type": "string"},
            "compare_period": {"type": "string"},
            "time_column": {"type": "string"},
            "row_limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "time_grain": {"type": "string", "enum": ["day", "week", "month", "quarter", "year"]},
        },
        "required": ["dataset_id", "metrics"],
        "additionalProperties": False,
    }

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.superset.client import BiError
        from app.modules.superset.dashboard_query import QueryRefused, query_dashboard_dataset

        user = _user(context)
        dataviz = getattr(context, "dataviz", None) or {}
        if not user:
            return _fail("A signed-in owner is required.")
        if not dataviz.get("dashboard_id"):
            return _fail("No dashboard is open in Dataviz; use semantic_query instead.")
        args = invocation.arguments
        try:
            answer = await query_dashboard_dataset(
                user,
                dashboard_id=int(dataviz["dashboard_id"]),
                dataset_id=int(args.get("dataset_id") or 0),
                metrics=[str(m) for m in args.get("metrics") or []],
                dimensions=[str(d) for d in args.get("dimensions") or []],
                native_filters_key=dataviz.get("native_filters_key"),
                period=args.get("period") or None,
                compare_period=args.get("compare_period") or None,
                time_column=args.get("time_column") or None,
                row_limit=int(args.get("row_limit") or 50),
                time_grain=args.get("time_grain") or None,
            )
        except QueryRefused as exc:
            return _fail(str(exc))
        except BiError as exc:
            return _fail(exc.message)
        return ToolOutcome(
            ok=True,
            summary=f"{answer.title}: {len(answer.rows)} rows",
            table={"title": answer.title, "columns": answer.columns, "rows": answer.rows},
            data={"columns": answer.columns, "rows": answer.rows[:50], "facts": answer.facts},
            evidence={"source": "dataviz_dataset", **answer.facts},
            # Kept on the tool step only (never sent to the model): what
            # "Add to Dataviz" draws again on the same dataset.
            trace_detail={
                "dataviz_source": {
                    key: answer.facts.get(key)
                    for key in (
                        "dashboard_id",
                        "dataset_id",
                        "dataset",
                        "dimensions",
                        "measures",
                        "period",
                        "compare_period",
                        "time_column",
                        "time_grain",
                        "filters_applied",
                        "concepts",
                    )
                }
            },
            metadata={
                "settles_capabilities": ["semantic_query"]
                + (["ontology_resolve"] if answer.facts.get("concepts") else [])
                + (
                    ["diagnose_change", "compute_metrics"]
                    if answer.facts.get("compare_period")
                    else []
                )
            },
        )


class ChartSourceTool(_Tool):
    name = "dataviz_chart_source"
    description = (
        "Explain where one Dataviz chart's numbers come from: chart, dataset, Semantic View, "
        "metrics and the approved business concepts, with any missing link stated."
    )
    parameters = {
        "type": "object",
        "properties": {"chart_id": {"type": "integer", "minimum": 1}},
        "required": ["chart_id"],
        "additionalProperties": False,
    }

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.superset.dashboard_context import chart_source

        user = _user(context)
        if not user:
            return _fail("A signed-in owner is required.")
        try:
            chart_id = int(invocation.arguments.get("chart_id") or 0)
        except (TypeError, ValueError):
            return _fail("Give a chart id from dataviz_context.")
        from app.modules.superset.client import BiError

        try:
            source = await chart_source(user, chart_id)
        except BiError as exc:
            return _fail(f"The chart could not be read right now: {exc.message}")
        return ToolOutcome(ok=True, summary=f"Source of chart {chart_id}", data=source)


class PlanAddTool(_Tool):
    name = "dataviz_plan_add"
    may_settle = frozenset({"dataviz_apply"})
    description = (
        "Prepare adding one result (id from dataviz_list_results) to a Dataviz dashboard: the "
        "dashboard open in Dataviz ('current'), a dashboard id, or a new dashboard by title. "
        "For a CONVERTIBLE result pass the alternative's strategy_id only after the user chose it "
        "knowing what it loses. Returns a human diff and an action_token; nothing is changed. Show "
        "the diff, then call dataviz_apply only when the user wants it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "result_id": {"type": "string"},
            "dashboard": {
                "description": "'current', a dashboard id, or omit with new_dashboard_title.",
                "type": ["string", "integer"],
            },
            "new_dashboard_title": {"type": "string", "maxLength": 250},
            "alternative": {"type": "string", "description": "A strategy_id the user approved."},
            "title": {"type": "string", "maxLength": 250},
        },
        "required": ["result_id"],
        "additionalProperties": False,
    }

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.superset.dataviz import preview_change
        from app.modules.superset.previews import sign
        from app.modules.superset.publication import PublishError, Target

        user = _user(context)
        if not user:
            return _fail("A signed-in owner is required.")
        args = invocation.arguments
        locator, item = await _locator(context, user, args.get("result_id", ""))
        if locator is None:
            return _fail("No result has that id. List the results first.")
        dashboard = args.get("dashboard")
        dataviz = getattr(context, "dataviz", None) or {}
        if dashboard == "current":
            dashboard = dataviz.get("dashboard_id")
            if not dashboard:
                return _fail("No dashboard is open in Dataviz. Ask which dashboard to use.")
        if args.get("new_dashboard_title"):
            target = Target("new", title=str(args["new_dashboard_title"]).strip())
        elif dashboard:
            try:
                target = Target("existing", dashboard_id=int(dashboard))
            except (TypeError, ValueError):
                return _fail("The dashboard must be 'current', an id, or a new title.")
        else:
            return _fail("Choose the dashboard: 'current', an id, or a new dashboard title.")
        try:
            plan = await preview_change(
                user,
                ref=sign(locator, user),
                target=target,
                approved_transform_id=args.get("alternative") or None,
                title=args.get("title"),
            )
        except PublishError as exc:
            # A verified refusal (role, permission, capability) is the answer.
            return ToolOutcome(
                ok=True,
                summary=f"Not prepared: {exc.message}",
                data={
                    "prepared": False,
                    "code": exc.code,
                    "message": exc.message,
                    "capability": exc.capability,
                },
                metadata={"settles_capabilities": ["dataviz_apply"]},
            )
        _remember_preview(plan["action_token"], "; ".join(plan.get("changes") or []))
        from app.modules.superset.dataviz import remember_pending

        await remember_pending(user, getattr(context, "thread_id", None), plan["action_token"])
        return ToolOutcome(
            ok=True,
            summary="Prepared a Dataviz change (not applied)",
            data={"result_id": item["result_id"], **plan},
        )


class ApplyTool(_Tool):
    name = "dataviz_apply"
    description = (
        "Apply a change prepared by dataviz_plan_add, exactly as prepared. Needs the user's "
        "approval. Refuses when the dashboard changed since, when it has unsaved edits in "
        "Dataviz, or when the role changed. Report only what this tool confirms."
    )
    parameters = {
        "type": "object",
        "properties": {"action_token": {"type": "string"}},
        "required": ["action_token"],
        "additionalProperties": False,
    }
    classification: ToolClassification = "session_change"
    requires_consent = True
    #: Applying a change prepared earlier in the conversation makes its
    #: preparation (listing, checking, planning) moot for this turn.
    may_settle = frozenset({"dataviz_list_results", "dataviz_check_result", "dataviz_plan_add"})

    def preview(self, invocation: ToolInvocation) -> str:
        token = str(invocation.arguments.get("action_token") or "")
        return _PREVIEWS.get(token) or "Apply the prepared Dataviz change"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.superset.dataviz import apply_change
        from app.modules.superset.publication import PublishError

        user = _user(context)
        if not user:
            return _fail("A signed-in owner is required.")
        dataviz = getattr(context, "dataviz", None) or {}
        try:
            result = await apply_change(
                user,
                token=str(invocation.arguments.get("action_token") or ""),
                # One request per consented call: a retried tool call reuses it.
                idempotency_key=f"tool-{invocation.tool_call_id}"[:128]
                if invocation.tool_call_id
                else f"tool-{uuid4().hex}",
                editor_state=dataviz.get("edit_state") or "unknown",
                open_dashboard_id=dataviz.get("dashboard_id"),
                agent_id=getattr(context, "agent_id", None),
            )
        except PublishError as exc:
            return ToolOutcome(ok=False, summary="", error=exc.message, data={"code": exc.code})
        return ToolOutcome(
            ok=True,
            summary=f"Added to dashboard {result.get('dashboard_id')} (verified)",
            data=result,
            metadata={
                "dataviz_publication": result,
                "settles_capabilities": sorted(self.may_settle),
            },
        )


TOOLS = (
    QueryTool(),
    ListResultsTool(),
    CheckResultTool(),
    ContextTool(),
    ChartSourceTool(),
    PlanAddTool(),
    ApplyTool(),
)

PROMPT = """
## Dataviz

Studio has a Dataviz tab: the user's governed BI dashboards (Superset), opened as their own role.
- To add something from this conversation to a dashboard, call dataviz_list_results, then
  dataviz_check_result for the exact result. Never say a result is supported, or convert it,
  without that check.
- SUPPORTED: call dataviz_plan_add, show the user the planned change, and call dataviz_apply only
  after they want it.
- CONVERTIBLE: explain in plain words what Dataviz cannot keep, offer only alternatives whose
  meaning is 'kept' or 'partial', say what each loses, and ask. If the user declines, change
  nothing.
- UNSUPPORTED or UNKNOWN: explain the real limitation; suggest re-asking the question so the
  governed query computes the view, never a misleading chart.
- About an open dashboard, call dataviz_context first. Name charts, sources and business concepts
  exactly as returned. If filter_state_status is not 'verified', say the current filters are
  unknown and do not present an unfiltered number as the dashboard's filtered number.
- Never say a dashboard changed unless dataviz_apply confirmed it.
""".strip()


def register(registry: Any) -> None:
    for tool in TOOLS:
        if registry.get(tool.name) is None:
            registry.register(tool)
