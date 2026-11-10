"""Neva's notebook tools.

Neva helps with the notebook the person has open: it reads the cells, the
runtime and the last results (``notebook_context``), proposes new or changed
cells through the page (``notebook.propose_cell``, reviewed and accepted by
the person), and — only with the person's approval each time — runs a cell or
adds an approved package. Every action goes through the notebook service as
the signed-in user under their active role, exactly as the page does it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

#: Cells and output read back to the model; enough to reason, never a dump.
_MAX_CELLS = 60
_MAX_SOURCE = 4000
_MAX_OUTPUT_TEXT = 1500
_PREVIEW_ROWS = 10
_RUN_WAIT_SECONDS = 120


def _user(context: Any) -> dict[str, Any] | None:
    user = getattr(context, "user", None)
    return user if isinstance(user, dict) else None


def _open_notebook(context: Any) -> str | None:
    """The notebook on the person's screen, if the page is a notebook."""
    app = getattr(context, "app_context", None)
    entity = getattr(app, "entity", None) if app is not None else None
    if entity is None or getattr(entity, "type", None) != "notebook":
        return None
    return getattr(entity, "id", None) or None


def _fail(message: str, *, recoverable: bool = False) -> ToolOutcome:
    return ToolOutcome(ok=False, summary="", error=message, recoverable=recoverable)


