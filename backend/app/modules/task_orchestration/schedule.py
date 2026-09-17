"""Pure next-fire computation for Nova's cron/interval scheduler.

No I/O, no database, no Redis. StarRocks 4.1.1 has no cron and its ``START``
literals are interpreted in the engine's session timezone, so Nova owns both the
cron parser and the tick. Every computation here is done against an explicit
IANA timezone carried by the task — UTC is never assumed.

``croniter`` (MIT) supplies the cron arithmetic; the interval form is parsed by
Nova because ``SCHEDULE EVERY(INTERVAL …)`` is a StarRocks-shaped expression,
not a cron expression.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

_CRON_FIELDS = 5

_INTERVAL_UNITS: dict[str, str] = {
    "second": "seconds",
    "seconds": "seconds",
    "minute": "minutes",
    "minutes": "minutes",
    "hour": "hours",
    "hours": "hours",
    "day": "days",
    "days": "days",
}

_INTERVAL_RE = re.compile(
    r"^\s*every\s*\(\s*interval\s+(\d+)\s+"
    r"(second|seconds|minute|minutes|hour|hours|day|days)\s*\)\s*$"
    r"|^\s*(?:every\s+)?(\d+)\s*"
    r"(second|seconds|minute|minutes|hour|hours|day|days)\s*$",
    re.IGNORECASE,
)


class ScheduleError(ValueError):
    """Raised when a schedule expression or timezone is unusable."""


@dataclass(frozen=True)
class Interval:
    """A parsed ``EVERY(INTERVAL n UNIT)`` cadence."""

    amount: int
    unit: str

    def resolve(self, reference: datetime) -> timedelta:
        if self.amount <= 0:
            raise ScheduleError(f"interval amount must be positive, got {self.amount}")
        return timedelta(**{_INTERVAL_UNITS[self.unit]: self.amount})


def resolve_timezone(name: str) -> ZoneInfo:
    """Return the IANA ``ZoneInfo`` for ``name`` or raise ``ScheduleError``.

    An explicit timezone is mandatory: StarRocks interprets ``START`` literals
    in the session timezone, so silently defaulting to UTC would fire tasks at
    the wrong wall-clock moment.
    """
    if not name or not name.strip():
        raise ScheduleError("task timezone is required and must be an IANA name")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError(f"unknown IANA timezone: {name!r}") from exc


_OFFSET_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


def _fixed_offset(name: str) -> timezone:
    """Parse a MySQL-style fixed offset (``+07:00``) into a ``timezone``.

    StarRocks reports ``@@time_zone`` as an IANA name when the session was set to
    one, but as a numeric offset when it was set with ``SET time_zone = '+07:00'``.
    Both are valid engine reports and both must be comparable to a configured
    override, so the offset form is parsed rather than rejected.
    """
    match = _OFFSET_RE.match(name.strip())
    if match is None:
        raise ScheduleError(f"unrecognised engine timezone: {name!r}")
    sign = 1 if match.group(1) == "+" else -1
    hours, minutes = int(match.group(2)), int(match.group(3))
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _parse_zone(value: str) -> ZoneInfo | timezone:
    """Parse an IANA name or a MySQL-style fixed offset; raise ``ScheduleError``."""
    try:
        return resolve_timezone(value)
    except ScheduleError:
        return _fixed_offset(value)


def engine_timezone_matches(configured: str, engine_reported: str) -> bool:
    """Whether ``configured`` and the engine's ``@@time_zone`` mean the same zone.

    The comparison is done on the instant in question, not on the spelling: the
    engine may report ``+07:00`` for a session the override names ``Asia/Jakarta``
    (and vice versa), and refusing to start over a synonym would be a false alarm.
    A genuinely different zone — the 7-hour bug this guards against — still fails.
    """
    try:
        configured_zone = _parse_zone(configured)
        engine_zone = _parse_zone(engine_reported)
    except ScheduleError:
        return False
    reference = datetime(2026, 1, 1, 12, 0)
    return (
        reference.replace(tzinfo=configured_zone).utcoffset()
        == reference.replace(tzinfo=engine_zone).utcoffset()
    )


def parse_cron(expression: str) -> croniter:
    """Parse a 5-field cron expression. Raises ``ScheduleError`` when invalid."""
    if not expression or not expression.strip():
        raise ScheduleError("cron expression is required")
    stripped = expression.strip()
    if len(stripped.split()) != _CRON_FIELDS:
        raise ScheduleError(
            f"cron expression must have exactly {_CRON_FIELDS} fields, "
            f"got {len(stripped.split())}: {expression!r}"
        )
    if not croniter.is_valid(stripped):
        raise ScheduleError(f"invalid cron expression: {expression!r}")
    return croniter(stripped)


def parse_interval(expression: str) -> Interval:
    """Parse ``EVERY(INTERVAL n UNIT)`` / ``n UNIT``. Raises ``ScheduleError``."""
    if not expression or not expression.strip():
        raise ScheduleError("interval expression is required")
    match = _INTERVAL_RE.match(expression)
    if match is None:
        raise ScheduleError(
            "interval expression must look like 'EVERY(INTERVAL 5 MINUTE)' "
            f"or '5 minute', got {expression!r}"
        )
    amount = int(match.group(1) or match.group(3))
    unit = match.group(2) or match.group(4)
    if amount <= 0:
        raise ScheduleError(f"interval amount must be positive, got {amount}")
    return Interval(amount=amount, unit=unit.lower())


def next_fire(
    schedule_kind: str,
    schedule_expr: str,
    timezone: str,
    after: datetime,
    *,
    on_missing: str = "skip",
) -> datetime | None:
    """Compute the first fire strictly after ``after``, in the task's timezone.

    ``after`` must be timezone-aware. The return value is a UTC instant (the
    canonical form for comparisons and persistence). ``None`` means the task has
    no future occurrence — a ``manual`` schedule, or an interval whose previous
    fire is unknowable.
    """
    if after.tzinfo is None:
        raise ScheduleError("reference time must be timezone-aware")

    kind = (schedule_kind or "").lower()
    if kind == "manual":
        return None

    zone = resolve_timezone(timezone)
    local_reference = after.astimezone(zone)

    if kind == "cron":
        iterator = parse_cron(schedule_expr)
        return iterator.get_next(datetime, start_time=local_reference).astimezone(
            ZoneInfo("UTC")
        )

    if kind == "interval":
        interval = parse_interval(schedule_expr)
        return (local_reference + interval.resolve(local_reference)).astimezone(
            ZoneInfo("UTC")
        )

    raise ScheduleError(f"unsupported schedule kind: {schedule_kind!r}")


def is_due(
    schedule_kind: str,
    schedule_expr: str,
    timezone: str,
    last_fired_at: datetime | None,
    now: datetime,
) -> bool:
    """Return True when a task should fire at or before ``now``.

    A task with no recorded fire is never due until its first computed fire
    arrives — a newly created cron task must not fire immediately just because
    Nova has no memory of it. ``last_fired_at`` is the last *scheduled* fire
    instant (not the last run's start), so a late tick still fires once per due
    occurrence rather than once per tick.
    """
    if last_fired_at is None:
        return False

    next_at = next_fire(schedule_kind, schedule_expr, timezone, last_fired_at)
    if next_at is None:
        return False
    return next_at <= now


def latest_occurrence(
    schedule_kind: str,
    schedule_expr: str,
    timezone: str,
    anchor: datetime,
    now: datetime,
) -> datetime | None:
    """The most recent scheduled fire at or before ``now``, at or after ``anchor``.

    This is the scheduler's due-time watermark and it is derived, not stored: a
    task's ``created_at`` is the anchor, so a newly created task never fires for
    occurrences that predate it, and a tick that arrives late fires exactly once
    per elapsed occurrence instead of once per tick. ``None`` means there is no
    occurrence to fire — ``manual``, a future first fire, or an unusable
    expression.

    Both ``anchor`` and ``now`` must be timezone-aware. The result is a UTC
    instant, which is also the timestamp used in the idempotency key.
    """
    if anchor.tzinfo is None or now.tzinfo is None:
        raise ScheduleError("anchor and now must be timezone-aware")

    kind = (schedule_kind or "").lower()
    if kind == "manual":
        return None

    zone = resolve_timezone(timezone)
    local_now = now.astimezone(zone)
    local_anchor = anchor.astimezone(zone)
    if local_now < local_anchor:
        return None

    if kind == "cron":
        iterator = parse_cron(schedule_expr)
        # A fire exactly at ``now`` is due at that instant. croniter.get_prev is
        # strictly-before, so on the fire instant it would skip this occurrence
        # and fall back to the previous one; interval has no such gap. Floating
        # to the minute first makes this match the cron grid (cron has no second
        # field), and croniter.match needs a naive local datetime.
        on_grid = local_now.replace(second=0, microsecond=0)
        if iterator.match(schedule_expr, on_grid.replace(tzinfo=None)):
            candidate = on_grid
        else:
            candidate = iterator.get_prev(datetime, start_time=local_now)
        if candidate is None or candidate < local_anchor:
            return None
        return candidate.astimezone(ZoneInfo("UTC"))

    if kind == "interval":
        interval = parse_interval(schedule_expr)
        step = interval.resolve(local_anchor)
        if step <= timedelta(0):
            return None
        # Occurrences are anchor + k*step for k >= 1: the first fire is one
        # interval after creation, never at the creation instant itself.
        elapsed = local_now - local_anchor
        occurrences = elapsed // step
        if occurrences < 1:
            return None
        candidate = local_anchor + occurrences * step
        if candidate > local_now:
            candidate -= step
        if candidate < local_anchor:
            return None
        return candidate.astimezone(ZoneInfo("UTC"))

    raise ScheduleError(f"unsupported schedule kind: {schedule_kind!r}")
