"""Dispatch a surface-owned client action without granting DOM or script access."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.common.audit import write_audit_log
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

_ARGUMENTS: dict[str, dict[str, type]] = {
    "surface.refresh": {},
    "surface.set_filter": {"filter": str, "value": str},
    "surface.select": {"id": str},
    "tab.open": {"tab": str},
    "editor.focus": {},
    # SQL put into the person's editor as a change they review and accept;
    # nothing is run or saved until they do.
    "editor.propose_sql": {"sql": str, "mode": str},
    # A notebook cell the person reviews and accepts; nothing runs until they
    # do. ``cell_id`` is the cell to replace or to insert below.
    "notebook.propose_cell": {
        "cell_id": str,
        "kind": str,
        "engine": str,
        "source": str,
        "mode": str,
    },
    "notebook.focus_cell": {"cell_id": str},
}
#: Longest value per argument; anything not listed is a short name.
_LIMITS = {"sql": 20_000, "source": 20_000}
_CHOICES = {
    "mode": {"replace", "insert"},
    "kind": {"python", "sql", "markdown"},
    "engine": {"duckdb", "nova", "none"},
}
_CAPABILITY_CHOICES = {
    "notebook.propose_cell": {"mode": {"replace", "insert_below"}},
}
#: What the person reads on the action card, instead of the capability name.
_PLAIN = {
    "surface.refresh": "Refresh this page",
    "surface.set_filter": "Change a filter on this page",
    "surface.select": "Select an item on this page",
    "tab.open": "Open a tab on this page",
    "editor.focus": "Move to the SQL editor",
    "editor.propose_sql": "Put the SQL in your editor for you to review",
    "notebook.propose_cell": "Put a cell in your notebook for you to review",
    "notebook.focus_cell": "Move to a cell in your notebook",
}


#: What Neva learns once the action is sent (it is not done yet).
_SENT = {
    "editor.propose_sql": (
        "The page shows the SQL in the person's editor as a suggested change "
        "beside their current text; it takes effect only if they accept it, and "
        "nothing has run. Finish with a short answer in the person's language: "
        "what you changed and why it helps, and that they can accept or dismiss "
        "it in the editor. Do not call it pending or unconfirmed, and do not repeat the "
        "whole SQL or the check results."
    ),
    "notebook.propose_cell": (
        "The notebook shows the cell as a suggested change; it takes effect only if the "
        "person accepts it, and nothing has run. Finish with a short answer in the "
        "person's language: what the cell does and that they can accept, accept and "
        "run, or dismiss it in the notebook. Do not call it pending or unconfirmed, and "
        "do not repeat the whole code."
    ),
}
#: The words a person reads when Neva acts on the page without the model.
SENT_PLAINLY = "I asked this page to do that; it confirms when done."


def _valid_arguments(capability: str, args: Any) -> bool:
    fields = _ARGUMENTS.get(capability)
    if fields is None or not isinstance(args, dict) or set(args) != set(fields):
        return False
    choices = {**_CHOICES, **_CAPABILITY_CHOICES.get(capability, {})}
    return all(
        isinstance(args[name], kind)
        # A filter value may be empty: that clears the filter.
        and (name == "value" or bool(args[name].strip()))
        and len(args[name]) <= _LIMITS.get(name, 128)
        and (name not in choices or args[name] in choices[name])
        for name, kind in fields.items()
    )


def _cell_problem(args: Any) -> str | None:
    """Why a proposed notebook cell cannot go to the page, or ``None``.

    Python must parse; Nova SQL must have passed validate_sql in this turn (the
    caller checks that); DuckDB SQL and Markdown are the person's to review.
    """
    if not isinstance(args, dict):
        return None
    if args.get("kind") == "python":
        import ast

        try:
            ast.parse(str(args.get("source") or ""))
        except SyntaxError as exc:
            return f"The Python does not parse (line {exc.lineno}): fix it, then propose it."
    if args.get("kind") == "sql" and args.get("engine") not in {"duckdb", "nova"}:
        return "A SQL cell runs on engine duckdb or nova."
    return None


def _normalize(sql: str) -> str:
    from app.modules.assistant.service import normalize_sql

    return normalize_sql(sql)


def _shape(capability: str) -> str:
    """The argument shape, with the page's choices where the page names them."""
    fields = _ARGUMENTS.get(capability, {})
    hints = {
        "filter": "one of the filters in the page's view.filters",
        "value": "the filter value, empty to clear",
        "id": "an item id shown on the page",
        "tab": "a tab name on the page",
        "sql": "the SQL",
        "mode": (
            "replace or insert_below" if capability == "notebook.propose_cell"
            else "replace or insert"
        ),
        "cell_id": "a cell id from the notebook context",
        "kind": "python, sql or markdown",
        "engine": "duckdb or nova for SQL, none otherwise",
        "source": "the cell's code or text",
    }
    return "{" + ", ".join(f'"{name}": {hints.get(name, "text")}' for name in fields) + "}"


