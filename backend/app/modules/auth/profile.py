from pydantic import BaseModel, ConfigDict, Field

from app.common.audit import write_audit_log
from app.core.database import db

PROFILE_KEY = "account.profile"


class ProfileFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    first_name: str = Field(default="", max_length=100)
    last_name: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=254, pattern=r"^(?:[^\s@]+@[^\s@]+\.[^\s@]+)?$")


class ProfileResponse(ProfileFields):
    username: str


async def get_profile(username: str) -> ProfileResponse:
    result = await db.execute_system(
        "SELECT pref_value FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
        "WHERE user_name = %s AND pref_key = %s",
        [username, PROFILE_KEY],
    )
    fields = (
        ProfileFields.model_validate_json(result["rows"][0][0])
        if result["rows"]
        else ProfileFields()
    )
    return ProfileResponse(username=username, **fields.model_dump())


async def save_profile(user: dict, fields: ProfileFields) -> ProfileResponse:
    username = user["username"]
    await db.execute_system(
        "INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
        "(user_name, pref_key, pref_value, updated_at) VALUES (%s, %s, %s, NOW())",
        [username, PROFILE_KEY, fields.model_dump_json()],
    )
    await write_audit_log(
        event_type="PROFILE",
        user_name=username,
        action="UPDATE",
        object_type="USER_PROFILE",
        object_name=username,
        status="SUCCESS",
        session_id=user.get("session_id"),
    )
    return ProfileResponse(username=username, **fields.model_dump())
