"""Request and response models of catalog business metadata and search."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

TAG_KEY_PATTERN = r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$"
PRINCIPAL_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.@-]{0,127}$"


class Classification(StrEnum):
    """Sensitivity of a column's values. Advisory: Ranger masks still decide."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class OwnerType(StrEnum):
    USER = "user"
    ROLE = "role"


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    if _CONTROL.search(value):
        raise ValueError("control characters are not allowed")
    value = value.strip()
    return value or None


class Tag(BaseModel):
    key: str
    value: str | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None


class TagWrite(BaseModel):
    key: str = Field(..., pattern=TAG_KEY_PATTERN)
    value: str | None = Field(None, max_length=256)

    @field_validator("value")
    @classmethod
    def clean_value(cls, value: str | None) -> str | None:
        return _clean(value)


class TagsWrite(BaseModel):
    """Set several tags at once; keys not named keep their values."""

    tags: list[TagWrite] = Field(..., min_length=1, max_length=50)


class AnnotationWrite(BaseModel):
    """Fields to change. A field left out is unchanged; ``null`` clears it."""

    comment: str | None = Field(None, max_length=4096)
    owner_name: str | None = Field(None, pattern=PRINCIPAL_PATTERN)
    owner_type: OwnerType | None = None
    classification: Classification | None = None

    @field_validator("comment")
    @classmethod
    def clean_comment(cls, value: str | None) -> str | None:
        return _clean(value)


class LinkedOwner(BaseModel):
    owner_type: str
    owner_id: str
    label: str


class SecurableSummary(BaseModel):
    id: str
    securable_type: str
    full_name: str
    catalog_name: str
    database_name: str | None = None
    object_name: str | None = None
    column_name: str | None = None
    data_type: str | None = None
    status: str
    dropped_at: datetime | None = None
    comment: str | None = None
    engine_comment: str | None = None
    owner_name: str | None = None
    owner_type: str | None = None
    classification: str | None = None
    tags: list[Tag] = Field(default_factory=list)


class SecurableDetail(SecurableSummary):
    first_seen_at: datetime | None = None
    parent_id: str | None = None
    can_edit: bool = False
    can_set_owner: bool = False
    columns: list[SecurableSummary] = Field(default_factory=list)
    used_by: list[LinkedOwner] = Field(default_factory=list)


class SearchHit(SecurableSummary):
    matched: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    query: str | None
    tag: str | None
    results: list[SearchHit]
    count: int
    truncated: bool = False


class LineageNode(BaseModel):
    id: str
    full_name: str
    status: str
    statement_kind: str
    last_seen_at: datetime | None = None


class LineageResponse(BaseModel):
    id: str
    full_name: str
    upstream: list[LineageNode]
    downstream: list[LineageNode]
