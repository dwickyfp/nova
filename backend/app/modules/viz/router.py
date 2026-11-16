"""Native chart and dashboard commands through the shared document service."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user
from app.core.exceptions import NovaException

from .contracts.authoring import (
    DashboardDatasetCreate,
    DatasetCreate,
    DatasetInspect,
    DatasetInspection,
    DatasetPreview,
    DatasetPreviewRequest,
    DatasetUpdate,
)
from .contracts.charts import ChartDefinition
from .contracts.commands import (
    DeleteRequest,
    GrantsReplace,
    Operation,
    PublicationRequest,
    RestoreRequest,
)
from .contracts.common import Permission
from .contracts.dashboards import DashboardDefinition
from .contracts.http import (
    ChartCreate,
    DashboardCreate,
    DashboardDatasets,
    DocumentSnapshot,
    DocumentSummary,
    EventPage,
    GrantState,
    OperationReceipt,
    RevisionSummary,
)
from .contracts.queries import DatasetQueryRequest, DatasetQueryResult
from .dataset_service import native_dataset_service as datasets
from .document_service import native_document_service as service
from .documents import Kind
from .export_schedules import ScheduleRequest
from .export_schedules import native_export_schedules as schedules
from .presence import native_presence as presence

MAX_REVISION = 9_007_199_254_740_991


class DocumentRequest(Request):
    async def body(self) -> bytes:
        if not hasattr(self, "_body"):
            chunks: list[bytes] = []
            size = 0
            try:
                async with asyncio.timeout(10):
                    async for chunk in self.stream():
                        size += len(chunk)
                        if size > 1_048_576:
                            raise HTTPException(413, "Document request exceeds one MiB")
                        chunks.append(chunk)
            except TimeoutError:
                raise HTTPException(408, "Document request timed out") from None
            self._body = b"".join(chunks)
        return self._body


class DocumentRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
                if media_type != "application/json":
                    raise NovaException("Expected a JSON document request", status_code=415)
            try:
                response = await handler(DocumentRequest(request.scope, request.receive))
            except (RequestValidationError, RecursionError):
                # FastAPI validation details echo input values. Definitions and
                # sensitive parameters must not become error-response payloads.
                response = SanitizingJSONResponse(
                    {"detail": "Invalid document request"},
                    status_code=422,
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        return bounded


router = APIRouter(route_class=DocumentRoute, default_response_class=SanitizingJSONResponse)
User = Annotated[dict[str, Any], Depends(get_current_user)]
Collection = Literal["charts", "dashboards", "datasets"]
KINDS: dict[Collection, Kind] = {
    "charts": "chart",
    "dashboards": "dashboard",
    "datasets": "dataset",
}


def operation_key(value: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> UUID:
    if value is None:
        raise NovaException("Idempotency-Key is required", status_code=428)
    try:
        if len(value) != 36:
            raise ValueError
        return UUID(value)
    except ValueError:
        raise NovaException("Invalid Idempotency-Key", status_code=422) from None


def expected_revision(value: Annotated[str | None, Header(alias="If-Match")] = None) -> int:
    if value is None:
        raise NovaException("If-Match is required", status_code=428)
    if not re.fullmatch(r'"[1-9][0-9]{0,15}"', value):
        raise NovaException("Expected a quoted document revision in If-Match", status_code=422)
    revision = int(value[1:-1])
    if revision > MAX_REVISION:
        raise NovaException("Invalid document revision", status_code=422)
    return revision


Key = Annotated[UUID, Depends(operation_key)]
Expected = Annotated[int, Depends(expected_revision)]


async def resource(collection: Collection, identity: UUID, user: User) -> UUID:
    await service.require_kind(user, identity, KINDS[collection])
    return identity


Resource = Annotated[UUID, Depends(resource)]


def command_matches(
    body: Operation | PublicationRequest | RestoreRequest | GrantsReplace | DeleteRequest,
    key: UUID,
    revision: int,
) -> None:
    if body.operation_id != key or body.expected_revision != revision:
        raise NovaException("Command headers and body must agree", status_code=422)


@router.get("/{collection}", response_model=list[DocumentSummary])
async def browse_documents(
    collection: Collection,
    user: User,
    workspace_id: Annotated[str, Query(min_length=1, max_length=128)],
    draft: bool = False,
    before_time: AwareDatetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Any:
    if (before_time is None) != (before_id is None):
        raise NovaException("Both document cursor fields are required", status_code=422)
    before = (before_time, before_id) if before_time is not None and before_id is not None else None
    return await service.browse(
        user,
        workspace_id,
        kind=KINDS[collection],
        draft=draft,
        before=before,
        limit=limit,
    )


@router.get("/operations/{operation_id}", response_model=OperationReceipt)
async def lookup_operation(operation_id: UUID, user: User) -> Any:
    return await service.receipt(user, None, operation_id)


@router.post("/charts", response_model=OperationReceipt, status_code=201)
async def create_chart(body: ChartCreate, user: User, key: Key) -> Any:
    return await service.create_visual(user, body.workspace_id, body.definition, operation_id=key)


@router.post("/dashboards", response_model=OperationReceipt, status_code=201)
async def create_dashboard(body: DashboardCreate, user: User, key: Key) -> Any:
    return await service.create_visual(user, body.workspace_id, body.definition, operation_id=key)


@router.post("/datasets/inspect", response_model=DatasetInspection)
async def inspect_dataset_source(
    body: DatasetInspect, user: User
) -> DatasetInspection:
    return await datasets.inspect(user, body)


@router.post("/datasets", response_model=OperationReceipt, status_code=201)
async def create_dataset(body: DatasetCreate, user: User, key: Key) -> Any:
    return await datasets.create(user, body, operation_id=key)


@router.post("/dashboards/{identity}/datasets", response_model=OperationReceipt, status_code=201)
async def create_dashboard_dataset(
    identity: UUID, body: DashboardDatasetCreate, user: User, key: Key, revision: Expected,
) -> Any:
    return await datasets.create_in_dashboard(
        user, identity, body, operation_id=key, expected_revision=revision,
    )


@router.post("/datasets/{identity}/preview", response_model=DatasetPreview)
async def preview_dataset(
    identity: UUID, body: DatasetPreviewRequest, user: User
) -> DatasetPreview:
    return await datasets.preview(user, identity, body)


@router.post("/datasets/{identity}/query", response_model=DatasetQueryResult)
async def query_dataset(
    identity: UUID, body: DatasetQueryRequest, user: User,
) -> DatasetQueryResult:
    return await datasets.query(user, identity, body)


@router.patch("/datasets/{identity}/draft", response_model=OperationReceipt)
async def update_dataset(
    identity: UUID, body: DatasetUpdate,
    user: User, key: Key, revision: Expected,
) -> Any:
    if body.dataset_id != identity:
        raise NovaException("Document identity cannot be changed", status_code=422)
    return await datasets.update(user, body, operation_id=key, expected_revision=revision)


@router.get("/dashboards/{identity}/datasets", response_model=DashboardDatasets)
async def dashboard_datasets(
    identity: UUID, user: User,
    revision: Annotated[int, Query(ge=1, le=MAX_REVISION)],
) -> Any:
    return await service.dashboard_datasets(user, identity, revision)


@router.get("/{collection}/{identity}", response_model=DocumentSnapshot)
async def read_document(
    identity: Resource, user: User, response: Response, draft: bool = False,
    revision: Annotated[int | None, Query(ge=1, le=MAX_REVISION)] = None,
) -> Any:
    document = await service.read(user, identity, draft=draft, revision=revision)
    response.headers["ETag"] = f'"{document["revision"]}"'
    return document


@router.patch("/charts/{identity}/draft", response_model=OperationReceipt)
async def replace_chart(
    identity: UUID, body: ChartDefinition, user: User, key: Key, revision: Expected
) -> Any:
    if body.chart_id != identity:
        raise NovaException("Document identity cannot be changed", status_code=422)
    await service.require_kind(user, identity, "chart")
    return await service.replace_visual(user, body, operation_id=key, expected_revision=revision)


@router.patch("/dashboards/{identity}/draft", response_model=OperationReceipt)
async def replace_dashboard(
    identity: UUID, body: DashboardDefinition, user: User, key: Key, revision: Expected
) -> Any:
    if body.dashboard_id != identity:
        raise NovaException("Document identity cannot be changed", status_code=422)
    await service.require_kind(user, identity, "dashboard")
    return await service.replace_visual(user, body, operation_id=key, expected_revision=revision)


@router.post("/dashboards/{identity}/operations", response_model=OperationReceipt)
async def apply_operation(
    identity: UUID, body: Operation, user: User, key: Key, revision: Expected
) -> Any:
    command_matches(body, key, revision)
    await service.require_kind(user, identity, "dashboard")
    return await service.apply(user, identity, body)


@router.post("/{collection}/{identity}/publish", response_model=OperationReceipt)
async def publish(
    identity: Resource, body: PublicationRequest, user: User, key: Key, revision: Expected
) -> Any:
    command_matches(body, key, revision)
    return await service.publish(user, identity, body)


@router.post("/{collection}/{identity}/restore", response_model=OperationReceipt)
async def restore(
    identity: Resource, body: RestoreRequest, user: User, key: Key, revision: Expected
) -> Any:
    command_matches(body, key, revision)
    return await service.restore(user, identity, body)


@router.get("/{collection}/{identity}/grants", response_model=GrantState)
async def grants(identity: Resource, user: User, response: Response) -> Any:
    state = await service.grant_state(user, identity)
    response.headers["ETag"] = f'"{state["revision"]}"'
    return state


@router.put("/{collection}/{identity}/grants", response_model=OperationReceipt)
async def replace_grants(
    identity: Resource, body: GrantsReplace, user: User, key: Key, revision: Expected
) -> Any:
    command_matches(body, key, revision)
    return await service.replace_grants(user, identity, body)


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["png", "csv", "xlsx", "pdf"]
    chart_id: UUID | None = None


@router.post("/dashboards/{identity}/exports")
async def authorize_export(identity: UUID, body: ExportRequest, user: User) -> Any:
    """Check the Export permission and audit a download before the browser builds it."""
    await service.require_kind(user, identity, KINDS["dashboards"])
    return await service.authorize_export(user, identity, file_format=body.format,
                                          part=body.chart_id)


class PresenceCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Across the canvas, 0 (left) to 1 (right).
    x: float
    #: Down the canvas, in grid rows.
    y: float


class PresenceBeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: UUID | None = None
    cursor: PresenceCursor | None = None
    editing: bool = False


@router.put("/dashboards/{identity}/presence")
async def presence_beat(identity: UUID, body: PresenceBeat, user: User) -> Any:
    """Say this viewer is here and list the others; anyone who can view may take part."""
    async def can_view() -> bool:
        await service.require_kind(user, identity, KINDS["dashboards"])
        return "view" in await service.permissions(user, identity)

    try:
        return await presence.beat(
            user, identity, page_id=body.page_id,
            cursor=body.cursor.model_dump() if body.cursor else None,
            editing=body.editing, can_view=can_view)
    except PermissionError:
        raise HTTPException(status_code=404, detail="Dashboard not found") from None


@router.delete("/dashboards/{identity}/presence", status_code=204)
async def presence_leave(identity: UUID, user: User) -> Response:
    await presence.leave(user, identity)
    return Response(status_code=204)


@router.get("/dashboards/{identity}/schedules")
async def export_schedules(identity: UUID, user: User) -> Any:
    """Scheduled Excel exports of this dashboard's pages, for people who may download it."""
    return await schedules.list(user, identity)


