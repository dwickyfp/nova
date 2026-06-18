"""User Management API schemas — request/response models."""

from pydantic import BaseModel


class UserCreate(BaseModel):
    """Request body for creating a new StarRocks user."""

    username: str
    password: str
    role: str | None = None


class UserResponse(BaseModel):
    """Response model for a single user with their roles."""

    username: str
    roles: list[str]


class UserRoleAssign(BaseModel):
    """Request body for assigning a role to a user."""

    username: str
    role: str


class UserListResponse(BaseModel):
    """Response model for listing users."""

    users: list[UserResponse]
    count: int
