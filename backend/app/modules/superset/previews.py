"""Server-verified references to a result shown in a Studio answer.

A ``preview_ref`` names one table, chart, KPI or map that Nova itself recorded
in a caller's conversation (docs/arch-19-studio-dataviz.md, D1). The client
only *locates* it (thread, message, content and, for an interactive answer,
the node); everything else is read back from Nova's persisted message:

* the statement comes only from the producing tool step, followed by
  ``tool_call_id`` (or a chart's ``evidence_id``) inside the same message, or
  from a statement the block recorded itself. There is no search through
  earlier turns: a result without an exact producer has no provenance;
* the visualization comes from the stored block (``viz_spec`` / ``chart_spec``
  or the OpenUI program node);
* Semantic View and ontology meaning come from the producing step's
  ``semantic_evidence``.

The signed form binds the locator to the principal, so a reference is useless
to anyone else. It carries no SQL, rows or credentials.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.config import settings
from app.core.metadata_scope import actor_for, metadata_transaction
from app.modules.assistant.metadata_repository import AssistantMetadataRepository

from .openui_nodes import DataNode, OpenUiParseError, data_nodes

RenderKind = Literal["table", "chart", "kpi", "map", "route_map"]
_PREFIX = "pv1"
_MAX_REF = 2048
_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")
_PRODUCERS = frozenset({"semantic_query", "query_execute"})


class PreviewError(ValueError):
    """The reference is malformed, forged, someone else's, or gone."""


@dataclass(frozen=True)
class Locator:
    thread_id: str
    message_id: str
    content_id: str
    #: For a node inside an interactive answer: component, evidence and props.
    node: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"t": self.thread_id, "m": self.message_id, "c": self.content_id}
        if self.node:
            out["n"] = self.node
        return out

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Locator:
        thread, message, content = value.get("t"), value.get("m"), value.get("c")
        node = value.get("n")
        if not all(
            isinstance(item, str) and _ID.match(item) for item in (thread, message, content)
        ):
            raise PreviewError("This result reference is not valid.")
        if node is not None and not isinstance(node, dict):
            raise PreviewError("This result reference is not valid.")
        return cls(thread, message, content, node)  # type: ignore[arg-type]


def _key() -> bytes:
    if not settings.SECRET_KEY:
        raise PreviewError("Result references need the deployment signing key.")
    return hmac.new(
        settings.SECRET_KEY.encode(), b"nova.dataviz.preview.v1", hashlib.sha256
    ).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(locator: Locator, user: dict[str, Any]) -> str:
    payload = _b64(json.dumps(locator.as_dict(), sort_keys=True, separators=(",", ":")).encode())
    mac = hmac.new(_key(), f"{user['username']}\n{payload}".encode(), hashlib.sha256).digest()
    return f"{_PREFIX}.{payload}.{_b64(mac[:18])}"


def verify(ref: str, user: dict[str, Any]) -> Locator:
    if not isinstance(ref, str) or len(ref) > _MAX_REF or ref.count(".") != 2:
        raise PreviewError("This result reference is not valid.")
    prefix, payload, mac = ref.split(".")
    expected = hmac.new(_key(), f"{user['username']}\n{payload}".encode(), hashlib.sha256).digest()
    try:
        given = _unb64(mac)
    except ValueError as exc:
        raise PreviewError("This result reference is not valid.") from exc
    if prefix != _PREFIX or not hmac.compare_digest(given, expected[:18]):
        raise PreviewError("This result reference is not valid.")
    try:
        return Locator.from_dict(json.loads(_unb64(payload)))
    except (ValueError, TypeError) as exc:
        raise PreviewError("This result reference is not valid.") from exc


def fingerprint(*parts: object) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def normal_sql(sql: str) -> str:
    return " ".join(sql.strip().rstrip(";").split())


