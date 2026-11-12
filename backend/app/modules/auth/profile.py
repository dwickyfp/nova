from pydantic import BaseModel, ConfigDict, Field

from app.common.audit import write_audit_log
from app.common.preference_store import read_value, write_values
from app.core.metadata_scope import actor_for, metadata_transaction

PROFILE_KEY = "account.profile"


class ProfileFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    first_name: str = Field(default="", max_length=100)
    last_name: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=254, pattern=r"^(?:[^\s@]+@[^\s@]+\.[^\s@]+)?$")


class ProfileResponse(ProfileFields):
    username: str


async def get_profile(user: dict) -> ProfileResponse:
    actor = actor_for(user)
    stored = await read_value(actor, actor.principal, PROFILE_KEY)
    fields = ProfileFields.model_validate_json(stored) if stored else ProfileFields()
    return ProfileResponse(username=actor.principal, **fields.model_dump())


async def save_profile(user: dict, fields: ProfileFields) -> ProfileResponse:
    """Save the caller's own profile; the change and its audit commit together."""
    actor = actor_for(user)
    username = actor.principal
    async with metadata_transaction(actor):
        await write_values(actor, username, {PROFILE_KEY: fields.model_dump_json()})
        await write_audit_log(
            event_type="PROFILE",
            user_name=username,
            action="UPDATE",
            object_type="USER_PROFILE",
            object_name=username,
            status="SUCCESS",
            session_id=user.get("session_id"),
            active_role=actor.active_role,
            security_context_version=actor.security_context_version,
        )
    return ProfileResponse(username=username, **fields.model_dump())
