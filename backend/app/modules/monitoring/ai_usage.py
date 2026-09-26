"""Read-only AI accounting from persisted turns, worker runs, and SQL audit rows."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from antlr4 import CommonTokenStream

from app.core.database import configured_timezone, db
from app.modules.query.dialect.parser import CaseInsensitiveInputStream, _visible_tokens
from app.sql_dialect.grammar import StarRocksLexer

logger = logging.getLogger(__name__)
SOURCE_LIMIT = 10_000
FUNCTIONS = frozenset({
    "AI_QUERY", "AI_COMPLETE", "AI_SENTIMENT", "AI_CLASSIFY", "AI_SUMMARIZE",
    "AI_EXTRACT", "AI_TRANSLATE", "AI_FILTER", "AI_EMBED", "AI_EMBEDDING",
})


def sql_actions(sql: str) -> list[str]:
    lexer = StarRocksLexer(CaseInsensitiveInputStream(sql))
    lexer.removeErrorListeners()
    stream = CommonTokenStream(lexer)
    stream.fill()
    tokens = _visible_tokens(stream)
    if not tokens or tokens[0].text.upper() not in {
        "SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "CREATE",
    }:
        return []
    words = [t.text.upper() for t in tokens]
    # Function definitions and EXPLAIN do not constitute executions.
    if words[0] == "CREATE" and "TABLE" not in words[:5]:
        return []
    return sorted({
        token.text.strip("`").upper()
        for token, following in zip(tokens, tokens[1:], strict=False)
        if token.text.strip("`").upper() in FUNCTIONS and following.text == "("
    })


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _event(
    event_id: str, at: Any, user: str, source: str, action: str,
    model: str | None, prompt: Any, completion: Any, total: Any,
    status: str, duration_ms: Any = None,
) -> dict:
    at = at if isinstance(at, datetime) else datetime.fromisoformat(str(at))
    at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
    prompt, completion, total = _count(prompt), _count(completion), _count(total)
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return {
        "id": event_id, "at": at.isoformat(), "user_name": user,
        "source": source, "action": action, "model": model or None,
        "input_tokens": prompt, "output_tokens": completion, "total_tokens": total,
        "status": status, "duration_ms": _count(duration_ms),
    }


def _stats(events: list[dict]) -> dict:
    reported = [e for e in events if e["total_tokens"] is not None]
    durations = [e["duration_ms"] for e in events if e["duration_ms"] is not None]
    total = sum(e["total_tokens"] for e in reported)
    return {
        "activities": len(events), "reported_activities": len(reported),
        "unreported_activities": len(events) - len(reported),
        "input_tokens": sum(e["input_tokens"] or 0 for e in events),
        "output_tokens": sum(e["output_tokens"] or 0 for e in events),
        "total_tokens": total,
        "unattributed_tokens": sum(e["total_tokens"] or 0 for e in events if not e["model"]),
        "users": len({e["user_name"] for e in events}),
        "models": len({e["model"] for e in events if e["model"]}),
        "failed_activities": sum(e["status"] in {"error", "failed"} for e in events),
        "avg_tokens": round(total / len(reported), 1) if reported else None,
        "coverage_percent": round(len(reported) / len(events) * 100, 1) if events else None,
        "avg_duration_ms": round(sum(durations) / len(durations)) if durations else None,
    }


def _groups(events: list[dict], key: str) -> list[dict]:
    groups: dict[str | None, list[dict]] = defaultdict(list)
    for event in events:
        groups[event[key]].append(event)
    return sorted(
        ({"name": name, **_stats(items)} for name, items in groups.items()),
        key=lambda g: (-g["total_tokens"], -g["activities"], g["name"] or ""),
    )


class AIUsageUnavailable(Exception):
    pass


class AIUsageService:
    async def _messages(self, start: datetime, end: datetime) -> tuple[list[dict], bool]:
        result = await db.execute_system(
            "SELECT m.message_id, m.created_at, m.user_name, m.agent_id, m.model_name, "
            "m.prompt_tokens, m.completion_tokens, m.total_tokens, t.workspace_file_id "
            "FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES m "
            "LEFT JOIN NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS t "
            "ON t.thread_id = m.thread_id AND t.user_name = m.user_name "
            "WHERE m.role = 'assistant' AND (m.agent_id IS NULL "
            "OR m.agent_id NOT IN ('__smart__', '__auto__')) "
            "AND m.created_at >= %s AND m.created_at < %s "
            "ORDER BY m.created_at DESC, m.message_id DESC LIMIT %s",
            [start.replace(tzinfo=None), end.replace(tzinfo=None), SOURCE_LIMIT + 1],
        )
        rows = result["rows"]
        events = [
            _event(
                f"message:{r[0]}", r[1], r[2], "assistant",
                "Workspace assistant" if r[8] else "Nova Studio / Assistant",
                r[4], r[5], r[6], r[7], "recorded",
            ) for r in rows[:SOURCE_LIMIT]
        ]
        return events, len(rows) > SOURCE_LIMIT

    async def _runs(self, start: datetime, end: datetime) -> tuple[list[dict], bool]:
        result = await db.execute_system(
            "SELECT run_id, started_at, owner_name, depth, status, prompt_tokens, "
            "completion_tokens FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
            "WHERE (root_run_id IS NOT NULL OR agent_id IN ('__smart__', '__auto__')) "
            "AND started_at >= %s AND started_at < %s "
            "ORDER BY started_at DESC, run_id DESC LIMIT %s",
            [start.replace(tzinfo=None), end.replace(tzinfo=None), SOURCE_LIMIT + 1],
        )
        rows = result["rows"]
        events = []
        for r in rows[:SOURCE_LIMIT]:
            # Run counters start at zero even when the provider omits usage.
            known = (_count(r[5]) or 0) + (_count(r[6]) or 0) > 0
            events.append(_event(
                f"run:{r[0]}", r[1], r[2], "smart",
                "Smart specialist" if r[3] else "Smart coordinator",
                None, r[5] if known else None, r[6] if known else None, None, r[4],
            ))
        return events, len(rows) > SOURCE_LIMIT

    async def _functions(self, start: datetime, end: datetime) -> tuple[list[dict], bool]:
        zone = configured_timezone()
        result = await db.execute_system(
            "SELECT log_id, CONVERT_TZ(event_time, %s, '+00:00'), "
            "user_name, sql_text, status, duration_ms "
            "FROM NOVA_SYSTEM.AUDIT_LOG WHERE event_type = 'query' "
            "AND event_time >= CONVERT_TZ(%s, '+00:00', %s) "
            "AND event_time < CONVERT_TZ(%s, '+00:00', %s) AND UPPER(sql_text) LIKE %s "
            "ORDER BY event_time DESC, log_id DESC LIMIT %s",
            [zone, start.replace(tzinfo=None), zone, end.replace(tzinfo=None), zone,
             "%AI%", SOURCE_LIMIT + 1],
        )
        rows = result["rows"]
        events = []
        for r in rows[:SOURCE_LIMIT]:
            actions = sql_actions(r[3] or "")
            if actions:
                events.append(_event(
                    f"query:{r[0]}", r[1], r[2], "functions", " + ".join(actions),
                    None, None, None, None, str(r[4]).lower(), r[5],
                ))
        return events, len(rows) > SOURCE_LIMIT

    async def dashboard(
        self, *, days: int = 7, source: str | None = None, model: str | None = None,
        user_name: str | None = None, offset: int = 0, limit: int = 25,
        now: datetime | None = None,
    ) -> dict:
        end = now or datetime.now(UTC)
        start = end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        previous_start, previous_end = start - timedelta(days=days), end - timedelta(days=days)
        loaders = {"assistant": self._messages, "smart": self._runs, "functions": self._functions}

        async def load(name: str, begin: datetime, finish: datetime) -> tuple[list[dict], bool]:
            return await asyncio.wait_for(loaders[name](begin, finish), timeout=15)

        keys = [(name, period) for name in loaders for period in ("current", "previous")]
        results = await asyncio.gather(*(
            load(name, start if period == "current" else previous_start,
                 end if period == "current" else previous_end)
            for name, period in keys
        ), return_exceptions=True)
        periods: dict[str, list[dict]] = {"current": [], "previous": []}
        coverage = []
        for (name, period), result in zip(keys, results, strict=True):
            failed = isinstance(result, BaseException)
            events, truncated = ([], False) if failed else result
            coverage.append({
                "source": name, "period": period,
                "status": "unavailable" if failed else "limited" if truncated else "available",
                "activities": len(events),
            })
            periods[period].extend(events)
            if failed:
                logger.warning("AI monitoring source unavailable: %s (%s)", name, period)
        if all(c["status"] == "unavailable" for c in coverage if c["period"] == "current"):
            raise AIUsageUnavailable

        def matches(event: dict) -> bool:
            return (
                (not source or event["source"] == source)
                and (not model or event["model"] == model)
                and (not user_name or event["user_name"] == user_name)
            )

        current = sorted(
            (e for e in periods["current"] if matches(e)),
            key=lambda e: (e["at"], e["id"]), reverse=True,
        )
        previous = [e for e in periods["previous"] if matches(e)]
        complete = all(c["status"] == "available" for c in coverage)
        stats, previous_stats = _stats(current), _stats(previous)
        daily = []
        for day in range(days):
            date = (start + timedelta(days=day)).date().isoformat()
            daily.append({"date": date, **_stats([e for e in current if e["at"][:10] == date])})
        prior = previous_stats["total_tokens"]
        return {
            "start": start.isoformat(), "end": end.isoformat(), "days": days,
            "previous_start": previous_start.isoformat(), "previous_end": previous_end.isoformat(),
            "summary": stats, "previous_summary": previous_stats if complete else None,
            "token_change_percent": round((stats["total_tokens"] - prior) / prior * 100, 1)
            if complete and prior else None,
            "daily": daily, "actions": _groups(current, "action"),
            "models": _groups(current, "model"), "users": _groups(current, "user_name"),
            "sources": _groups(current, "source"), "coverage": coverage,
            "partial": not complete, "source_limit": SOURCE_LIMIT,
            "filters": {
                "models": sorted({e["model"] for e in periods["current"] if e["model"]}),
                "users": sorted({e["user_name"] for e in periods["current"]}),
            },
            "activities": current[offset:offset + limit], "total": len(current),
            "offset": offset, "limit": limit,
        }


ai_usage_service = AIUsageService()
