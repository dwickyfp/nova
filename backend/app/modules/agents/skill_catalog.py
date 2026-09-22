"""Read-only platform skills exposed beside caller-owned Agent Studio skills."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.modules.assistant.skill_registry import skill_library

BUILTIN_SKILL_ID_PREFIX = "builtin:"
BUILTIN_SKILL_OWNER = "__nova__"
_PACKAGE_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def builtin_skill_rows() -> list[dict[str, Any]]:
    """Return stable API rows for packaged skills.

    Packaged Markdown has no database timestamps. A fixed timestamp keeps the
    response deterministic and avoids pretending a deploy time is authorship.
    """
    return [
        {
            "skill_id": f"{BUILTIN_SKILL_ID_PREFIX}{skill.name}",
            "owner_name": BUILTIN_SKILL_OWNER,
            "name": skill.name,
            "description": skill.summary,
            "body": skill.body,
            "scope": "global",
            "source": "builtin",
            "read_only": True,
            "created_at": _PACKAGE_EPOCH,
            "updated_at": _PACKAGE_EPOCH,
        }
        for skill in skill_library.skills
    ]


def merge_skill_rows(user_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge platform and user rows, reserving packaged skill names."""
    builtins = builtin_skill_rows()
    reserved = {row["name"] for row in builtins}
    users = [
        {**row, "source": "user", "read_only": False}
        for row in user_rows
        if row.get("name") not in reserved
    ]
    return [*builtins, *users]


def is_builtin_skill_id(skill_id: str) -> bool:
    return skill_id.startswith(BUILTIN_SKILL_ID_PREFIX)
