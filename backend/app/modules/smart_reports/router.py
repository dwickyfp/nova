"""Authenticated report operations; server sessions own effective query state."""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute
from pydantic import Field
from redis.exceptions import RedisError

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user
from app.modules.smart_reports.details import DetailRequest
from app.modules.smart_reports.distribution import SubscriptionRequest
from app.modules.smart_reports.repository import digest
from app.modules.smart_reports.runtime import report_runtime
from app.modules.smart_reports.schemas import (
    Contract,
    CreateReport,
    GrantRequest,
    Identifier,
    Origin,
    PatchRequest,
    RenderRequest,
    ReportContextRef,
    ReportInteractionEvent,
    ReportSpecV1,
    ReportViewStateV1,
    ViewCreate,
)
from app.modules.smart_reports.service import report_service, stable_id
from app.modules.smart_reports.sessions import new_session_id, view_sessions


class ReportRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except ValueError as exc:
                raise HTTPException(
                    422, "Invalid report configuration; check fields, layout and semantic mappings"
                ) from exc
            except RedisError as exc:
                raise HTTPException(503, "Report coordination is temporarily unavailable") from exc

        return handle


router = APIRouter(
    prefix="/studio/reports", default_response_class=SanitizingJSONResponse, route_class=ReportRoute
)
User = Annotated[dict, Depends(get_current_user)]


class Operation(Contract):
    operation_id: Identifier


class PublishRequest(Operation):
    expected_revision: int = Field(ge=1)
    expected_published_revision: int = Field(ge=0)


class RebaseRequest(PublishRequest):
    restore_revision: int | None = Field(default=None, ge=1)
    review_id: Identifier | None = None


class ArchiveRequest(Operation):
    expected_revision: int = Field(ge=1)
    archived: bool


class HandoffRequest(Contract):
    view_session_id: Identifier
    expected_state_revision: int = Field(ge=1)


class ConsumeRequest(Contract):
    handoff_id: str = Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class PlanRequest(Contract):
    prompt: str = Field(min_length=3, max_length=4000)


class ExportRequest(Operation):
    view_session_id: Identifier
    expected_state_revision: int = Field(ge=1)
    format: Literal["pdf", "xlsx", "pptx"]


@router.post("/{report_id}/exports")
async def create_export(report_id: str, body: ExportRequest, user: User):
    from app.modules.smart_reports.exports import create_export as create

    return await create(
        report_id,
        body.view_session_id,
        body.expected_state_revision,
        body.format,
        body.operation_id,
        user,
    )


