"""API contracts for Nova Access Control."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RoleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field("", max_length=1024)


class RoleMemberRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    host: str = Field("%", min_length=1, max_length=255)


class AccessPolicyRequest(BaseModel):
    role: str
    catalog: str = "default_catalog"
    database: str
    table: str
    accesses: list[str] = Field(min_length=1)


class ScopeBindingInput(BaseModel):
    dimension: str
    column: str
    values: list[str] = Field(min_length=1)


class DataScopeRequest(BaseModel):
    principal: str
    role: str
    catalog: str = "default_catalog"
    database: str
    table: str
    bindings: list[ScopeBindingInput] = Field(min_length=1)


class MaskPolicyRequest(BaseModel):
    role: str
    catalog: str = "default_catalog"
    database: str
    table: str
    column: str
    mask_type: str
    value_expression: str | None = None


class EffectiveAccessRequest(BaseModel):
    principal: str
    active_role: str
    resource: str