@dataclass
class ResolvedPreview:
    locator: Locator
    render_kind: RenderKind
    title: str
    #: The exact read-only statement behind the rows, or None without provenance.
    sql: str | None
    provenance: Literal["tool_step", "block_statement", "none"]
    producer_tool: str | None
    columns: list[str]
    #: Normalized visualization, renderer-neutral.
    visualization: dict[str, Any]
    #: Functions the answer applied to the rows before drawing (OpenUI only),
    #: outermost first, with their literal arguments.
    transforms: list[dict[str, Any]] = field(default_factory=list)
    temporal: set[str] = field(default_factory=set)
    semantic: dict[str, Any] | None = None
    agent_id: str | None = None
    #: A chart block's own ``viz_spec`` / ``chart_spec``, for the plan.
    viz_spec: dict[str, Any] | None = None
    chart_spec: dict[str, Any] | None = None
    #: Why provenance is missing or inconsistent, when it is.
    provenance_issue: str | None = None
    #: The rows Nova recorded with the result (bounded; never returned to
    #: clients by this module), used to check what the native view drew.
    rows: list[dict[str, Any]] = field(default_factory=list)
    #: A Dataviz answer read from a dashboard's own dataset (``dataviz_query``):
    #: the dataset, its metrics and columns, and the period, as recorded on the
    #: producing tool step. It has no SQL; Dataviz draws it on that dataset.
    dataset_source: dict[str, Any] | None = None

    @property
    def business_context(self) -> dict[str, Any] | None:
        value = (self.semantic or {}).get("business_context")
        return value if isinstance(value, dict) else None

    def query_fingerprint(self) -> str | None:
        if self.sql:
            return fingerprint(normal_sql(self.sql))
        if self.dataset_source:
            source = self.dataset_source
            return fingerprint(
                "dataset",
                source.get("dataset_id"),
                source.get("measures"),
                source.get("dimensions"),
                source.get("period"),
                source.get("time_column"),
                source.get("time_grain"),
            )
        return None


async def _message(
    locator: Locator, user: dict[str, Any]
) -> tuple[list[dict], dict | None, str | None]:
    actor = actor_for(user)
    async with metadata_transaction(actor, read_only=True) as scope:
        repository = AssistantMetadataRepository(scope.connection, actor)
        message = await repository.get_message(locator.thread_id, locator.message_id)
        thread = await repository.get_thread(locator.thread_id)
    if not message or not thread or message["role"] != "assistant":
        raise PreviewError("That result is not in your conversations.")
    stamp, steps, agent_id = message["security_context"], message["steps"], thread["agent_id"]
    steps = json.loads(steps) if isinstance(steps, str) else (steps or [])
    stamp = json.loads(stamp) if isinstance(stamp, str) else stamp
    return [step for step in steps if isinstance(step, dict)], stamp, agent_id


def _producer(
    steps: list[dict], *, tool_call_id: str | None = None, evidence_id: str | None = None
) -> dict | None:
    for step in steps:
        if step.get("kind") != "tool" or step.get("status") not in ("done", None):
            continue
        if tool_call_id and step.get("tool_call_id") == tool_call_id:
            return step
        if evidence_id and step.get("evidence_id") == evidence_id:
            return step
    return None


def _statement(step: dict | None) -> str | None:
    if not step or step.get("name") not in _PRODUCERS:
        return None
    sql = str(step.get("preview") or "").strip()
    return sql if re.match(r"^\s*(SELECT|WITH)\b", sql, re.IGNORECASE) else None


def _dataset_source(step: dict | None) -> dict[str, Any] | None:
    """The dashboard-dataset query a ``dataviz_query`` step recorded, if whole."""
    if not step or step.get("name") != "dataviz_query":
        return None
    source = ((step.get("trace_detail") or {}).get("dataviz_source")) or None
    if not isinstance(source, dict):
        return None
    try:
        source["dataset_id"] = int(source.get("dataset_id") or 0)
    except (TypeError, ValueError):
        return None
    if source["dataset_id"] <= 0 or not source.get("measures"):
        return None
    return source


