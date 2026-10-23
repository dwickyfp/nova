"""Security-bound Redis exploration state and single-use navigation handoffs."""

from __future__ import annotations

import json
import secrets
from uuid import uuid4

from fastapi import HTTPException

from app.core.redis import session_store
from app.modules.assistant.security import session_security
from app.modules.smart_reports.repository import digest
from app.modules.smart_reports.schemas import (
    Origin,
    Predicate,
    ReportInteractionEvent,
    ReportSpecV1,
    ReportViewStateV1,
)

VIEW_TTL = 3600
HANDOFF_TTL = 600


def scope(user: dict) -> str:
    security = session_security(user)
    return digest([security.cache_partition(), security.session_id])


def redis_client():
    if session_store._redis is None:
        raise HTTPException(503, "Report sessions are unavailable")
    return session_store._redis


def reduce_event(
    spec: ReportSpecV1, state: ReportViewStateV1, event: ReportInteractionEvent
) -> ReportViewStateV1:
    if (
        state.spec_revision != event.spec_revision
        or state.state_revision != event.expected_state_revision
    ):
        raise HTTPException(409, "Report state changed; reload before applying this action")
    result = state.model_copy(deep=True)
    filters = {f.id: f for f in spec.filters}
    widgets = spec.widgets()
    target = event.target or ""
    if event.type in {"FILTER_SET", "FILTER_RESET"}:
        if target not in filters:
            raise ValueError("Filter does not exist")
        definition = filters[target]
        if event.type == "FILTER_SET":
            if event.value is None:
                raise ValueError("Filter value is required")
            definition.validate_value(event.value)
            if definition.required and event.value.kind == "unset":
                raise ValueError("A required filter cannot be cleared")
            result.filters[target] = event.value
        else:
            result.filters[target] = definition.default
        pending = [target]
        while pending:
            parent = pending.pop()
            for child in spec.filters:
                if parent in child.parents:
                    result.filters[child.id] = child.default
                    pending.append(child.id)
        result.page_index = {}
    elif event.type == "FILTER_RESET_ALL":
        result.filters = {f.id: f.default for f in spec.filters}
        result.cross_filters = {}
        result.drill_paths = {}
        result.page_index = {}
    elif event.type == "PAGE_SET":
        if target not in {p.id for p in spec.pages}:
            raise ValueError("Page does not exist")
        result.active_page_id = target
    elif event.type == "WIDGET_FOCUS":
        if event.target is not None and target not in widgets:
            raise ValueError("Widget does not exist")
        result.selected_widget_id = event.target
    else:
        if target not in widgets:
            raise ValueError("Widget does not exist")
        widget = widgets[target]
        if not widget.query:
            raise ValueError("Map this widget before exploring it")
        if event.type == "CROSS_FILTER_APPLY":
            rules = [i for i in spec.interactions if i.source_widget == target]
            if not rules:
                raise ValueError("Cross-filter is not configured for this widget")
            dimensions = result.group_by.get(target, widget.query.dimensions)
            if any(rule.field not in dimensions for rule in rules):
                raise ValueError("Cross-filter is unavailable at this drill level")
            result.cross_filters[target] = event.values
            result.page_index = {}
        elif event.type == "CROSS_FILTER_CLEAR":
            result.cross_filters.pop(target, None)
            result.page_index = {}
        elif event.type in {"DRILL_DOWN", "DRILL_BY"}:
            if event.field not in widget.drill_dimensions:
                raise ValueError("Drill dimension is not allowed")
            if event.type == "DRILL_DOWN":
                dimensions = result.group_by.get(target, widget.query.dimensions)
                if not dimensions or not event.values:
                    raise ValueError("Choose a grouped mark before drilling")
                result.drill_paths.setdefault(target, []).append(
                    Predicate(field=dimensions[-1], operator="IN", value=event.values)
                )
            result.group_by[target] = [event.field]
            result.page_index[target] = 0
        elif event.type == "DRILL_UP":
            path = result.drill_paths.get(target, [])
            if path:
                result.group_by[target] = [path.pop().field]
            else:
                result.group_by.pop(target, None)
            result.page_index[target] = 0
        elif event.type == "TABLE_SORT":
            result.sort[target] = event.sort
            result.page_index[target] = 0
        elif event.type == "TABLE_PAGE":
            result.page_index[target] = event.page_index
    result.state_revision += 1
    return ReportViewStateV1.model_validate(result.model_dump())


