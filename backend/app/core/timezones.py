"""Resolve the named zones and fixed offsets used by governed SQL sessions."""

from __future__ import annotations

import re
from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def resolve_timezone(name: str) -> tzinfo:
    offset = re.fullmatch(r"([+-])([0-9]{2}):([0-9]{2})", name)
    if offset:
        hours, minutes = int(offset[2]), int(offset[3])
        if hours > 14 or minutes > 59 or (hours == 14 and minutes):
            raise ValueError("Invalid timezone offset")
        delta = timedelta(hours=hours, minutes=minutes) * (-1 if offset[1] == "-" else 1)
        return timezone(delta)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise ValueError("Unknown timezone") from None
