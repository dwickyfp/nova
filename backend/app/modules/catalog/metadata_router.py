"""Nova Catalog business metadata, search, and lineage (``/api/v1/catalog``).

  GET    /search?q=&tag=&type=&include_dropped=&limit=  → securables the caller can see
  GET    /objects/resolve?catalog=&database=&object=&column= → a name's securable
  GET    /objects/{id}                                  → detail, columns, tags, dependents
  GET    /objects/{id}/tags                             → tags
  PUT    /objects/{id}/tags                             → set tags (audited)
  DELETE /objects/{id}/tags/{key}                       → remove a tag (audited)
  PATCH  /objects/{id}/annotations                      → comment, owner, classification
  GET    /objects/{id}/lineage                          → upstream and downstream relations

``{id}`` is a securable id. A securable the caller cannot see answers 404, the
same as one that does not exist. Every response passes
``SanitizingJSONResponse``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user

from .governance import (
    MetadataDenied,
    MetadataInvalid,
    MetadataNotFound,
    governance_service,
)
from .metadata_schemas import (
    TAG_KEY_PATTERN,
    AnnotationWrite,
    LineageResponse,
    SearchResponse,
    SecurableDetail,
    Tag,
    TagsWrite,
)

router = APIRouter()

CurrentUser = Annotated[dict, Depends(get_current_user)]
_ID = r"^[0-9a-f]{64}$"
_NAME = r"^[^\x00-\x1f`]{1,256}$"


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, MetadataNotFound):
        return HTTPException(status_code=404, detail="Object not found")
    if isinstance(exc, MetadataDenied):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


_ERRORS = (MetadataNotFound, MetadataDenied, MetadataInvalid)


@router.get("/search", response_model=SearchResponse, response_class=SanitizingJSONResponse)
async def search(
    user: CurrentUser,
    q: Annotated[str | None, Query(max_length=200)] = None,
    tag: Annotated[str | None, Query(max_length=400)] = None,
    type: Annotated[list[str] | None, Query()] = None,  # noqa: A002 - public query name
    include_dropped: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
):
    try:
        return await governance_service.search(
            user, query=q, tag=tag, types=type, include_dropped=include_dropped, limit=limit
        )
    except _ERRORS as exc:
        raise _http(exc) from exc


@router.get("/objects/resolve", response_model=SecurableDetail,
            response_class=SanitizingJSONResponse)
async def resolve(
    user: CurrentUser,
    catalog: Annotated[str, Query(pattern=_NAME)],
    database: Annotated[str | None, Query(pattern=_NAME)] = None,
    object: Annotated[str | None, Query(pattern=_NAME)] = None,  # noqa: A002
    column: Annotated[str | None, Query(pattern=_NAME)] = None,
):
    try:
        return await governance_service.resolve(
            user, catalog=catalog, database=database, object_name=object, column=column
        )
    except _ERRORS as exc:
        raise _http(exc) from exc


@router.get("/objects/{securable_id}", response_model=SecurableDetail,
            response_class=SanitizingJSONResponse)
async def describe(securable_id: Annotated[str, Path(pattern=_ID)], user: CurrentUser):
    try:
        return await governance_service.describe(securable_id, user)
    except _ERRORS as exc:
        raise _http(exc) from exc


@router.get("/objects/{securable_id}/tags", response_model=list[Tag],
            response_class=SanitizingJSONResponse)
async def list_tags(securable_id: Annotated[str, Path(pattern=_ID)], user: CurrentUser):
    try:
        return (await governance_service.describe(securable_id, user)).tags
    except _ERRORS as exc:
        raise _http(exc) from exc


@router.put("/objects/{securable_id}/tags", response_model=SecurableDetail,
            response_class=SanitizingJSONResponse)
async def put_tags(
    securable_id: Annotated[str, Path(pattern=_ID)], body: TagsWrite, user: CurrentUser
):
    try:
        return await governance_service.put_tags(securable_id, body.tags, user)
    except _ERRORS as exc:
        raise _http(exc) from exc


@router.delete("/objects/{securable_id}/tags/{key}", response_model=SecurableDetail,
               response_class=SanitizingJSONResponse)
async def delete_tag(
    securable_id: Annotated[str, Path(pattern=_ID)],
    key: Annotated[str, Path(pattern=TAG_KEY_PATTERN)],
    user: CurrentUser,
):
    try:
        return await governance_service.delete_tag(securable_id, key, user)
    except _ERRORS as exc:
        raise _http(exc) from exc


@router.patch("/objects/{securable_id}/annotations", response_model=SecurableDetail,
              response_class=SanitizingJSONResponse)
async def annotate(
    securable_id: Annotated[str, Path(pattern=_ID)], body: AnnotationWrite, user: CurrentUser
):
    try:
        return await governance_service.annotate(securable_id, body, user)
    except _ERRORS as exc:
        raise _http(exc) from exc


@router.get("/objects/{securable_id}/lineage", response_model=LineageResponse,
            response_class=SanitizingJSONResponse)
async def lineage(securable_id: Annotated[str, Path(pattern=_ID)], user: CurrentUser):
    try:
        return await governance_service.lineage(securable_id, user)
    except _ERRORS as exc:
        raise _http(exc) from exc