def _summarize_outputs(outputs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for output in outputs or []:
        kind = output.get("type")
        if kind in {"stream", "text", "status", "markdown"}:
            text = str(output.get("text") or "")
            summary.append({"type": kind, "text": text[-_MAX_OUTPUT_TEXT:]})
        elif kind == "error":
            traceback = [str(line) for line in output.get("traceback") or []][-12:]
            summary.append(
                {
                    "type": "error",
                    "name": output.get("ename"),
                    "message": str(output.get("evalue") or "")[:_MAX_OUTPUT_TEXT],
                    "traceback_tail": traceback,
                }
            )
        elif kind == "table":
            table = output.get("table") or {}
            summary.append(
                {
                    "type": "table",
                    "name": output.get("name"),
                    "columns": table.get("schema", [])[:40],
                    "rows": (table.get("rows") or [])[:_PREVIEW_ROWS],
                    "rows_shown": len(table.get("rows") or []),
                    "total_rows": table.get("total_rows"),
                    "computed": table.get("origin"),
                }
            )
        elif kind in {"image", "svg", "html"}:
            summary.append({"type": kind, "note": "a rendered figure or page"})
        if len(summary) >= 8:
            break
    return summary


class NotebookContextTool:
    name = "notebook_context"
    description = (
        "Read the notebook the person has open: its cells in order (kind, result name, "
        "SQL engine, source), the runtime state, the role Nova data is read as, the last "
        "result or error of each cell, and the variables defined. Use it before you "
        "explain, fix or extend the notebook. Pass cell_id to name the cell the person "
        "is asking about; its full last result is included."
    )
    parameters = {
        "type": "object",
        "properties": {"cell_id": {"type": "string", "maxLength": 64}},
        "additionalProperties": False,
    }
    classification: ToolClassification = "read_only"
    requires_consent = False

    def preview(self, invocation: ToolInvocation) -> str:
        return "Read the open notebook"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.notebooks.documents import notebook_documents
        from app.modules.notebooks.runtime.service import notebook_runtime_service

        user = _user(context)
        notebook_id = _open_notebook(context)
        if not user or not notebook_id:
            return _fail("No notebook is open on this page.")
        try:
            entry, document = await notebook_documents.get(user["username"], notebook_id)
            status = await notebook_runtime_service.status(user, notebook_id)
            latest = await notebook_runtime_service.latest(user, notebook_id)
        except Exception:  # noqa: BLE001 - one safe message for every failure
            return _fail("The notebook could not be read.")
        by_cell = {item["cell_id"]: item for item in latest["executions"]}
        focus = str(invocation.arguments.get("cell_id") or "")
        cells = []
        for index, cell in enumerate(document.cells[:_MAX_CELLS]):
            run = by_cell.get(cell.id)
            cells.append(
                {
                    "position": index + 1,
                    "cell_id": cell.id,
                    "kind": cell.kind,
                    "name": cell.name,
                    "engine": cell.engine if cell.kind == "sql" else None,
                    "source": cell.source[:_MAX_SOURCE],
                    "last_run": None
                    if run is None
                    else {
                        "status": run["status"],
                        "out_of_date": run["source_revision"] != cell.source_revision,
                        "error": run.get("error_message"),
                        "outputs": _summarize_outputs(run.get("outputs")),
                    },
                }
            )
        runtime = status["runtime"]
        data = {
            "notebook": entry["name"],
            "focus_cell": focus or None,
            "cells": cells,
            "more_cells": max(0, len(document.cells) - _MAX_CELLS),
            "runtime": {
                "status": runtime.get("status"),
                "nova_data_role": runtime.get("active_role"),
                "nova_data_available": runtime.get("nova_attached"),
            },
            "variables": (latest.get("variables") or [])[:60],
            "how_it_works": (
                "SQL cells run in DuckDB (engine duckdb: local data, files under /workspace, "
                "governed Nova tables as nova.<database>.<table>, {{name}} references to "
                "earlier results) or in Nova (engine nova: plain Nova SQL, pushed down, "
                "read-only). A named SQL result is a DuckDB relation in Python; `db` is the "
                "DuckDB connection."
            ),
        }
        return ToolOutcome(
            ok=True,
            summary=f"Read {len(cells)} cells of {entry['name']}",
            data=data,
        )


async def _wait_for(notebook_id: str, execution_id: str, user: dict[str, Any]) -> dict | None:
    from app.modules.notebooks.runtime import state
    from app.modules.notebooks.runtime.service import notebook_runtime_service

    deadline = time.monotonic() + _RUN_WAIT_SECONDS
    while time.monotonic() < deadline:
        live = await state.get_execution(execution_id) or {}
        if live.get("status") not in {None, "queued", "running"}:
            break
        await asyncio.sleep(0.5)
    try:
        return await notebook_runtime_service.execution(user, notebook_id, execution_id)
    except Exception:  # noqa: BLE001
        return None


class NotebookRunCellTool:
    name = "notebook_run_cell"
    description = (
        "Run one cell of the open notebook, after the person approves, and read its "
        "result. The cell runs exactly as saved, in the notebook's runtime, as the "
        "person. Use it only when they asked you to run something or approved running "
        "a cell you proposed; the runtime starts first when it is not running."
    )
    parameters = {
        "type": "object",
        "properties": {"cell_id": {"type": "string", "maxLength": 64}},
        "required": ["cell_id"],
        "additionalProperties": False,
    }
    classification: ToolClassification = "session_change"
    requires_consent = True

    def preview(self, invocation: ToolInvocation) -> str:
        return "Run a cell of this notebook"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.notebooks.documents import notebook_documents
        from app.modules.notebooks.runtime.service import (
            NotebookRuntimeError,
            notebook_runtime_service,
        )

        user = _user(context)
        notebook_id = _open_notebook(context)
        if not user or not notebook_id:
            return _fail("No notebook is open on this page.")
        cell_id = str(invocation.arguments.get("cell_id") or "")
        try:
            _entry, document = await notebook_documents.get(user["username"], notebook_id)
            document.cell(cell_id)
        except Exception:  # noqa: BLE001
            return _fail("That cell is not in the open notebook.", recoverable=True)
        try:
            status = await notebook_runtime_service.status(user, notebook_id)
            if status["runtime"].get("status") in {"ABSENT", "STOPPED", "FAILED", "LOST"}:
                await notebook_runtime_service.start(user, notebook_id)
            response = await notebook_runtime_service.run(
                user,
                notebook_id,
                mode="cell",
                cell_id=cell_id,
                base_revision=None,
                on_error="stop",
                idempotency_key=f"neva-{invocation.tool_call_id}" if invocation.tool_call_id
                else None,
            )
        except NotebookRuntimeError as exc:
            return _fail(exc.message)
        if not response["executions"]:
            return _fail("That cell has nothing to run.")
        execution_id = response["executions"][0]["execution_id"]
        result = await _wait_for(notebook_id, execution_id, user)
        if result is None or result.get("status") in {"queued", "running"}:
            return ToolOutcome(
                ok=True,
                summary="The cell is still running; its result appears in the notebook",
                data={"status": "running"},
            )
        return ToolOutcome(
            ok=result["status"] == "succeeded",
            summary=f"The cell {result['status']}",
            data={
                "status": result["status"],
                "error": result.get("error_message"),
                "outputs": _summarize_outputs(result.get("outputs")),
            },
            error=None if result["status"] == "succeeded" else (
                result.get("error_message") or f"The cell {result['status']}"
            ),
            recoverable=result["status"] == "failed",
        )


class NotebookInstallPackageTool:
    name = "notebook_install_package"
    description = (
        "Add an approved Python package to the open notebook, after the person approves. "
        "Only packages on the deployment's approved list can be added; the notebook keeps "
        "it and installs it again whenever its runtime starts."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "maxLength": 100},
            "version": {"type": "string", "maxLength": 40},
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    classification: ToolClassification = "session_change"
    requires_consent = True

    def preview(self, invocation: ToolInvocation) -> str:
        name = str(invocation.arguments.get("name") or "a package")[:100]
        return f"Install {name} into this notebook's runtime"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.notebooks.runtime.service import (
            NotebookRuntimeError,
            notebook_runtime_service,
        )

        user = _user(context)
        notebook_id = _open_notebook(context)
        if not user or not notebook_id:
            return _fail("No notebook is open on this page.")
        try:
            result = await notebook_runtime_service.install(
                user,
                notebook_id,
                name=str(invocation.arguments.get("name") or ""),
                version=invocation.arguments.get("version") or None,
            )
        except NotebookRuntimeError as exc:
            return _fail(exc.message, recoverable=exc.code == "PACKAGE_NOT_ALLOWED")
        package = result["package"]
        return ToolOutcome(
            ok=True,
            summary=f"Installing {package['name']}; the notebook shows when it is ready",
            data={"package": package["name"], "status": "installing"},
        )


notebook_context_tool = NotebookContextTool()
notebook_run_cell_tool = NotebookRunCellTool()
notebook_install_package_tool = NotebookInstallPackageTool()

TOOLS = (notebook_context_tool, notebook_run_cell_tool, notebook_install_package_tool)
