"""User management API schemas for StarRocks-backed administration."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

DefaultRoleMode = Literal["explicit", "all", "none"]
PrivilegeScope = Literal[
    "SYSTEM",
    "CATALOG",
    "DATABASE",
    "TABLE",
    "VIEW",
    "MATERIALIZED VIEW",
]
PrivilegeSelectorMode = Literal["specific", "all_databases", "all_in_database"]
RoleMemberType = Literal["user", "role"]


class UserCreate(BaseModel):
    """Request body for creating a StarRocks user."""

    username: str
    password: str
    host: str = "%"
    granted_roles: list[str] = Field(default_factory=list)
    default_role_mode: DefaultRoleMode = "none"
    default_roles: list[str] = Field(default_factory=list)
    max_user_connections: int | None = None
    catalog: str | None = None
    database: str | None = None
    session_properties: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_default_roles(self) -> "UserCreate":
        if self.default_role_mode == "explicit" and not self.default_roles:
            raise ValueError("default_roles is required when default_role_mode is explicit")
        if self.default_role_mode != "explicit" and self.default_roles:
            raise ValueError("default_roles can only be provided when default_role_mode is explicit")
        return self


class UserUpdate(BaseModel):
    """Request body for updating an existing StarRocks user."""

    password: str | None = None
    granted_roles_add: list[str] = Field(default_factory=list)
    granted_roles_remove: list[str] = Field(default_factory=list)
    default_role_mode: DefaultRoleMode | None = None
    default_roles: list[str] = Field(default_factory=list)
    max_user_connections: int | None = None
    catalog: str | None = None
    database: str | None = None
    session_properties: dict[str, str] = Field(default_factory=dict)
    clear_properties: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_payload(self) -> "UserUpdate":
        if self.default_role_mode == "explicit" and not self.default_roles:
            raise ValueError("default_roles is required when default_role_mode is explicit")
        if self.default_role_mode not in (None, "explicit") and self.default_roles:
            raise ValueError("default_roles can only be provided when default_role_mode is explicit")
        return self


class UserResponse(BaseModel):
    """Normalized StarRocks user identity response."""

    username: str
    host: str
    identity: str
    is_protected: bool
    roles: list[str] = Field(default_factory=list)
    default_roles: list[str] = Field(default_factory=list)
    default_role_mode: DefaultRoleMode = "none"
    auth_plugin: str | None = None
    auth_mode: str = "native_password"
    password_enabled: bool = False
    last_login: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)


class UserListResponse(BaseModel):
    """Response model for listing users."""

    users: list[UserResponse]
    count: int


class UserRoleAssign(BaseModel):
    """Request body for assigning a role to a user."""

    role: str
    host: str = "%"


class UserResetPasswordResponse(BaseModel):
    """Response payload for an admin-triggered password reset."""

    username: str
    host: str
    password: str
    message: str


class UserAuthenticationResponse(BaseModel):
    """Normalized authentication details for a user identity."""

    username: str
    host: str
    identity: str
    password_enabled: bool
    auth_plugin: str | None = None
    auth_mode: str = "native_password"
    plugin_user: str | None = None


class UserDefaultRolesResponse(BaseModel):
    """Default-role state for a user identity."""

    username: str
    host: str
    identity: str
    mode: DefaultRoleMode
    roles: list[str] = Field(default_factory=list)


class RoleCreate(BaseModel):
    """Request body for creating a role."""

    role_name: str


class RoleMemberChange(BaseModel):
    """Request body for granting/revoking role membership."""

    member_type: RoleMemberType
    member_name: str
    host: str = "%"


class RolePrivilegeChange(BaseModel):
    """Structured privilege grant/revoke payload."""

    privilege: str
    scope: PrivilegeScope
    selector_mode: PrivilegeSelectorMode = "specific"
    catalog: str | None = None
    database: str | None = None
    object_name: str | None = None
    with_grant_option: bool = False

    @model_validator(mode="after")
    def validate_selector(self) -> "RolePrivilegeChange":
        scope = self.scope
        mode = self.selector_mode

        if scope in {"SYSTEM", "CATALOG"} and mode != "specific":
            raise ValueError(f"{scope} privileges only support selector_mode='specific'")

        if scope == "SYSTEM":
            if self.catalog or self.database or self.object_name:
                raise ValueError("SYSTEM privileges cannot target catalog/database/object")
            return self

        if scope == "CATALOG":
            if not self.catalog:
                raise ValueError("catalog is required for CATALOG scope")
            if self.database or self.object_name:
                raise ValueError("CATALOG scope cannot target database/object")
            return self

        if scope == "DATABASE":
            if mode == "specific" and not self.database:
                raise ValueError("database is required for DATABASE scope")
            if mode == "all_databases" and (self.database or self.object_name):
                raise ValueError("DATABASE all_databases cannot include database/object_name")
            if self.object_name:
                raise ValueError("DATABASE scope cannot include object_name")
            return self

        if mode == "specific":
            if not self.database or not self.object_name:
                raise ValueError(f"{scope} specific privileges require database and object_name")
        elif mode == "all_in_database":
            if not self.database:
                raise ValueError(f"{scope} all_in_database privileges require database")
            if self.object_name:
                raise ValueError("all_in_database cannot include object_name")
        elif mode == "all_databases" and (self.database or self.object_name):
            raise ValueError("all_databases cannot include database/object_name")

        return self