@router.get("/{report_id}/exports/{export_id}/download")
async def download_export(report_id: str, export_id: str, user: User):
    from app.modules.agents.exporters import MEDIA_TYPES
    from app.modules.smart_reports.exports import download

    payload, fmt = await download(report_id, export_id, user)
    return Response(
        payload,
        media_type=MEDIA_TYPES[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="report.{fmt}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/{report_id}/subscriptions")
async def configure_subscription(report_id: str, body: SubscriptionRequest, user: User):
    from app.modules.smart_reports.distribution import configure

    return await configure(report_id, body, user)


@router.get("/{report_id}/subscriptions")
async def subscriptions(report_id: str, user: User):
    from app.modules.smart_reports.distribution import list_private

    return await list_private(report_id, "subscription", user)


@router.get("/{report_id}/activity")
async def activity(report_id: str, user: User):
    from app.modules.smart_reports.distribution import list_private

    return await list_private(report_id, "activity", user)


@router.get("/{report_id}/activity/{activity_id}/snapshot")
async def read_activity_snapshot(report_id: str, activity_id: str, user: User):
    from app.modules.smart_reports.distribution import activity_snapshot

    return await activity_snapshot(report_id, activity_id, user)


@router.post("/{report_id}/widgets/{widget_id}/detail")
async def report_detail(report_id: str, widget_id: str, body: DetailRequest, user: User):
    from app.modules.smart_reports.details import read_detail

    return await read_detail(report_id, widget_id, body, user)


@router.get("/capabilities")
async def report_capabilities(user: User):
    from app.modules.smart_reports.capabilities import capabilities

    return capabilities()


@router.get("/sources")
async def sources(user: User):
    from app.modules.smart_reports.authoring import source_catalog

    return {"sources": await source_catalog(user)}


@router.post("/plan")
async def plan_report(body: PlanRequest, user: User):
    from app.modules.smart_reports.authoring import propose

    return await propose(body.prompt, user)


@router.post("/{report_id}/drafts/{draft_id}/proposals")
async def propose_edit(report_id: str, draft_id: str, body: PlanRequest, user: User):
    from app.modules.smart_reports.authoring import propose

    spec, revision = await report_service.spec(report_id, user, draft_id=draft_id)
    return await propose(body.prompt, user, spec=spec, revision=revision)


@router.get("")
async def list_reports(user: User, after: str = Query(default="", max_length=64)):
    return await report_service.list(user, after)


@router.post("", status_code=201)
async def create_report(body: CreateReport, user: User):
    return await report_service.create(body, user)


@router.post("/handoffs/consume")
async def consume_handoff(body: ConsumeRequest, user: User):
    state = await view_sessions.consume(user, body.handoff_id)
    await report_service.spec(
        state.report_id, user, draft_id=state.draft_id, revision=state.spec_revision
    )
    await report_service.origin(state.origin, user)
    return state


@router.get("/templates")
async def templates(user: User, after: str = Query(default="", max_length=64)):
    from app.modules.smart_reports.templates import list_templates

    return await list_templates(user, after)


@router.post("/templates/{template_id}/instantiate")
async def use_template(template_id: str, body: Operation, user: User):
    from app.modules.smart_reports.templates import instantiate

    return await instantiate(template_id, body.operation_id, user)


class TemplateRequest(Operation):
    view_session_id: Identifier
    expected_state_revision: int = Field(ge=1)


@router.post("/{report_id}/templates")
async def save_template(report_id: str, body: TemplateRequest, user: User):
    from app.modules.smart_reports.templates import save

    return await save(
        report_id, body.view_session_id, body.expected_state_revision, body.operation_id, user
    )


@router.get("/{report_id}")
async def get_report(report_id: str, user: User):
    report = await report_service.access(report_id, user)
    draft_id = stable_id(report_id, "draft", user["username"])
    draft = await report_service.repo.read(draft_id)
    return {**report, "draft_id": draft_id if draft else None}


@router.get("/{report_id}/revisions/{revision}")
async def get_revision(report_id: str, revision: int, user: User):
    spec, current = await report_service.spec(report_id, user, revision=revision)
    return {"spec": spec, "revision": current}


@router.get("/{report_id}/revisions")
async def history(report_id: str, user: User, before: int = Query(default=2**63 - 1, ge=1)):
    report = await report_service.access(report_id, user)
    records = await report_service.repo.history(stable_id(report_id, "published"), before)
    return {
        "revisions": [
            {
                "revision": r["revision"],
                "actor": r["payload"]["actor"],
                "published_at": r["payload"]["published_at"],
            }
            for r in records
            if r["revision"] <= report["published_revision"]
        ]
    }


@router.post("/{report_id}/drafts")
async def create_draft(report_id: str, body: Operation, user: User):
    return await report_service.draft(report_id, user, body.operation_id)


@router.get("/{report_id}/drafts/{draft_id}")
async def get_draft(
    report_id: str, draft_id: str, user: User, revision: int | None = Query(default=None, ge=1)
):
    spec, current = await report_service.spec(report_id, user, draft_id=draft_id, revision=revision)
    record = await report_service.repo.read(draft_id, current)
    assert record is not None
    return {
        "spec": spec,
        "revision": current,
        "can_undo": bool(record["payload"].get("undo", [current - 1] if current > 1 else [])),
        "can_redo": bool(record["payload"].get("redo", [])),
    }


@router.get("/{report_id}/drafts/{draft_id}/history")
async def draft_history(report_id: str, draft_id: str, user: User):
    await report_service.spec(report_id, user, draft_id=draft_id)
    return {"revisions": await report_service.repo.history(draft_id)}


@router.post("/{report_id}/drafts/{draft_id}/validate-patch")
async def preview_patch(report_id: str, draft_id: str, body: PatchRequest, user: User):
    return await report_service.patch(report_id, draft_id, body, user, preview=True)


class PreviewRenderRequest(Contract):
    patch: PatchRequest
    page_id: Identifier | None = None


@router.post("/{report_id}/drafts/{draft_id}/preview-render")
async def preview_render(
    report_id: str, draft_id: str, body: PreviewRenderRequest, user: User, request: Request
):
    preview = await report_service.patch(report_id, draft_id, body.patch, user, preview=True)
    spec = ReportSpecV1.model_validate(preview["after"])
    page_id = body.page_id or spec.pages[0].id
    if page_id not in {page.id for page in spec.pages}:
        raise HTTPException(422, "Preview page not found")
    state = ReportViewStateV1(
        session_id=new_session_id(),
        report_id=report_id,
        draft_id=draft_id,
        spec_revision=body.patch.expected_revision,
        active_page_id=page_id,
        filters={item.id: item.default for item in spec.filters},
    )
    result = await render_connected(spec, state, user, [], request)
    current, revision = await report_service.spec(report_id, user, draft_id=draft_id)
    if revision != body.patch.expected_revision or digest(
        current.model_dump(mode="json")
    ) != digest(preview["before"]):
        raise HTTPException(409, "Draft changed; preview the current revision")
    await report_service.audit(report_id, user, "preview")
    return {
        "spec": spec,
        "state": state,
        "result": result,
        "definition_fingerprint": preview["preview_hash"],
    }


@router.patch("/{report_id}/drafts/{draft_id}")
async def patch_draft(report_id: str, draft_id: str, body: PatchRequest, user: User):
    return await report_service.patch(report_id, draft_id, body, user)


@router.post("/{report_id}/drafts/{draft_id}/publish")
async def publish(report_id: str, draft_id: str, body: PublishRequest, user: User):
    return await report_service.publish(report_id, draft_id, user, **body.model_dump())


@router.post("/{report_id}/drafts/{draft_id}/rebase")
async def rebase(report_id: str, draft_id: str, body: RebaseRequest, user: User):
    return await report_service.rebase(report_id, draft_id, user, **body.model_dump())


@router.post("/{report_id}/archive")
async def archive(report_id: str, body: ArchiveRequest, user: User):
    return await report_service.archive(report_id, user, **body.model_dump())


@router.get("/{report_id}/grants")
async def list_grants(report_id: str, user: User):
    await report_service.access(report_id, user, "share")
    ids = await report_service.repo.ids("grant", report_id=report_id)
    return {
        "grants": [
            {"grant_id": identity, **(await report_service.repo.read(identity) or {})}
            for identity in ids
        ]
    }


@router.post("/{report_id}/grants")
async def grant(report_id: str, body: GrantRequest, user: User):
    return await report_service.grant(report_id, body, user)


@router.post("/{report_id}/view-sessions")
async def create_view(report_id: str, body: ViewCreate, user: User):
    return await report_service.create_view(report_id, body, user)


@router.get("/{report_id}/view-sessions/{session_id}")
async def get_view(report_id: str, session_id: str, user: User):
    _spec, state = await report_service.view(report_id, session_id, user)
    return state


class AttachOrigin(Contract):
    origin: Origin
    expected_state_revision: int = Field(ge=1)


@router.post("/{report_id}/view-sessions/{session_id}/conversation")
async def attach_conversation(report_id: str, session_id: str, body: AttachOrigin, user: User):
    from app.modules.smart_reports.context import bind

    _spec, state = await report_service.view(report_id, session_id, user)
    if state.state_revision != body.expected_state_revision:
        raise HTTPException(409, "Report state changed; attach the current view")
    await report_service.origin(body.origin, user)
    updated = await view_sessions.attach_origin(user, state, body.origin)
    await bind(user, updated)
    return updated


@router.post("/{report_id}/view-sessions/{session_id}/events")
async def apply_event(report_id: str, session_id: str, body: ReportInteractionEvent, user: User):
    from app.modules.smart_reports.context import bind

    spec, _state = await report_service.view(report_id, session_id, user)
    state = await view_sessions.event(user, report_id, session_id, spec, body)
    await bind(user, state)
    return {
        "state": state,
        "state_fingerprint": digest(state.model_dump()),
        "affected_widget_ids": list(spec.widgets()),
    }


@router.post("/{report_id}/render")
async def render(report_id: str, body: RenderRequest, user: User, request: Request):
    spec, state = await report_service.view(report_id, body.view_session_id, user)
    if state.state_revision != body.expected_state_revision:
        raise HTTPException(409, "Report state changed; reload before rendering")
    result = await render_connected(spec, state, user, body.widget_ids, request)
    await report_service.verify_view(state, user)
    await report_service.audit(report_id, user, "render")
    return {**result, "request_generation": body.request_generation}


async def render_connected(
    spec: ReportSpecV1,
    state: ReportViewStateV1,
    user: dict,
    widget_ids: list[str],
    request: Request,
) -> dict:
    execution = asyncio.create_task(report_runtime.render(spec, state, user, widget_ids))
    try:
        while not execution.done():
            await asyncio.wait({execution}, timeout=0.25)
            if await request.is_disconnected():
                raise HTTPException(499, "Report request cancelled")
        result = await execution
    finally:
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
    return result


@router.post("/{report_id}/handoffs")
async def create_handoff(report_id: str, body: HandoffRequest, user: User):
    _spec, state = await report_service.view(report_id, body.view_session_id, user)
    await report_service.origin(state.origin, user)
    if state.state_revision != body.expected_state_revision:
        raise HTTPException(409, "Report state changed; retry opening the report")
    return await view_sessions.handoff(user, state)


class SaveViewRequest(Operation):
    view_session_id: Identifier
    name: str = Field(min_length=1, max_length=128)


@router.get("/{report_id}/filter-options/{filter_id}")
async def options(
    report_id: str,
    filter_id: str,
    user: User,
    view_session_id: str = Query(max_length=64),
    search: str = Query(default="", max_length=128),
    offset: int = Query(default=0, ge=0, le=100000),
):
    from app.modules.smart_reports.exploration import filter_options

    return await filter_options(report_id, view_session_id, filter_id, user, search, offset)


@router.get("/{report_id}/saved-views")
async def list_saved_views(report_id: str, user: User):
    from app.modules.smart_reports.exploration import saved_views

    return await saved_views(report_id, user)


@router.post("/{report_id}/saved-views")
async def save_personal_view(report_id: str, body: SaveViewRequest, user: User):
    from app.modules.smart_reports.exploration import save_view

    return await save_view(report_id, body.view_session_id, body.name, body.operation_id, user)


@router.post("/{report_id}/context")
async def attach_context(report_id: str, body: ReportContextRef, user: User):
    from app.modules.smart_reports.context import bind, resolve

    if body.report_id != report_id:
        raise HTTPException(422, "Report reference mismatch")
    _spec, state = await report_service.view(report_id, body.view_session_id, user)
    if not state.origin:
        raise HTTPException(422, "Open the report from a conversation to ask Nova")
    result = await resolve(body, user, state.origin.thread_id)
    await bind(user, state)
    return {"reference": result["reference"], "origin": state.origin}


class AddArtifactRequest(Operation):
    expected_revision: int = Field(ge=1)
    artifact_id: Identifier
    section_id: Identifier


@router.post("/{report_id}/drafts/{draft_id}/artifacts")
async def add_artifact(report_id: str, draft_id: str, body: AddArtifactRequest, user: User):
    from app.modules.smart_reports.artifacts import add_to_report

    return await add_to_report(
        report_id,
        draft_id,
        body.artifact_id,
        body.section_id,
        body.operation_id,
        body.expected_revision,
        user,
    )


@router.post("/{report_id}/insights")
async def inspect_report(report_id: str, body: ReportContextRef, user: User):
    from app.modules.smart_reports.insights import inspect_report as inspect

    if body.report_id != report_id:
        raise HTTPException(422, "Report reference mismatch")
    return await inspect(body, user)


class ApplySavedViewRequest(Operation):
    preset_id: Identifier
    expected_state_revision: int = Field(ge=1)


@router.post("/{report_id}/view-sessions/{session_id}/saved-view")
async def apply_saved_view(
    report_id: str, session_id: str, body: ApplySavedViewRequest, user: User
):
    from app.modules.smart_reports.exploration import apply_saved_view as apply

    return await apply(report_id, session_id, user=user, **body.model_dump())


class SubmitReviewRequest(Operation):
    expected_revision: int = Field(ge=1)


@router.post("/{report_id}/drafts/{draft_id}/reviews")
async def submit_review(report_id: str, draft_id: str, body: SubmitReviewRequest, user: User):
    return await report_service.submit_review(report_id, draft_id, user, **body.model_dump())


@router.get("/{report_id}/reviews")
async def list_reviews(report_id: str, user: User, after: str = Query(default="", max_length=64)):
    await report_service.access(report_id, user, "publish")
    identities = await report_service.repo.ids("review", report_id=report_id, after=after)
    reviews = []
    for identity in identities:
        submitted = await report_service.review(report_id, identity, user)
        reviews.append({key: value for key, value in submitted.items() if key != "spec"})
    return {"reviews": reviews, "next_cursor": identities[-1] if len(identities) == 100 else None}


@router.get("/{report_id}/reviews/{review_id}")
async def get_review(report_id: str, review_id: str, user: User):
    return await report_service.review(report_id, review_id, user)
