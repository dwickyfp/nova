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
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadDateError, croniter

_CRON_FIELDS = 5

_OFFSET_RE = re.compile(r"^([+-])(\d{1,2}):(\d{2})$")
_MAX_OFFSET = timedelta(hours=23, minutes=59)

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
    r"(second|seconds|minute|minutes|hour|hours|day|days)\s*$"
    r"|^\s*interval\s+(\d+)\s+"
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
        try:
            return timedelta(**{_INTERVAL_UNITS[self.unit]: self.amount})
        except (OverflowError, ValueError) as exc:
            raise ScheduleError("interval amount is too large") from exc


def _parse_offset(name: str) -> timezone | None:
    """Return the fixed-offset ``timezone`` for ``[+-]HH:MM``, else ``None``.

    StarRocks accepts offset session zones (``SET time_zone='+07:00'``), so
    ``@@time_zone`` may report a numeric offset rather than an IANA key. A fixed
    ``timezone`` is the right representation for those: it carries no DST rules,
    which matches what the engine itself does with an offset.
    """
    match = _OFFSET_RE.match(name)
    if match is None:
        return None
    sign = 1 if match.group(1) == "+" else -1
    hours = int(match.group(2))
    minutes = int(match.group(3))
    if minutes > 59:
        return None
    value = timedelta(hours=hours, minutes=minutes)
    if value > _MAX_OFFSET:
        return None
    return timezone(sign * value)


def resolve_timezone(name: str) -> ZoneInfo | timezone:
    """Return a zone for ``name`` or raise ``ScheduleError``.

    Accepts an IANA name (``Asia/Jakarta``) primarily, and also the numeric
    offset form StarRocks reports for offset session zones (``+07:00``). An
    explicit timezone is mandatory: StarRocks interprets ``START`` literals in
    the session timezone, so silently defaulting to UTC would fire tasks at the
    wrong wall-clock moment.
    """
    if not name or not name.strip():
        raise ScheduleError("task timezone is required and must be an IANA name")
    stripped = name.strip()
    offset = _parse_offset(stripped)
    if offset is not None:
        return offset
    try:
        return ZoneInfo(stripped)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError(f"unknown IANA timezone: {name!r}") from exc


def engine_timezone_matches(configured: str, engine_reported: str) -> bool:
    """Whether ``configured`` and the engine's ``@@time_zone`` mean the same zone.

    ``resolve_timezone`` already accepts both an IANA key and the numeric offset
    StarRocks reports for offset session zones, so the comparison is done on the
    instant in question rather than on the spelling: the engine may report
    ``+07:00`` for a session the override names ``Asia/Jakarta`` (and vice versa),
    and refusing to start over a synonym would be a false alarm. A genuinely
    different zone — the 7-hour bug this guards against — still fails.
    """
    try:
        configured_zone = resolve_timezone(configured)
        engine_zone = resolve_timezone(engine_reported)
    except ScheduleError:
        return False
    reference = datetime(2026, 1, 1, 12, 0)
    return (
        reference.replace(tzinfo=configured_zone).utcoffset()
        == reference.replace(tzinfo=engine_zone).utcoffset()
    )


@lru_cache(maxsize=4096)
def _validate_cron_expression(stripped: str) -> None:
    if not croniter.is_valid(stripped):
        raise ScheduleError(f"invalid cron expression: {stripped!r}")
    iterator = croniter(stripped)
    try:
        iterator.get_next(datetime, start_time=datetime(2024, 1, 1))
    except (CroniterBadDateError, OverflowError) as exc:
        raise ScheduleError(f"cron expression has no valid fire date: {stripped!r}") from exc


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
    _validate_cron_expression(stripped)
    return croniter(stripped)


def parse_interval(expression: str) -> Interval:
    """Parse a stored interval clause or a user-facing interval expression."""
    if not expression or not expression.strip():
        raise ScheduleError("interval expression is required")
    match = _INTERVAL_RE.match(expression)
    if match is None:
        raise ScheduleError(
            "interval expression must look like 'EVERY(INTERVAL 5 MINUTE)', "
            f"'INTERVAL 5 MINUTE', or '5 minute', got {expression!r}"
        )
    amount = int(match.group(1) or match.group(3) or match.group(5))
    unit = match.group(2) or match.group(4) or match.group(6)
    if amount <= 0:
        raise ScheduleError(f"interval amount must be positive, got {amount}")
    interval = Interval(amount=amount, unit=unit.lower())
    interval.resolve(datetime(2024, 1, 1))
    return interval


def _local_instants(local: datetime, zone: ZoneInfo | timezone) -> list[datetime]:
    """Resolve a cron wall time, skipping gaps and retaining both repeated hours."""
    instants: set[datetime] = set()
    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        instant = aware.astimezone(ZoneInfo("UTC"))
        if instant.astimezone(zone).replace(tzinfo=None) == local:
            instants.add(instant)
    return sorted(instants)


def _near_clock_change(instant: datetime, zone: ZoneInfo | timezone) -> bool:
    """A repeated local hour needs a short wall-time lookahead."""
    before = (instant - timedelta(hours=3)).astimezone(zone).utcoffset()
    after = (instant + timedelta(hours=3)).astimezone(zone).utcoffset()
    return before != after


