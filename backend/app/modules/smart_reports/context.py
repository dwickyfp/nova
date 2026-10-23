"""Server-owned conversation bindings and freshly authorized report evidence."""

import json

from fastapi import HTTPException

from app.modules.smart_reports.repository import digest
from app.modules.smart_reports.runtime import report_runtime
from app.modules.smart_reports.schemas import Origin, ReportContextRef, ReportViewStateV1
from app.modules.smart_reports.service import report_service
from app.modules.smart_reports.sessions import VIEW_TTL, redis_client, scope


def binding_key(user: dict, thread_id: str) -> str:
    return f"nova:reports:binding:{scope(user)}:{digest(thread_id)}"


async def bind(user: dict, state: ReportViewStateV1) -> None:
    if state.origin:
        await report_service.origin(state.origin, user)
        await redis_client().set(
            binding_key(user, state.origin.thread_id),
            json.dumps({"report_id": state.report_id, "session_id": state.session_id}),
            ex=VIEW_TTL,
        )


async def bound(user: dict, origin: Origin) -> ReportViewStateV1 | None:
    raw = await redis_client().get(binding_key(user, origin.thread_id))
    if not raw:
        return None
    value = json.loads(raw)
    try:
        _spec, state = await report_service.view(value["report_id"], value["session_id"], user)
    except HTTPException as exc:
        if exc.status_code == 410:
            await redis_client().delete(binding_key(user, origin.thread_id))
            return None
        raise
    if not state.origin or state.origin.agent_id != origin.agent_id:
        raise HTTPException(403, "Report conversation binding changed")
    return state


def reference(state: ReportViewStateV1) -> ReportContextRef:
    return ReportContextRef(
        report_id=state.report_id,
        view_session_id=state.session_id,
        state_revision=state.state_revision,
        selected_widget_id=state.selected_widget_id,
    )


async def resolve(ref: ReportContextRef, user: dict, thread_id: str) -> dict:
    spec, state = await report_service.view(ref.report_id, ref.view_session_id, user)
    if not state.origin or state.origin.thread_id != thread_id:
        raise HTTPException(403, "Report context belongs to another conversation")
    if (
        state.state_revision != ref.state_revision
        or state.selected_widget_id != ref.selected_widget_id
    ):
        raise HTTPException(409, "Report selection changed; ask again using the current view")
    page_widgets = [
        w.id
        for p in spec.pages
        if p.id == state.active_page_id
        for s in p.sections
        for w in s.widgets
    ]
    selected = [state.selected_widget_id] if state.selected_widget_id else page_widgets[:8]
    result = await report_runtime.render(spec, state, user, selected)
    await report_service.verify_view(state, user)
    tables = []
    for widget in result["widgets"]:
        if widget["status"] in {"success", "empty"}:
            tables.append(
                {
                    "widget_id": widget["widget_id"],
                    "columns": widget["columns"],
                    "rows": widget["rows"][:100],
                    "truncated": widget["completeness"] != "complete" or len(widget["rows"]) > 100,
                    "query_id": widget["query_id"],
                    "query_fingerprint": widget["query_fingerprint"],
                    "semantic_version": widget["semantic_version"],
                }
            )
    await report_service.audit(ref.report_id, user, "context")
    return {
        "reference": ref.model_dump(),
        "title": spec.title,
        "filters": state.model_dump()["filters"],
        "selected_widget_id": state.selected_widget_id,
        "as_of": result["as_of"],
        "tables": tables,
        "widget_status": [{"id": w["widget_id"], "status": w["status"]} for w in result["widgets"]],
        "omitted_widgets": len(page_widgets) - len(selected) if not state.selected_widget_id else 0,
    }