def _dataset_semantic(source: dict[str, Any]) -> dict[str, Any] | None:
    """Ontology concepts the dataset's charts are bound to, as business context."""
    concepts = source.get("concepts") or []
    return {"business_context": {"concepts": concepts}} if concepts else None


def _json(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _node_matches(node: DataNode, wanted: dict[str, Any]) -> bool:
    if node.component != wanted.get("component") or node.evidence_id != wanted.get("evidence_id"):
        return False
    for key in ("x", "y", "type", "title", "column", "longitude", "latitude", "geometry"):
        if (
            key in wanted
            and wanted[key] != node.prop(key)
            and not (key == "type" and wanted[key] == "bar" and node.prop(key) is None)
        ):
            return False
    return True


def _ui_node(block: dict, wanted: dict[str, Any]) -> DataNode | None:
    try:
        nodes = data_nodes(str(block.get("program") or ""))
    except OpenUiParseError:
        return None
    matches = [node for node in nodes if _node_matches(node, wanted)]
    return matches[0] if len(matches) == 1 else None


#: A notebook result: ``thread_id`` is ``notebook:<notebook id>`` and
#: ``message_id`` the execution that produced the table.
NOTEBOOK_PREFIX = "notebook:"


async def _resolve_notebook(locator: Locator, user: dict[str, Any]) -> ResolvedPreview:
    """A SQL cell's table, read back from the notebook's own record.

    Only a cell whose query reads Nova catalogs alone has a governed statement
    Dataviz can run again: the Nova SQL the worker checked when the cell ran
    (``nova_sql`` on the table), with the cell unchanged since, under the same
    role. Results built from local files or Python values have none.
    """
    import json as _json

    from app.modules.notebooks.document import NotebookFormatError
    from app.modules.notebooks.documents import notebook_documents
    from app.modules.notebooks.repository import notebook_repository
    from app.modules.workspaces.service import workspace_service

    notebook_id = locator.thread_id[len(NOTEBOOK_PREFIX) :]
    from app.core.metadata_scope import actor_for

    row = await notebook_repository.get_execution(actor_for(user), notebook_id, locator.message_id)
    if row is None or row.get("status") != "succeeded" or row.get("cell_kind") != "sql":
        raise PreviewError("That notebook result is no longer available.")
    if (row.get("active_role") or "") != (user.get("active_role") or ""):
        raise PreviewError(
            "This result was produced under another role. Run the cell again under your "
            "current role to use it."
        )
    try:
        entry, document = await notebook_documents.get_for(user, notebook_id)
        cell = document.cell(str(row["cell_id"]))
    except (NotebookFormatError, Exception) as exc:  # noqa: BLE001 - one safe message
        raise PreviewError("That notebook cell is no longer there.") from exc
    outputs: list[dict[str, Any]] = []
    if row.get("output_key"):
        try:
            outputs = _json.loads(await workspace_service.read_internal_object(row["output_key"]))
        except Exception:  # noqa: BLE001
            outputs = []
    table = next((o.get("table") for o in outputs if o.get("type") == "table"), None)
    columns = [str(c.get("name")) for c in (table or {}).get("schema", []) if c.get("name")]
    rows = [
        dict(zip(columns, values, strict=False))
        for values in ((table or {}).get("rows") or [])[:200]
    ]
    nova_sql = str((table or {}).get("nova_sql") or "").strip()
    unchanged = cell.source_revision == row.get("source_revision")
    issue = None
    if not nova_sql:
        issue = (
            "This result uses data only the notebook has (local files, Python values or "
            "DuckDB-only SQL), so Dataviz cannot keep it up to date. A SQL cell that reads "
            "only Nova catalogs can be added."
        )
    elif not unchanged:
        issue = "The cell changed since it ran. Run it again to add this result."
    return ResolvedPreview(
        locator=locator,
        render_kind="table",
        title=cell.name or entry.get("name", "Notebook result").removesuffix(".ipynb"),
        sql=nova_sql if nova_sql and unchanged else None,
        provenance="tool_step" if nova_sql and unchanged else "none",
        producer_tool="query_execute" if nova_sql else "notebook",
        columns=columns,
        visualization={"kind": "table", "columns": columns},
        provenance_issue=issue,
        rows=rows,
    )


async def resolve(locator: Locator, user: dict[str, Any]) -> ResolvedPreview:
    """Read the located result back from Nova's own record of it."""
    from app.modules.assistant.security import (
        observation_context,
        same_ui_context,
        session_security,
    )

    if locator.thread_id.startswith(NOTEBOOK_PREFIX):
        return await _resolve_notebook(locator, user)

    steps, stamp, agent_id = await _message(locator, user)
    current = observation_context(session_security(user))
    if not same_ui_context(stamp, current):
        raise PreviewError(
            "This result was produced under another role or session. Run the question again "
            "under your current role to use it."
        )
    blocks = [
        step
        for step in steps
        if step.get("content_id") == locator.content_id
        and step.get("kind") in ("table", "chart", "ui")
    ]
    if len(blocks) != 1:
        raise PreviewError("That result is no longer in the conversation.")
    block = blocks[0]
    kind = block["kind"]

    if kind == "table":
        producer = _producer(steps, tool_call_id=_string(block.get("tool_call_id")))
        sql = _statement(producer)
        columns = [str(c) for c in block.get("columns") or []]
        source = _dataset_source(producer)
        if source is not None:
            return ResolvedPreview(
                locator=locator,
                render_kind="table",
                title=str(block.get("title") or "Result")[:256],
                sql=None,
                provenance="tool_step",
                producer_tool="dataviz_query",
                columns=columns,
                visualization={"kind": "table", "columns": columns},
                semantic=_dataset_semantic(source),
                agent_id=agent_id,
                dataset_source=source,
            )
        return ResolvedPreview(
            locator=locator,
            render_kind="table",
            title=str(block.get("title") or "Result")[:256],
            sql=sql,
            provenance="tool_step" if sql else "none",
            producer_tool=(producer or {}).get("name"),
            columns=columns,
            visualization={"kind": "table", "columns": columns},
            semantic=(producer or {}).get("semantic_evidence"),
            agent_id=agent_id,
            provenance_issue=None if sql else "The table's producing query was not recorded.",
        )

    if kind == "chart":
        viz_spec = _json(block.get("viz_spec"))
        chart_spec = _json(block.get("chart_spec"))
        charting = _producer(steps, tool_call_id=_string(block.get("tool_call_id")))
        source_evidence = ((charting or {}).get("arguments") or {}).get("evidence_id")
        producer = (
            _producer(steps, evidence_id=source_evidence)
            if isinstance(source_evidence, str)
            else None
        )
        if producer is None and charting is not None and charting.get("name") in _PRODUCERS:
            producer = charting
        sql = _statement(producer)
        issue = None if sql else "The chart's producing query was not recorded."
        recorded = ((viz_spec or {}).get("source") or {}).get("sql")
        if (
            sql
            and isinstance(recorded, str)
            and recorded.strip()
            and normal_sql(recorded) != normal_sql(sql)
        ):
            issue, sql = (
                "The chart's recorded source differs from the query that produced it.",
                None,
            )
        title = str((chart_spec or {}).get("title") or (viz_spec or {}).get("title") or "Chart")
        field_types = (viz_spec or {}).get("field_types") or {}
        temporal = {name for name, kind_ in field_types.items() if kind_ == "temporal"}
        columns = [str(c) for c in field_types] if field_types else []
        if not columns:
            table = next(
                (
                    s
                    for s in steps
                    if s.get("kind") == "table"
                    and producer
                    and s.get("tool_call_id") == producer.get("tool_call_id")
                ),
                None,
            )
            columns = [str(c) for c in (table or {}).get("columns") or []]
        marks = ((viz_spec or {}).get("marks") or {}).get("type") or (chart_spec or {}).get("mark")
        if isinstance(marks, dict):
            marks = marks.get("type")
        return ResolvedPreview(
            locator=locator,
            render_kind="chart",
            title=title[:256],
            sql=sql,
            provenance="tool_step" if sql else "none",
            producer_tool=(producer or {}).get("name"),
            columns=columns,
            visualization={
                "kind": str(marks or "unknown"),
                "source": "viz_spec" if viz_spec else "vega",
            },
            temporal=temporal,
            semantic=(producer or {}).get("semantic_evidence"),
            agent_id=agent_id,
            viz_spec=viz_spec,
            chart_spec=chart_spec,
            provenance_issue=issue,
        )

    # An interactive answer: one node of its program.
    wanted = locator.node or {}
    evidence_id = _string(wanted.get("evidence_id"))
    provenance = block.get("provenance") or {}
    if not evidence_id or evidence_id not in (provenance.get("evidence_ids") or []):
        raise PreviewError("That view does not belong to this answer.")
    node = _ui_node(block, wanted)
    if node is None:
        raise PreviewError("That view could not be found in the answer.")
    producer = _producer(
        steps, tool_call_id=_string((provenance.get("tool_calls") or {}).get(evidence_id))
    )
    sql = _statement(producer)
    origin: Literal["tool_step", "block_statement", "none"] = "tool_step" if sql else "none"
    if not sql:
        recorded = (provenance.get("statements") or {}).get(evidence_id)
        if isinstance(recorded, str) and re.match(r"^\s*(SELECT|WITH)\b", recorded, re.IGNORECASE):
            sql, origin = recorded.strip(), "block_statement"
    rows = (block.get("evidence") or {}).get(evidence_id) or []
    columns = [str(c) for c in (rows[0].keys() if rows and isinstance(rows[0], dict) else [])]
    options = node.prop("options") if isinstance(node.prop("options"), dict) else {}
    render: RenderKind
    if node.component == "GeoMap":
        render = (
            "route_map" if options.get("layer") == "route" and options.get("sequence") else "map"
        )
    elif node.component == "Kpi":
        render = "kpi"
    elif node.component == "Chart":
        render = "chart"
    else:
        render = "table"
    visualization: dict[str, Any] = {"component": node.component}
    for key in (
        "x",
        "y",
        "type",
        "series",
        "column",
        "columns",
        "longitude",
        "latitude",
        "geometry",
        "label",
    ):
        value = node.prop(key)
        if isinstance(value, (str, list, int, float)) or value is None:
            visualization[key] = value
    if render == "route_map":
        visualization["sequence"] = options.get("sequence")
    if node.component == "Chart" and visualization.get("type") is None:
        visualization["type"] = "bar"
    title = str(node.prop("title") or node.prop("label") or "View")
    return ResolvedPreview(
        locator=locator,
        render_kind=render,
        title=title[:256],
        sql=sql,
        provenance=origin,
        producer_tool=(producer or {}).get("name"),
        columns=columns,
        visualization=visualization,
        transforms=(
            [
                {
                    "name": call.name,
                    "args": [a for a in call.args[1:] if isinstance(a, (str, int, float))],
                    "exact": all(isinstance(a, (str, int, float)) for a in call.args[1:]),
                }
                for call in node.transforms
            ]
            if not node.opaque
            else [{"name": "<expression>", "args": [], "exact": False}]
        ),
        semantic=(producer or {}).get("semantic_evidence"),
        agent_id=agent_id,
        provenance_issue=None if sql else "The view's producing query was not recorded.",
        rows=[row for row in rows if isinstance(row, dict)][:5000],
    )
