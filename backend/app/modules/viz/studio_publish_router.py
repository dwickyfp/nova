"""``/api/v1/agents/studio/dataviz``: add a Studio result to a native dashboard.

Routes validate HTTP input and map refusals; the behavior lives in
:mod:`app.modules.viz.studio_publish`. Every route runs as the signed-in caller.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user

from . import studio_artifacts, studio_drafts, studio_publish
from .contracts.queries import DatasetQueryRequest, DatasetQueryResult
from .studio_drafts import DraftError
from .studio_publish import PublishError, Target
from .studio_results import Locator

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


class PageChartLocator(BaseModel):
    """A native chart on an answer page, by its position among the page's charts."""

    model_config = ConfigDict(extra="forbid")

    component: Literal["DatavizChart"]
    position: int = Field(ge=0, le=255, strict=True)


class ResultLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(pattern=_ID)
    message_id: str = Field(pattern=_ID)
    content_id: str = Field(pattern=_ID)
    node: Annotated[PageChartLocator | NodeLocator, Field(union_mode="left_to_right")] | None = None

    def locator(self) -> Locator:
        node = self.node.model_dump(exclude_none=True) if self.node else None
        return Locator(self.thread_id, self.message_id, self.content_id, node)


class CapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ResultLocator] = Field(default_factory=list, max_length=studio_publish.MAX_BATCH)
    preview_refs: list[str] = Field(
        default_factory=list, max_length=studio_publish.MAX_BATCH
    )


class TargetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["existing", "new"]
    dashboard_id: UUID | None = None
    title: str | None = Field(default=None, max_length=250)

    @model_validator(mode="after")
    def complete(self) -> TargetBody:
        if self.mode == "existing" and self.dashboard_id is None:
            raise ValueError("choose the dashboard to add the chart to")
        return self

    def target(self) -> Target:
        return Target(self.mode, self.dashboard_id, (self.title or "").strip() or None)


_REF = r"^[A-Za-z0-9_.\-]+$"


class PublishBody(BaseModel):
    """Exactly one of ``preview_ref`` (a result) or ``draft_ref`` (a chart draft)."""

    model_config = ConfigDict(extra="ignore")

    preview_ref: str | None = Field(default=None, min_length=8, max_length=2048)
    draft_ref: str | None = Field(default=None, min_length=8, max_length=1024, pattern=_REF)
    idempotency_key: UUID
    target: TargetBody
    title: str | None = Field(default=None, max_length=250)

    @model_validator(mode="after")
    def one_source(self) -> PublishBody:
        if (self.preview_ref is None) == (self.draft_ref is None):
            raise ValueError("send either preview_ref or draft_ref")
        return self


class RevisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart: dict[str, Any]
    base_revision: int = Field(ge=1, le=10_000)


def _error(exc: PublishError) -> HTTPException:
    return HTTPException(status_code=exc.status,
                         detail={"code": exc.code, "message": exc.message})


@router.post("/capabilities/check", response_class=SanitizingJSONResponse)
async def check_capabilities(body: CapabilityRequest, user: CurrentUser) -> dict:
    """Whether each result can be added; ``can_add`` decides whether Add is shown."""
    results = await studio_publish.check([item.locator() for item in body.items], user)
    for ref in body.preview_refs[: studio_publish.MAX_BATCH - len(results)]:
        results.append(await studio_publish.check_ref(ref, user))
    return {"results": results}


@router.get("/dashboards", response_class=SanitizingJSONResponse)
async def list_dashboards(
    user: CurrentUser,
    search: str = Query(default="", max_length=80),
    page: int = Query(default=0, ge=0, le=1000),
) -> dict:
    """Dashboards the caller may add a chart to."""
    return await studio_publish.editable_dashboards(user, search=search, page=page)


@router.post("/publish", response_class=SanitizingJSONResponse)
async def publish_preview(body: PublishBody, user: CurrentUser) -> dict[str, Any]:
    """Add a verified result, or a chart draft as it is, to an existing or a new dashboard.

    Nothing is saved without this explicit request; the agent cannot call it.
    """
    try:
        if body.draft_ref is not None:
            return await studio_artifacts.publish_draft(
                user, ref=body.draft_ref, idempotency_key=str(body.idempotency_key),
                target=body.target.target(), title=body.title,
            )
        assert body.preview_ref is not None
        return await studio_publish.publish(
            user,
            ref=body.preview_ref,
            idempotency_key=str(body.idempotency_key),
            target=body.target.target(),
            title=body.title,
        )
    except PublishError as exc:
        raise _error(exc) from exc


@router.post("/drafts/{ref}/query", response_model=DatasetQueryResult)
async def query_draft(
    body: DatasetQueryRequest, user: CurrentUser,
    ref: str = Path(min_length=8, max_length=1024, pattern=r"^[A-Za-z0-9_.\-]+$"),
) -> DatasetQueryResult:
    """Read a draft chart's data from its governed query, as the caller.

    Runs under the caller's active role through the Dataviz aggregate planner and
    QueryService, so row filters and column masks apply. Never returns stored rows.
    """
    return await studio_drafts.query(ref, user, body)


@router.get("/drafts/{ref}", response_class=SanitizingJSONResponse)
async def draft_state(
    user: CurrentUser,
    ref: str = Path(min_length=8, max_length=1024, pattern=_REF),
) -> dict[str, Any]:
    """A chart draft's latest revision, its fields, capability and save receipt (no rows)."""
    try:
        return await studio_artifacts.state(ref, user)
    except DraftError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/drafts/{ref}/capability", response_class=SanitizingJSONResponse)
async def draft_capability(
    user: CurrentUser,
    ref: str = Path(min_length=8, max_length=1024, pattern=_REF),
) -> dict[str, Any]:
    """Whether the caller's active role can save this chart draft now, and its save receipt."""
    try:
        return await studio_artifacts.capability(ref, user)
    except DraftError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/drafts/{ref}/revisions", response_class=SanitizingJSONResponse)
async def revise_draft(
    body: RevisionBody, user: CurrentUser,
    ref: str = Path(min_length=8, max_length=1024, pattern=_REF),
) -> dict[str, Any]:
    """Record an edited chart definition as the draft's next revision (no data, no save)."""
    try:
        return await studio_artifacts.revise(ref, user, chart=body.chart,
                                             base_revision=body.base_revision)
    except DraftError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
