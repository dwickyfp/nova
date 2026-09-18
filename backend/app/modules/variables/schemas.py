"""Variables & settings schemas — session/global browse + ``SET`` validation.

The engine is the source of truth for variable names and values: Nova reads
them back with ``SHOW [GLOBAL] VARIABLES`` and writes them with
``SET [SESSION|GLOBAL]``. These schemas only shape the request/response and
validate the inputs Nova interpolates into the ``SET`` statement.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

#: Engine variable names are identifiers. ``SET`` takes the name, not a quoted
#: identifier, so the shape is enforced here and again in the service.
VARIABLE_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.]*$"

#: A ``SET`` value is rendered bare (``SET SESSION x = 600``) or as a quoted
#: string. The service picks the quoting; this bounds the characters so a value
#: cannot start a second statement.
VALUE_PATTERN = r"^[A-Za-z0-9_.,+\-:/@% *]*$"


class VariableScope(StrEnum):
    """Where a variable is read from or written to."""

    SESSION = "session"
    GLOBAL = "global"


class VariableItem(BaseModel):
    name: str
    value: str
    default: str | None = None
    scope: VariableScope = VariableScope.SESSION


class VariableListResponse(BaseModel):
    variables: list[VariableItem]
    count: int
    total: int
    limit: int
    offset: int
    scope: VariableScope


class VariableSetRequest(BaseModel):
    """``SET [SESSION|GLOBAL] <name> = <value>``.

    ``global`` is a Python keyword, so the field is named ``scope``. ``value``
    is a string; the service renders it as a bare token or a quoted literal.
    ``reset`` issues ``SET <name> = DEFAULT`` and ignores ``value``.
    """

    scope: VariableScope = VariableScope.SESSION
    name: str = Field(..., min_length=1, max_length=256, pattern=VARIABLE_NAME_PATTERN)
    value: str | None = Field(None, max_length=4096, pattern=VALUE_PATTERN)
    reset: bool = False


class VariableSetResponse(BaseModel):
    success: bool
    name: str
    scope: VariableScope
    value: str | None = None
    message: str


class PasswordPolicy(BaseModel):
    """The password-policy subset `docs/22-variables-settings.md` documents.

    Every field is optional: the settings page sends only what changed, and the
    service writes exactly those `SET GLOBAL` statements.
    """

    password_lifetime: int | None = Field(None, ge=0, le=3650)
    password_history: int | None = Field(None, ge=0, le=100)
    failed_login_attempts: int | None = Field(None, ge=0, le=1000)
    password_lock_time: int | None = Field(None, ge=0, le=100000)
    validate_password: bool | None = None
    validate_password_length: int | None = Field(None, ge=1, le=256)
    validate_password_mixed_case_count: int | None = Field(None, ge=0, le=64)
    validate_password_number_count: int | None = Field(None, ge=0, le=64)
    validate_password_special_char_count: int | None = Field(None, ge=0, le=64)
