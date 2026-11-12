"""Bound values must reach PostgreSQL already typed.

A cast such as ``%s::integer`` makes PostgreSQL parse a string, and a failed
parse quotes the whole value in its error message. Where server logging is not
the private metadata policy, that copies whatever the caller sent into server
logs (this is how a synthetic credential reached the fixture log on
2026-11-11). Convert values in Python and pass typed parameters instead.
``::text`` cannot fail and stays allowed. Each other cast must be reviewed here.
"""

from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
CAST = re.compile(r"%s::(?!text\b)([a-z_]+)", re.IGNORECASE)
#: path relative to app/ -> casts reviewed to receive only Python-typed values.
REVIEWED = {
    # ScheduledOccurrence.scheduled_at is a datetime built by the scheduler.
    "modules/task_orchestration/metadata_scheduler.py": {"timestamptz"},
}


def test_no_unreviewed_server_side_casts_of_bound_values():
    found: dict[str, set[str]] = {}
    for path in APP.rglob("*.py"):
        casts = {match.lower() for match in CAST.findall(path.read_text(encoding="utf-8"))}
        if casts:
            found[path.relative_to(APP).as_posix()] = casts
    unreviewed = {
        path: casts - REVIEWED.get(path, set())
        for path, casts in found.items()
        if casts - REVIEWED.get(path, set())
    }
    assert unreviewed == {}, (
        "Convert these values in Python and bind typed parameters, or review "
        f"the cast and add it to REVIEWED: {unreviewed}"
    )


def test_the_guard_recognises_a_leaking_cast():
    assert CAST.findall("SELECT %s::integer, %s::text, %s::uuid") == ["integer", "uuid"]