@router.put("/dashboards/{identity}/schedules")
async def save_export_schedule(identity: UUID, body: ScheduleRequest, user: User) -> Any:
    return await schedules.save(user, identity, body)


@router.delete("/dashboards/{identity}/schedules/{schedule_id}", status_code=204)
async def remove_export_schedule(identity: UUID, schedule_id: UUID, user: User) -> Response:
    await schedules.remove(user, identity, schedule_id)
    return Response(status_code=204)


@router.get("/{collection}/{identity}/permissions", response_model=list[Permission])
async def permissions(identity: Resource, user: User) -> Any:
    return await service.permissions(user, identity)


@router.get("/{collection}/{identity}/revisions", response_model=list[RevisionSummary])
async def revisions(
    identity: Resource,
    user: User,
    before: Annotated[int | None, Query(ge=1, le=MAX_REVISION)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Any:
    return await service.history(user, identity, before=before, limit=limit)


@router.get("/{collection}/{identity}/operations/{operation_id}", response_model=OperationReceipt)
async def receipt(identity: Resource, operation_id: UUID, user: User) -> Any:
    return await service.receipt(user, identity, operation_id)


@router.get("/{collection}/{identity}/events", response_model=EventPage)
async def events(
    identity: Resource,
    user: User,
    after: Annotated[int, Query(ge=0, le=MAX_REVISION)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> Any:
    return await service.events(user, identity, after=after, limit=limit)


@router.delete("/{collection}/{identity}", response_model=OperationReceipt)
async def delete_document(
    collection: Collection,
    identity: UUID,
    body: DeleteRequest,
    user: User,
    key: Key,
    revision: Expected,
) -> Any:
    command_matches(body, key, revision)
    return await service.delete(user, identity, KINDS[collection], body)
