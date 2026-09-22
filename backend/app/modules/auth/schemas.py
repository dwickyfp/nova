"""Auth request/response schemas."""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=0)


class LoginResponse(BaseModel):
    # "AUTHENTICATED" | "SETUP_REQUIRED" | "PASSWORD_CHANGE_REQUIRED"
    status: str
    access_token: str | None = None
    token_type: str = "bearer"
    user: str | None = None
    roles: list[str] = []
    assigned_roles: list[str] = []
    default_role: str | None = None
    active_role: str | None = None
    security_context_version: int = 1
    message: str | None = None


class SetupRequest(BaseModel):
    new_password: str = Field(..., min_length=8)
    confirm_password: str = Field(..., min_length=8)


class SetupResponse(BaseModel):
    status: str  # "SETUP_COMPLETE"
    message: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)
    confirm_password: str = Field(..., min_length=8)


class SessionInfo(BaseModel):
    username: str
    roles: list[str]
    assigned_roles: list[str] = []
    default_role: str | None = None
    active_role: str | None = None
    security_context_version: int = 1
    session_id: str
    #: True when the user must set a new password before using Nova. The UI
    #: routes them to the change-password screen on load if this is set, so a
    #: reload cannot skip the requirement.
    must_change_password: bool = False


class SwitchRoleRequest(BaseModel):
    role: str