class ViewSessions:
    def key(self, user: dict, session_id: str) -> str:
        return f"nova:reports:view:{scope(user)}:{session_id}"

    async def create(self, user: dict, state: ReportViewStateV1) -> ReportViewStateV1:
        await redis_client().set(
            self.key(user, state.session_id), state.model_dump_json(), ex=VIEW_TTL, nx=True
        )
        return state

    async def get(self, user: dict, report_id: str, session_id: str) -> ReportViewStateV1:
        raw = await redis_client().get(self.key(user, session_id))
        if not raw:
            raise HTTPException(410, "Report view expired; reopen the report")
        state = ReportViewStateV1.model_validate_json(raw)
        if state.report_id != report_id:
            raise HTTPException(404, "Report view not found")
        return state

    async def event(
        self,
        user: dict,
        report_id: str,
        session_id: str,
        spec: ReportSpecV1,
        event: ReportInteractionEvent,
    ) -> ReportViewStateV1:
        state = await self.get(user, report_id, session_id)
        key = self.key(user, session_id)
        event_key = f"{key}:event:{event.event_id}"
        fingerprint = digest(event.model_dump())
        cached = await redis_client().get(event_key)
        if cached:
            if cached != fingerprint:
                raise HTTPException(409, "Event ID already used")
            return state
        result = reduce_event(spec, state, event)
        return await self.commit(user, state, result, event.event_id, fingerprint)

    async def commit(
        self,
        user: dict,
        state: ReportViewStateV1,
        result: ReportViewStateV1,
        operation_id: str,
        fingerprint: str,
    ) -> ReportViewStateV1:
        key = self.key(user, state.session_id)
        event_key = f"{key}:event:{operation_id}"
        status = await redis_client().eval(
            "local old=redis.call('GET',KEYS[1]); if not old then return -1 end; "
            "local seen=redis.call('GET',KEYS[2]); if seen then if "
            "seen==ARGV[3] then return 2 else return -2 end end; "
            "if cjson.decode(old).state_revision~=tonumber(ARGV[1]) then return 0 end; "
            "redis.call('SET',KEYS[1],ARGV[2],'EX',ARGV[4]); "
            "redis.call('SET',KEYS[2],ARGV[3],'EX',ARGV[4]); return 1",
            2,
            key,
            event_key,
            state.state_revision,
            result.model_dump_json(),
            fingerprint,
            VIEW_TTL,
        )
        if status == 2:
            return await self.get(user, state.report_id, state.session_id)
        if status != 1:
            raise HTTPException(409, "Report state changed; reload before retrying")
        return result

    async def handoff(self, user: dict, state: ReportViewStateV1) -> dict:
        token = secrets.token_urlsafe(32)
        await redis_client().set(
            f"nova:reports:handoff:{digest(token)}",
            json.dumps(
                {"scope": scope(user), "report_id": state.report_id, "session_id": state.session_id}
            ),
            ex=HANDOFF_TTL,
        )
        return {
            "handoff_id": token,
            "target_path": f"/studio/reports/{state.report_id}?handoff={token}",
        }

    async def attach_origin(
        self, user: dict, state: ReportViewStateV1, origin: Origin
    ) -> ReportViewStateV1:
        if state.origin:
            if (
                state.origin.agent_id != origin.agent_id
                or state.origin.thread_id != origin.thread_id
            ):
                raise HTTPException(409, "This view already belongs to another conversation")
            return state
        updated = state.model_copy(
            update={"origin": origin, "state_revision": state.state_revision + 1}
        )
        changed = await redis_client().eval(
            "local old=redis.call('GET',KEYS[1]); if not old then return 0 end; "
            "if cjson.decode(old).state_revision~=tonumber(ARGV[1]) then return 0 end; "
            "redis.call('SET',KEYS[1],ARGV[2],'EX',ARGV[3]); return 1",
            1,
            self.key(user, state.session_id),
            state.state_revision,
            updated.model_dump_json(),
            VIEW_TTL,
        )
        if changed != 1:
            raise HTTPException(409, "Report state changed; attach the current view")
        return updated

    async def consume(self, user: dict, token: str) -> ReportViewStateV1:
        raw = await redis_client().eval(
            "local v=redis.call('GET',KEYS[1]); if not v then return '' end; "
            "if cjson.decode(v).scope~=ARGV[1] then return '' end; "
            "redis.call('DEL',KEYS[1]); return v",
            1,
            f"nova:reports:handoff:{digest(token)}",
            scope(user),
        )
        if not raw:
            raise HTTPException(410, "Report handoff expired or unavailable")
        value = json.loads(raw)
        return await self.get(user, value["report_id"], value["session_id"])


view_sessions = ViewSessions()


def new_session_id() -> str:
    return str(uuid4())
