"""Management authority for Nova Intelligence metadata objects."""

from __future__ import annotations


def can_manage(owner_name: str, user: dict) -> bool:
    """The owner or the caller's active ACCOUNTADMIN role may manage an object."""
    return owner_name == user.get("username") or (
        str(user.get("active_role") or "").upper() == "ACCOUNTADMIN"
    )
