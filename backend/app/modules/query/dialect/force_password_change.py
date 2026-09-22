"""Parser for Nova's `ALTER USER … REQUIRE PASSWORD CHANGE` statement.

StarRocks has no "must change password at next login" attribute, so Nova models
it as its own statement: it is **intercepted in Python**, records the flag in
``NOVA_SYSTEM.CONFIG_USER_PREFERENCES``, and is **never sent to StarRocks**. The
grammar mirrors StarRocks' own ``ALTER USER`` prefix so the statement reads
naturally next to a real ``CREATE USER``.

Accepted forms (case-insensitive, whitespace-tolerant)::

    ALTER USER <name> REQUIRE PASSWORD CHANGE;
    ALTER USER '<name>'@'<host>' REQUIRE PASSWORD CHANGE;

The inverse (clearing the flag) is also recognized, because a UI toggle needs it::

    ALTER USER <name> REQUIRE PASSWORD CHANGE OFF;   -- or NONE

Only the *flag* is Nova's; the password itself stays in StarRocks. A companion
statement should set the password (`SET PASSWORD FOR … = PASSWORD('…')` or
`CREATE USER … IDENTIFIED BY …`), which Nova passes through to the engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: ``ALTER USER <identity> REQUIRE PASSWORD CHANGE [OFF|NONE]``.
#: The identity is either a bare/backticked identifier or a quoted
#: ``'user'@'host'`` pair. The trailing ``OFF``/``NONE`` is optional and means
#: "clear the flag".
_FORCE_CHANGE_PATTERN = re.compile(
    r"^\s*ALTER\s+USER\s+"
    r"(?P<identity>"
    r"(?:'[^']+'|\"[^\"]+\"|`[^`]+`|[A-Za-z_][A-Za-z0-9_.$-]*)"
    r"(?:\s*@\s*(?:'[^']+'|\"[^\"]+\"|`[^`]+`|[A-Za-z_][A-Za-z0-9_.$-]*))?"
    r")"
    r"\s+REQUIRE\s+PASSWORD\s+CHANGE"
    r"(?:\s+(?P<clear>OFF|NONE))?"
    r"\s*;?\s*$",
    re.IGNORECASE,
)

#: A quoted ``'user'@'host'`` pair; the user part is the first quoted token.
_QUOTED_IDENTITY_PATTERN = re.compile(r"^\s*['\"`](?P<user>[^'\"`]+)['\"`]")

#: A bare or backticked identifier: the user is the whole token.
_BARE_IDENTITY_PATTERN = re.compile(r"^\s*`?(?P<user>[A-Za-z_][A-Za-z0-9_.$-]*)`?")


@dataclass(frozen=True)
class ForcePasswordChangeStatement:
    """Parsed ``ALTER USER … REQUIRE PASSWORD CHANGE`` statement."""

    username: str
    #: True to require a change, False to clear the requirement.
    required: bool


def is_force_password_change(sql: str) -> bool:
    """True when ``sql`` is a Nova password-change-requirement statement."""
    return bool(_FORCE_CHANGE_PATTERN.match(sql))


def parse_force_password_change(sql: str) -> ForcePasswordChangeStatement:
    """Parse the statement, or raise ``ValueError`` for a malformed one.

    The username is extracted from the identity, host stripped. A quoted identity
    keeps internal dots/spaces; a bare one is taken whole.
    """
    match = _FORCE_CHANGE_PATTERN.match(sql)
    if not match:
        raise ValueError(
            "Invalid ALTER USER … REQUIRE PASSWORD CHANGE syntax. "
            "Expected: ALTER USER <name> REQUIRE PASSWORD CHANGE [OFF]"
        )
    identity = match.group("identity").strip()
    user_match = _QUOTED_IDENTITY_PATTERN.match(identity) or _BARE_IDENTITY_PATTERN.match(identity)
    if not user_match:
        raise ValueError("The statement does not name a user")
    username = user_match.group("user").strip()
    if not username:
        raise ValueError("The statement does not name a user")
    required = match.group("clear") is None
    return ForcePasswordChangeStatement(username=username, required=required)


__all__ = [
    "ForcePasswordChangeStatement",
    "is_force_password_change",
    "parse_force_password_change",
]