class InvokeClientCapabilityTool:
    name = "invoke_client_capability"
    description = (
        "Request a safe capability registered by the current Nova application "
        "surface, such as changing a filter, opening a tab, refreshing, or "
        "focusing the editor. On the SQL Workspace, editor.propose_sql puts SQL "
        "the person asked you to write, change or speed up into their editor as a "
        "change they review and accept: check it with validate_sql first; mode "
        "'replace' swaps the editor's SQL (or their selection) for it, 'insert' "
        "adds it below. In a notebook, notebook.propose_cell puts a Python, SQL or "
        "Markdown cell in as a change they review: mode 'replace' changes the cell "
        "named by cell_id, 'insert_below' adds one after it; check Nova-engine SQL "
        "with validate_sql first. The action is pending until a matching application "
        "outcome event arrives. Never claim it completed from this tool result."
    )
    parameters = {
        "type": "object",
        "properties": {
            "capability": {"type": "string", "enum": sorted(_ARGUMENTS)},
            "args": {"type": "object", "additionalProperties": True},
        },
        "required": ["capability", "args"],
        "additionalProperties": False,
    }
    classification: ToolClassification = "read_only"
    requires_consent = False

    def preview(self, invocation: ToolInvocation) -> str:
        capability = str(invocation.arguments.get("capability") or "")
        return _PLAIN.get(capability, "Do an action on this page")

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        app = getattr(context, "app_context", None)
        if app is None:
            return ToolOutcome(
                ok=False, summary="", error="No active application surface is available."
            )
        capability = invocation.arguments.get("capability")
        args = invocation.arguments.get("args")
        if not isinstance(capability, str) or capability not in app.capabilities:
            return ToolOutcome(
                ok=False, summary="", error="That capability is unavailable on the current surface."
            )
        checked = getattr(context, "checked_sql", None)
        proposed_sql = None
        if capability == "editor.propose_sql" and isinstance(args, dict):
            proposed_sql = str(args.get("sql") or "")
        elif (
            capability == "notebook.propose_cell"
            and isinstance(args, dict)
            and args.get("kind") == "sql"
            and args.get("engine") == "nova"
        ):
            proposed_sql = str(args.get("source") or "")
        if capability == "notebook.propose_cell":
            problem = _cell_problem(args)
            if problem:
                return ToolOutcome(ok=False, summary="", recoverable=True, error=problem)
        if (
            proposed_sql is not None
            and isinstance(checked, set)
            and _normalize(proposed_sql) not in checked
        ):
            # Only SQL that passed validate_sql in this turn reaches the editor.
            return ToolOutcome(
                ok=False, summary="", recoverable=True,
                error="Check this exact SQL with validate_sql first, then propose it.",
            )
        if not _valid_arguments(capability, args):
            # Recoverable: the model learns the expected shape and tries again.
            return ToolOutcome(
                ok=False, summary="", recoverable=True,
                error=f"Use args {_shape(capability)} for {capability}.",
            )
        audit_id = None
        if isinstance(getattr(context, "user", None), dict):
            try:
                audit_id = await write_audit_log(
                    event_type="assistant_client_action",
                    user_name=context.user_name,
                    action=capability,
                    object_type="NOVA_SURFACE",
                    object_name=app.surface.id,
                    status="PENDING",
                    session_id=getattr(context, "audit_session_id", None),
                    active_role=getattr(context, "role", None),
                )
            except Exception:
                return ToolOutcome(
                    ok=False,
                    summary="",
                    error="The page action could not be recorded for audit.",
                    error_class="AUDIT_UNAVAILABLE",
                )
        action = {
            "capability": capability,
            "args": args,
            "correlation_id": str(uuid4()),
            "surface_id": app.surface.id,
        }
        return ToolOutcome(
            ok=True,
            summary=_SENT.get(
                capability,
                "The page applies this and shows its outcome to the person. Tell them in one "
                "short sentence, in their language, what you set on the page (for example "
                "'I set the Protected filter'); do not call it pending.",
            ),
            data={
                "status": "pending",
                "capability": capability,
                "correlation_id": action["correlation_id"],
            },
            evidence={"audit_id": audit_id} if audit_id else {},
            metadata={"client_action": action},
        )


invoke_client_capability_tool = InvokeClientCapabilityTool()
