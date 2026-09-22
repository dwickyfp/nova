"""Typed Ranger API payloads used by Nova.

The Ranger REST API is intentionally represented by small Pydantic models
instead of leaking untyped Ranger JSON into the application domain.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RangerRoleMember(BaseModel):
    name: str
    is_admin: bool = Field(False, alias="isAdmin")


class RangerRole(BaseModel):
    id: int | None = None
    name: str
    description: str = "Managed by Nova"
    users: list[RangerRoleMember] = Field(default_factory=list)
    groups: list[RangerRoleMember] = Field(default_factory=list)
    roles: list[RangerRoleMember] = Field(default_factory=list)


class RangerPolicyResource(BaseModel):
    values: list[str]
    is_excludes: bool = Field(False, alias="isExcludes")
    is_recursive: bool = Field(False, alias="isRecursive")


class RangerPolicyItemAccess(BaseModel):
    type: str
    is_allowed: bool = Field(True, alias="isAllowed")


class RangerPolicyItem(BaseModel):
    accesses: list[RangerPolicyItemAccess]
    users: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    delegate_admin: bool = Field(False, alias="delegateAdmin")


class RangerRowFilterInfo(BaseModel):
    filter_expr: str = Field(alias="filterExpr")


class RangerRowFilterPolicyItem(RangerPolicyItem):
    row_filter_info: RangerRowFilterInfo = Field(alias="rowFilterInfo")


class RangerDataMaskInfo(BaseModel):
    data_mask_type: str = Field(alias="dataMaskType")
    value_expr: str | None = Field(None, alias="valueExpr")


class RangerDataMaskPolicyItem(RangerPolicyItem):
    data_mask_info: RangerDataMaskInfo = Field(alias="dataMaskInfo")


class RangerPolicy(BaseModel):
    id: int | None = None
    guid: str | None = None
    service: str
    name: str
    description: str = "Managed by Nova"
    is_enabled: bool = Field(True, alias="isEnabled")
    policy_type: int = Field(0, alias="policyType")
    policy_priority: int = Field(0, alias="policyPriority")
    resources: dict[str, RangerPolicyResource]
    policy_items: list[RangerPolicyItem] = Field(default_factory=list, alias="policyItems")
    row_filter_policy_items: list[RangerRowFilterPolicyItem] = Field(
        default_factory=list, alias="rowFilterPolicyItems"
    )
    data_mask_policy_items: list[RangerDataMaskPolicyItem] = Field(
        default_factory=list, alias="dataMaskPolicyItems"
    )
    deny_policy_items: list[RangerPolicyItem] = Field(default_factory=list, alias="denyPolicyItems")
    deny_exceptions: list[RangerPolicyItem] = Field(default_factory=list, alias="denyExceptions")
    allow_exceptions: list[RangerPolicyItem] = Field(default_factory=list, alias="allowExceptions")

    def to_api(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)
