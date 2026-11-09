"""``/api/v1/studio/dataviz``: one governed path from a Studio result to Dataviz.

Routes validate HTTP input and map errors; the behavior lives in
:mod:`app.modules.superset.dataviz` and :mod:`app.modules.superset.publication`
(docs/arch-19-studio-dataviz.md). Every route runs as the signed-in caller.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user

from . import dataviz
from .client import BiError
from .dashboard_context import chart_source, dashboard_context
from .previews import Locator, PreviewError
from .publication import (
    PublishError,
    Target,
    editable_dashboards,
    get_operation,
    list_operations,
    publish,
)

router = APIRouter()
CurrentUser = Annotated[dict, Depends(get_current_user)]

_ID = r"^[A-Za-z0-9_.:\-]{1,128}$"


class NodeLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: Literal["Chart", "Table", "Kpi", "GeoMap"]
    evidence_id: str = Field(pattern=_ID)
    x: str | None = Field(default=None, max_length=256)
    y: str | None = Field(default=None, max_length=256)
    type: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=256)
    column: str | None = Field(default=None, max_length=256)
    longitude: str | None = Field(default=None, max_length=256)
    latitude: str | None = Field(default=None, max_length=256)
    geometry: str | None = Field(default=None, max_length=256)


class ResultLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(pattern=_ID)
    message_id: str = Field(pattern=_ID)
    content_id: str = Field(pattern=_ID)
    node: NodeLocator | None = None

    def locator(self) -> Locator:
        node = self.node.model_dump(exclude_none=True) if self.node else None
        return Locator(self.thread_id, self.message_id, self.content_id, node)


class CapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ResultLocator] = Field(default_factory=list, max_length=dataviz.MAX_BATCH)
    preview_refs: list[str] = Field(default_factory=list, max_length=dataviz.MAX_BATCH)


class TargetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["existing", "new"]
    dashboard_id: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, max_length=250)
    expected_revision: str | None = Field(default=None, max_length=80)

    def target(self) -> Target:
        return Target(
            self.mode, self.dashboard_id, (self.title or "").strip() or None, self.expected_revision
        )


class PublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_ref: str = Field(min_length=8, max_length=2048)
    idempotency_key: str = Field(min_length=8, max_length=128)
    target: TargetBody
    approved_transform_id: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=250)
    editor_state: Literal["clean", "dirty", "unknown"] = "unknown"
    open_dashboard_id: int | None = None
    agent_id: str | None = Field(default=None, max_length=64)


class ChangePreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_ref: str = Field(min_length=8, max_length=2048)
    target: TargetBody
    approved_transform_id: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=250)


class ChangeApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_token: str = Field(min_length=16, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=128)
    editor_state: Literal["clean", "dirty", "unknown"] = "unknown"
    open_dashboard_id: int | None = None


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, PublishError):
        detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
        if exc.capability:
            detail["capability"] = exc.capability
        if exc.operation:
            detail["operation"] = exc.operation
        return HTTPException(status_code=exc.status, detail=detail)
    if isinstance(exc, BiError):
        status = exc.status if exc.status in (401, 403, 404) else 503
        return HTTPException(
            status_code=status, detail={"code": "PROVIDER_ERROR", "message": exc.message}
        )
    if isinstance(exc, PreviewError):
        return HTTPException(
            status_code=404, detail={"code": "PREVIEW_NOT_FOUND", "message": str(exc)}
        )
    raise exc


@router.post("/capabilities/check", response_class=SanitizingJSONResponse)
async def check_capabilities(body: CapabilityRequest, user: CurrentUser) -> dict:
    """Capability for each result; ``can_add`` decides whether Add is shown."""
    results = await dataviz.check_many([item.locator() for item in body.items], user)
    for ref in body.preview_refs:
        results.append(await dataviz.check_ref(ref, user))
    return {"results": results}


@router.get("/dashboards", response_class=SanitizingJSONResponse)
async def list_dashboards(
    user: CurrentUser,
    search: str = Query(default="", max_length=80),
    page: int = Query(default=0, ge=0, le=1000),
) -> dict:
    """Dashboards the caller may add a chart to."""
    try:
        return await editable_dashboards(user, search=search, page=page)
    except (BiError, PublishError) as exc:
        raise _error(exc) from exc


@router.post("/publish", response_class=SanitizingJSONResponse)
async def publish_preview(body: PublishBody, user: CurrentUser) -> dict:
    """Add a verified result to an existing or new dashboard."""
    try:
        op = await publish(
            user,
            ref=body.preview_ref,
            idempotency_key=body.idempotency_key,
            target=body.target.target(),
            approved_transform_id=body.approved_transform_id,
            title=body.title,
            editor_state=body.editor_state,
            open_dashboard_id=body.open_dashboard_id,
            agent_id=body.agent_id,
        )
    except (BiError, PublishError, PreviewError, ValueError) as exc:
        if isinstance(exc, ValueError) and not isinstance(exc, PreviewError):
            raise HTTPException(
                status_code=422, detail={"code": "BAD_REQUEST", "message": str(exc)}
            ) from exc
        raise _error(exc) from exc
    if op.status != "SUCCEEDED":
        raise HTTPException(
            status_code=503 if op.status == "NEEDS_RECONCILIATION" else 422,
            detail={
                "code": op.status,
                "message": op.error or "Not published.",
                "operation": op.public(),
            },
        )
    return op.public()


@router.get("/operations", response_class=SanitizingJSONResponse)
async def operations(user: CurrentUser, unresolved: bool = False) -> dict:
    items = await list_operations(user["username"], unresolved=unresolved)
    return {"operations": [item.public() | {"created_at": str(item.created_at)} for item in items]}


@router.get("/operations/{operation_id}", response_class=SanitizingJSONResponse)
async def operation(operation_id: str, user: CurrentUser) -> dict:
    found = await get_operation(operation_id, user["username"])
    if found is None:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": "No such operation."}
        )
    return found.public()


@router.post("/changes/preview", response_class=SanitizingJSONResponse)
async def preview_change(body: ChangePreviewBody, user: CurrentUser) -> dict:
    """A human diff and an action token; nothing is changed."""
    try:
        return await dataviz.preview_change(
            user,
            ref=body.preview_ref,
            target=body.target.target(),
            approved_transform_id=body.approved_transform_id,
            title=body.title,
        )
    except (BiError, PublishError) as exc:
        raise _error(exc) from exc


@router.post("/changes/apply", response_class=SanitizingJSONResponse)
async def apply_change(body: ChangeApplyBody, user: CurrentUser) -> dict:
    """Apply exactly the previewed change, after the user approved it."""
    try:
        return await dataviz.apply_change(
            user,
            token=body.action_token,
            idempotency_key=body.idempotency_key,
            editor_state=body.editor_state,
            open_dashboard_id=body.open_dashboard_id,
        )
    except (BiError, PublishError) as exc:
        raise _error(exc) from exc


@router.get("/context", response_class=SanitizingJSONResponse)
async def context(
    user: CurrentUser,
    dashboard_id: int = Query(ge=1),
    native_filters_key: str | None = Query(default=None, max_length=128),
    edit_state: Literal["clean", "dirty", "unknown"] = "unknown",
) -> dict:
    """The verified context of the dashboard on screen."""
    try:
        return await dashboard_context(
            user, dashboard_id, native_filters_key=native_filters_key, edit_state=edit_state
        )
    except BiError as exc:
        raise _error(exc) from exc


@router.get("/charts/{chart_id}/source", response_class=SanitizingJSONResponse)
async def source(chart_id: int, user: CurrentUser) -> dict:
    try:
        return await chart_source(user, chart_id)
    except BiError as exc:
        raise _error(exc) from exc
