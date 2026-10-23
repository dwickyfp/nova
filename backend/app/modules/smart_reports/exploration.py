"""Governed filter enumeration and personally saved exploration state."""

from datetime import UTC, datetime

from fastapi import HTTPException

from app.modules.agents.semantic.planning import SemanticFilter, SemanticOrder, SemanticPlan
from app.modules.intelligence.engine_repository import metadata_lock
from app.modules.intelligence.semantic_views import semantic_view_service
from app.modules.smart_reports.runtime import scalar_json, value_filters
from app.modules.smart_reports.schemas import ReportViewStateV1
from app.modules.smart_reports.security import require_current_session
from app.modules.smart_reports.service import now, report_service, stable_id


async def filter_options(
    report_id: str, session_id: str, filter_id: str, user: dict, search: str, offset: int
) -> dict:
    spec, state = await report_service.view(report_id, session_id, user)
    selected = next((f for f in spec.filters if f.id == filter_id), None)
    if selected is None:
        raise HTTPException(404, "Filter not found")
    source = next(s for s in spec.sources if s.id == selected.source)
    filters = []
    instant = datetime.now(UTC)
    for definition in spec.filters:
        if definition.id == filter_id:
            continue
        if definition.scope != "report" and definition.id not in selected.parents:
            continue
        field = (
            definition.field
            if definition.source == source.id
            else definition.mappings.get(source.id)
        )
        if not field:
            if definition.required:
                raise HTTPException(422, "Required filter has no mapping to this source")
            continue
        value = state.filters.get(definition.id, definition.default)
        if definition.required and value.kind == "unset":
            raise HTTPException(422, "Select the required parent filter first")
        filters.extend(
            value_filters(
                field,
                value,
                kind=definition.kind,
                now=instant,
                timezone=spec.timezone,
                **spec.calendar.model_dump(),
            )
        )
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(SemanticFilter(selected.field, "LIKE", f"%{escaped}%"))
    if not selected.allow_null:
        filters.append(SemanticFilter(selected.field, "IS NOT NULL", None))
    _view, version = await semantic_view_service._readable_version(
        source.view_id, source.version, user
    )
    if version["fingerprint"] != source.fingerprint:
        raise HTTPException(409, "Filter source has changed")
    result = await semantic_view_service.execute_plan(
        source.view_id,
        source.version,
        SemanticPlan(
            metrics=(),
            dimensions=(selected.field,),
            distinct=True,
            filters=tuple(filters),
            order_by=(SemanticOrder(selected.field, "asc"),),
            limit=101,
            offset=offset,
        ),
        user,
    )
    await require_current_session(user)
    await report_service.verify_view(state, user)
    return {
        "values": scalar_json([row[0] for row in result["rows"][:100]]),
        "next_offset": offset + 100 if len(result["rows"]) > 100 else None,
        "state_revision": state.state_revision,
    }


async def save_view(
    report_id: str, session_id: str, name: str, operation_id: str, user: dict
) -> dict:
    _spec, state = await report_service.view(report_id, session_id, user)
    identity = stable_id(report_id, "preset", user["username"], operation_id)
    request = {"state": state.model_dump(mode="json"), "name": name}
    async with metadata_lock(f"smart-report:{report_id}") as lease:
        record = await report_service.repo.append(
            record_id=identity,
            report_id=report_id,
            kind="preset",
            owner=user["username"],
            operation_id=operation_id,
            expected_revision=0,
            request=request,
            lease=lease,
            payload={
                "report_id": report_id,
                "owner": user["username"],
                "name": name,
                "active_role": user["active_role"],
                "state": request["state"],
                "created_at": now(),
            },
        )
    return {"preset_id": identity, "name": record["payload"]["name"]}


async def saved_views(report_id: str, user: dict) -> dict:
    await report_service.access(report_id, user)
    views = []
    for identity in await report_service.repo.ids("preset", report_id=report_id):
        record = await report_service.repo.read(identity)
        if (
            record
            and record["payload"]["owner"] == user["username"]
            and record["payload"]["active_role"] == user["active_role"]
        ):
            state = ReportViewStateV1.model_validate(record["payload"]["state"])
            views.append(
                {
                    "preset_id": identity,
                    "name": record["payload"]["name"],
                    "draft_id": state.draft_id,
                    "spec_revision": state.spec_revision,
                }
            )
    return {"views": views}


async def apply_saved_view(
    report_id: str,
    session_id: str,
    preset_id: str,
    operation_id: str,
    expected_state_revision: int,
    user: dict,
) -> ReportViewStateV1:
    from app.modules.smart_reports.context import bind
    from app.modules.smart_reports.repository import digest
    from app.modules.smart_reports.sessions import redis_client, view_sessions

    _spec, state = await report_service.view(report_id, session_id, user)
    request_hash = digest(["preset", preset_id, expected_state_revision])
    previous = await redis_client().get(
        f"{view_sessions.key(user, session_id)}:event:{operation_id}"
    )
    if previous:
        if previous != request_hash:
            raise HTTPException(409, "Operation ID already used")
        return state
    if state.state_revision != expected_state_revision:
        raise HTTPException(409, "Report state changed; reload before applying the saved view")
    record = await report_service.repo.read(preset_id)
    if (
        not record
        or record["payload"].get("owner") != user["username"]
        or record["payload"].get("active_role") != user["active_role"]
        or record["payload"].get("report_id") != report_id
    ):
        raise HTTPException(404, "Saved view not found")
    saved = ReportViewStateV1.model_validate(record["payload"]["state"])
    if saved.spec_revision != state.spec_revision or saved.draft_id != state.draft_id:
        raise HTTPException(409, "Saved view belongs to a different report revision")
    replacement = saved.model_copy(
        update={
            "session_id": state.session_id,
            "origin": state.origin,
            "state_revision": state.state_revision + 1,
        }
    )
    result = await view_sessions.commit(user, state, replacement, operation_id, request_hash)
    await bind(user, result)
    return result
