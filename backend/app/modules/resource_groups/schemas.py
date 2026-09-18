"""Resource group schemas — warehouse/resource-group CRUD + classifiers.

A resource group's limits are engine attributes, so the create/alter bodies
carry a typed subset of the documented ``WITH (...)`` attributes and the
service serializes exactly those. Quota *enforcement* is the engine's; Nova
only configures it (roadmap #16).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

#: Engine identifiers Nova accepts. A resource-group name is interpolated into
#: DDL, so a path segment is not trusted just because the create body validated.
NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"

#: The attributes `docs/12-resource-groups.md` documents as configurable.
#: ``exclusive_cpu_cores`` is included because the doc lists it as the hard
#: CPU-isolation knob. Values are strings because that is what the engine's
#: ``WITH (...)`` grammar takes.
RESOURCE_GROUP_ATTRIBUTES = (
    "cpu_core_limit",
    "mem_limit",
    "concurrency_limit",
    "big_query_cpu_second_limit",
    "big_query_scan_rows_limit",
    "big_query_mem_limit",
    "spill_mem_limit_threshold",
    "warehouses",
    "cpu_weight_percent",
    "exclusive_cpu_weight",
    "exclusive_cpu_cores",
)


class ClassifierSpec(BaseModel):
    """One classifier rule.

    An empty rule matches everything, which is how StarRocks spells the default
    classifier, so at least one field is not required.
    """

    user: str | None = Field(None, max_length=256)
    role: str | None = Field(None, max_length=256)
    query_type: str | None = Field(None, max_length=32)
    source_ip: str | None = Field(None, max_length=256)


class ResourceGroupCreate(BaseModel):
    """``CREATE RESOURCE GROUP <name> WITH (<attrs>)``."""

    name: str = Field(..., min_length=1, max_length=256, pattern=NAME_PATTERN)
    properties: dict[str, Any] = Field(default_factory=dict)
    classifiers: list[ClassifierSpec] = Field(default_factory=list)


class ResourceGroupAlter(BaseModel):
    """``ALTER RESOURCE GROUP <name> SET (<attrs>)``."""

    properties: dict[str, Any] = Field(default_factory=dict)


class ResourceGroupResponse(BaseModel):
    name: str
    properties: dict[str, Any] = Field(default_factory=dict)
    classifiers: list[ClassifierSpec] = Field(default_factory=list)
    created_at: datetime | None = None
    created_by: str | None = None


class ResourceGroupListResponse(BaseModel):
    resource_groups: list[ResourceGroupResponse]
    count: int
