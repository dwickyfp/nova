"""Data governance schemas — masking policies and row access policies.

The module wraps engine DDL (``CREATE/ALTER/DROP MASKING POLICY``,
``CREATE/ALTER/DROP ROW ACCESS POLICY``) plus the two binding statements
(``ALTER TABLE … SET MASKING POLICY``, ``ALTER TABLE … ADD/DROP ROW ACCESS
POLICY``). Requests carry an identifier and a policy *body*, never a value that
is interpolated unquoted into SQL: the body is the engine's own expression and
is validated as a single statement before it reaches the guard, so a request
cannot smuggle a second statement through it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

#: Engine identifiers Nova accepts for policies. Deliberately conservative: a
#: policy name is interpolated into DDL on the caller's connection, and a path
#: segment is not trusted just because the create body validated it.
POLICY_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"


class MaskingPolicyCreate(BaseModel):
    """``CREATE MASKING POLICY <name> AS (val <type>) -> <body>``.

    ``body`` is the masking expression (a ``CASE``/scalar expression). It is
    checked as one statement, so ``;`` or a second DDL keyword is refused here
    rather than by the engine.
    """

    name: str = Field(..., min_length=1, max_length=256, pattern=POLICY_NAME_PATTERN)
    column_type: str = Field("STRING", min_length=1, max_length=64)
    body: str = Field(..., min_length=1, max_length=8192)
    comment: str | None = Field(None, max_length=1024)


class MaskingPolicyAlter(BaseModel):
    """``ALTER MASKING POLICY <name> SET BODY -> <body>``."""

    body: str = Field(..., min_length=1, max_length=8192)


class MaskingPolicyResponse(BaseModel):
    name: str
    body: str | None = None
    bound_columns: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    created_by: str | None = None


class MaskingPolicyListResponse(BaseModel):
    policies: list[MaskingPolicyResponse]
    count: int


class ColumnBindingRequest(BaseModel):
    """``ALTER TABLE <db>.<table> MODIFY COLUMN <column> SET MASKING POLICY <name>``.

    A ``null`` policy unbinds the column. StarRocks spells unbinding as
    ``DROP MASKING POLICY`` on the column; the service translates the null into
    that statement so the API stays one shape.
    """

    database: str = Field(..., min_length=1, max_length=256, pattern=POLICY_NAME_PATTERN)
    table: str = Field(..., min_length=1, max_length=256, pattern=POLICY_NAME_PATTERN)
    column: str = Field(..., min_length=1, max_length=256, pattern=POLICY_NAME_PATTERN)
    policy_name: str | None = Field(None, max_length=256, pattern=POLICY_NAME_PATTERN)


class RowAccessPolicyCreate(BaseModel):
    """``CREATE ROW ACCESS POLICY <name> AS (<arg> <type>) -> <body>``."""

    name: str = Field(..., min_length=1, max_length=256, pattern=POLICY_NAME_PATTERN)
    argument_name: str = Field("col", min_length=1, max_length=64, pattern=POLICY_NAME_PATTERN)
    argument_type: str = Field("STRING", min_length=1, max_length=64)
    body: str = Field(..., min_length=1, max_length=8192)
    comment: str | None = Field(None, max_length=1024)


class RowAccessPolicyAlter(BaseModel):
    """``ALTER ROW ACCESS POLICY <name> SET BODY -> <body>``."""

    body: str = Field(..., min_length=1, max_length=8192)


class RowAccessPolicyResponse(BaseModel):
    name: str
    body: str | None = None
    bound_tables: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    created_by: str | None = None


class RowAccessPolicyListResponse(BaseModel):
    policies: list[RowAccessPolicyResponse]
    count: int


class TableBindingRequest(BaseModel):
    """``ALTER TABLE <db>.<table> ADD ROW ACCESS POLICY <name> ON (<column>)``."""

    database: str = Field(..., min_length=1, max_length=256, pattern=POLICY_NAME_PATTERN)
    table: str = Field(..., min_length=1, max_length=256, pattern=POLICY_NAME_PATTERN)
    column: str = Field(..., min_length=1, max_length=256, pattern=POLICY_NAME_PATTERN)
    policy_name: str = Field(..., min_length=1, max_length=256, pattern=POLICY_NAME_PATTERN)
