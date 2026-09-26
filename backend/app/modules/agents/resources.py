"""Explicit resource scope, intersected with the caller's existing permissions."""

from __future__ import annotations

import math
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SearchBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: str = Field(min_length=1, max_length=128)
    filters: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=16)

    @model_validator(mode="after")
    def valid_filters(self) -> SearchBinding:
        if any(isinstance(v, float) and not math.isfinite(v) for v in self.filters.values()):
            raise ValueError("Filter numbers must be finite")
        if any(not k or len(k) > 128 for k in self.filters):
            raise ValueError("Invalid filter column")
        return self


class ResourceBindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search_indexes: list[SearchBinding] = Field(default_factory=list, max_length=32)
    feature_groups: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def unique_names(self) -> ResourceBindings:
        names = [item.index for item in self.search_indexes]
        if len(names) != len(set(names)) or len(self.feature_groups) != len(
            set(self.feature_groups)
        ):
            raise ValueError("Resource bindings must be unique")
        if any(not name or len(name) > 128 for name in [*names, *self.feature_groups]):
            raise ValueError("Invalid resource name")
        return self


async def validate_resources(fields: dict[str, Any], user: dict) -> None:
    if "resource_bindings" not in fields:
        return
    from app.modules.intelligence.feature_store import feature_store
    from app.modules.intelligence.search import search_service

    bindings = ResourceBindings.model_validate(fields["resource_bindings"] or {})
    indexes = (
        {item["name"]: item for item in await search_service.list(user)}
        if bindings.search_indexes
        else {}
    )
    for binding in bindings.search_indexes:
        definition = indexes.get(binding.index)
        if definition is None or not definition.get("active_version"):
            raise HTTPException(422, "Search index is unavailable or unauthorized")
        if set(binding.filters) - set(definition.get("filter_columns") or []):
            raise HTTPException(422, "Fixed filters must use the index's filter columns")
    if bindings.feature_groups:
        groups = {
            item["name"]
            for item in await feature_store.list_groups(user)
            if item.get("active_version")
        }
        if set(bindings.feature_groups) - groups:
            raise HTTPException(422, "Feature Group is unavailable or unauthorized")
    fields["resource_bindings"] = bindings.model_dump()


def scoped_filters(bindings: dict, name: str, requested: dict) -> dict:
    binding = next(
        (item for item in bindings.get("search_indexes", []) if item["index"] == name), None
    )
    if binding is None:
        raise ValueError("Search index is not bound to this agent")
    fixed = binding.get("filters") or {}
    if any(
        key in requested and (type(requested[key]) is not type(value) or requested[key] != value)
        for key, value in fixed.items()
    ):
        raise ValueError("Search filters conflict with this agent's resource scope")
    return {**requested, **fixed}


async def authorized_resources(bindings: dict, user: dict) -> dict:
    from app.modules.intelligence.feature_store import feature_store
    from app.modules.intelligence.search import search_service

    indexes = (
        {item["name"] for item in await search_service.list(user) if item.get("active_version")}
        if bindings.get("search_indexes")
        else set()
    )
    groups = (
        {
            item["name"]
            for item in await feature_store.list_groups(user)
            if item.get("active_version")
        }
        if bindings.get("feature_groups")
        else set()
    )
    return {
        "search_indexes": [
            item for item in bindings.get("search_indexes", []) if item["index"] in indexes
        ],
        "feature_groups": [name for name in bindings.get("feature_groups", []) if name in groups],
    }