def _cron_fires_before(
    expression: str,
    zone: ZoneInfo | timezone,
    anchor: datetime,
    now: datetime,
    limit: int,
) -> list[datetime]:
    """Return the latest distinct UTC fires strictly after anchor, through now."""
    if now <= anchor or limit <= 0:
        return []
    local_now = now.astimezone(zone).replace(tzinfo=None)
    local_anchor = anchor.astimezone(zone).replace(tzinfo=None)
    near_change = _near_clock_change(now, zone)
    # In the second copy of a repeated hour, an earlier UTC fire can have a
    # later wall-clock label. Inspect the whole nearby wall window first.
    seed = local_now + (timedelta(hours=3) if near_change else timedelta(microseconds=1))
    iterator = croniter(expression, seed)
    fires: set[datetime] = set()
    for _ in range(10000):
        try:
            local = iterator.get_prev(datetime)
        except (CroniterBadDateError, OverflowError):
            return sorted(fires)[-limit:]
        instants = _local_instants(local, zone)
        fires.update(instant for instant in instants if anchor < instant <= now)
        if near_change:
            if local >= local_now - timedelta(hours=3):
                continue
            near_change = False
        if len(fires) >= limit:
            return sorted(fires)[-limit:]
        # Outside a clock change, wall-time and UTC order agree. A small
        # offset check avoids discarding an earlier repeated-hour fire.
        if (
            instants
            and max(instants) <= anchor
            and not _near_clock_change(anchor, zone)
            and not _near_clock_change(max(instants), zone)
        ):
            return sorted(fires)[-limit:]
        if local < local_anchor - timedelta(days=1):
            return sorted(fires)[-limit:]
    raise ScheduleError("cron schedule exceeded bounded occurrence search")


def _next_cron_fire(
    expression: str, zone: ZoneInfo | timezone, after: datetime
) -> datetime:
    local_after = after.astimezone(zone).replace(tzinfo=None)
    near_change = _near_clock_change(after, zone)
    seed = local_after - (timedelta(hours=3) if near_change else timedelta(0))
    iterator = croniter(expression, seed)
    candidates: list[datetime] = []
    for _ in range(10000):
        try:
            local = iterator.get_next(datetime)
        except (CroniterBadDateError, OverflowError) as exc:
            raise ScheduleError("cron schedule has no future fire date") from exc
        candidates.extend(instant for instant in _local_instants(local, zone) if instant > after)
        if near_change:
            if local <= local_after + timedelta(hours=3):
                continue
            near_change = False
        if candidates:
            return min(candidates)
    raise ScheduleError("cron schedule exceeded bounded next-fire search")


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
        parse_cron(schedule_expr)
        return _next_cron_fire(schedule_expr, zone, after)

    if kind == "interval":
        interval = parse_interval(schedule_expr)
        try:
            return (local_reference + interval.resolve(local_reference)).astimezone(
                ZoneInfo("UTC")
            )
        except OverflowError as exc:
            raise ScheduleError("interval next-fire exceeds datetime range") from exc

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
        parse_cron(schedule_expr)
        fires = _cron_fires_before(schedule_expr, zone, anchor, now, 1)
        return fires[-1] if fires else None

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


#: The most occurrences one catch-up (backfill) may emit for one root.
#: Bounded so a task whose scheduler was down for a long time cannot enqueue an
#: unbounded backlog in a single tick. The **newest** ``MAX_CATCH_UP_OCCURRENCES``
#: missed fires are emitted and older ones are dropped, so the task converges to
#: the present rather than replaying ancient history. Five covers a short outage
#: (a few missed fires) without a burst of runs for a task anchored long ago.
MAX_CATCH_UP_OCCURRENCES = 5


def due_occurrences(
    schedule_kind: str,
    schedule_expr: str,
    timezone: str,
    anchor: datetime,
    now: datetime,
    *,
    limit: int = MAX_CATCH_UP_OCCURRENCES,
) -> list[datetime]:
    """Every scheduled fire at or before ``now``, at or after ``anchor``.

    The scheduler's due-time watermark is derived, not stored (a task's
    ``created_at`` is the anchor), so a tick that arrives late must be able to
    fire the occurrences it missed — not only the most recent one. This returns
    the missed occurrences in ascending order, capped at ``limit``; when the
    backlog exceeds the cap the **newest** ``limit`` occurrences are returned
    (the oldest are dropped) so the task catches up to the present rather than
    replaying ancient history.

    ``latest_occurrence`` is exactly the last element of this list, kept as a
    separate function because it is the common, cheap case. Both ``anchor`` and
    ``now`` must be timezone-aware; results are UTC instants.
    """
    if anchor.tzinfo is None or now.tzinfo is None:
        raise ScheduleError("anchor and now must be timezone-aware")

    kind = (schedule_kind or "").lower()
    if kind == "manual":
        return []

    if limit <= 0:
        return []

    zone = resolve_timezone(timezone)
    local_now = now.astimezone(zone)
    local_anchor = anchor.astimezone(zone)
    if local_now < local_anchor:
        return []

    if kind == "cron":
        parse_cron(schedule_expr)
        return _cron_fires_before(schedule_expr, zone, anchor, now, limit)

    if kind == "interval":
        interval = parse_interval(schedule_expr)
        step = interval.resolve(local_anchor)
        if step <= timedelta(0):
            return []
        elapsed = local_now - local_anchor
        count = elapsed // step
        if count < 1:
            return []
        # Occurrences are anchor + k*step for k in [1, count]. Keep the newest
        # ``limit`` of them.
        first_k = max(1, int(count) - limit + 1)
        return [
            (local_anchor + k * step).astimezone(ZoneInfo("UTC"))
            for k in range(first_k, int(count) + 1)
        ]

    raise ScheduleError(f"unsupported schedule kind: {schedule_kind!r}")
