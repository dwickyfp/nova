"""Auth request/response schemas."""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    status: str  # "AUTHENTICATED" | "SETUP_REQUIRED"
    access_token: str | None = None
    token_type: str = "bearer"
    user: str | None = None
    roles: list[str] = []
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
    session_id: str
