"""Read-only AI accounting from persisted turns, worker runs, and SQL audit rows."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from antlr4 import CommonTokenStream

from app.common.metadata_actors import metadata_scope
from app.modules.access_control.security_context import SecurityContext
from app.modules.llm_functions.metadata_repository import LLMUsageMetadataRepository
from app.modules.monitoring.repository import MonitoringRepository
from app.modules.query.dialect.parser import CaseInsensitiveInputStream, _visible_tokens
from app.sql_dialect.grammar import StarRocksLexer

logger = logging.getLogger(__name__)
SOURCE_LIMIT = 10_000
FUNCTIONS = frozenset(
    {
        "AI_QUERY",
        "AI_COMPLETE",
        "AI_SENTIMENT",
        "AI_CLASSIFY",
        "AI_SUMMARIZE",
        "AI_EXTRACT",
        "AI_TRANSLATE",
        "AI_FILTER",
        "AI_EMBED",
        "AI_EMBEDDING",
    }
)
_FUNCTION_PATTERNS = tuple(f"%{name}%" for name in sorted(FUNCTIONS))


def sql_actions(sql: str) -> list[str]:
    lexer = StarRocksLexer(CaseInsensitiveInputStream(sql))
    lexer.removeErrorListeners()
    stream = CommonTokenStream(lexer)
    stream.fill()
    tokens = _visible_tokens(stream)
    if not tokens or tokens[0].text.upper() not in {
        "SELECT",
        "WITH",
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
    }:
        return []
    words = [t.text.upper() for t in tokens]
    # Function definitions and EXPLAIN do not constitute executions.
    if words[0] == "CREATE" and "TABLE" not in words[:5]:
        return []
    return sorted(
        {
            token.text.strip("`").upper()
            for token, following in zip(tokens, tokens[1:], strict=False)
            if token.text.strip("`").upper() in FUNCTIONS and following.text == "("
        }
    )


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _event(
    event_id: str,
    at: Any,
    user: str,
    source: str,
    action: str,
    model: str | None,
    prompt: Any,
    completion: Any,
    total: Any,
    status: str,
    duration_ms: Any = None,
) -> dict:
    at = at if isinstance(at, datetime) else datetime.fromisoformat(str(at))
    at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
    prompt, completion, total = _count(prompt), _count(completion), _count(total)
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return {
        "id": event_id,
        "at": at.isoformat(),
        "user_name": user,
        "source": source,
        "action": action,
        "model": model or None,
        "input_tokens": prompt,
        "output_tokens": completion,
        "total_tokens": total,
        "status": status,
        "duration_ms": _count(duration_ms),
    }


def _stats(events: list[dict]) -> dict:
    reported = [e for e in events if e["total_tokens"] is not None]
    durations = [e["duration_ms"] for e in events if e["duration_ms"] is not None]
    total = sum(e["total_tokens"] for e in reported)
    return {
        "activities": len(events),
        "reported_activities": len(reported),
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
    async def _messages(
        self,
        start: datetime,
        end: datetime,
        *,
        actor: SecurityContext | None = None,
    ) -> tuple[list[dict], bool]:
        if actor is None:
            raise AIUsageUnavailable
        rows = await MonitoringRepository().assistant_usage(
            actor, start, end, limit=SOURCE_LIMIT + 1
        )
        events = [
            _event(
                f"message:{row['message_id']}",
                row["created_at"],
                row["user_name"],
                "assistant",
                "Workspace assistant" if row["workspace_file_id"] else "Nova Studio / Assistant",
                row["model_name"],
                row["prompt_tokens"],
                row["completion_tokens"],
                row["total_tokens"],
                "recorded",
            )
            for row in rows[:SOURCE_LIMIT]
        ]
        return events, len(rows) > SOURCE_LIMIT

    async def _functions(
        self,
        start: datetime,
        end: datetime,
        *,
        actor: SecurityContext | None = None,
    ) -> tuple[list[dict], bool]:
        if actor is None:
            raise AIUsageUnavailable
        rows = await MonitoringRepository().ai_query_usage(
            actor,
            start,
            end,
            patterns=_FUNCTION_PATTERNS,
            limit=SOURCE_LIMIT + 1,
        )
        events = []
        for row in rows[:SOURCE_LIMIT]:
            actions = sql_actions(row["sql_text"] or "")
            if actions:
                events.append(
                    _event(
                        f"query:{row['log_id']}",
                        row["event_time"],
                        row["user_name"],
                        "functions",
                        " + ".join(actions),
                        None,
                        None,
                        None,
                        None,
                        str(row["status"]).lower(),
                        row["duration_ms"],
                    )
                )
        return events, len(rows) > SOURCE_LIMIT

    async def _function_tokens(
        self,
        start: datetime,
        end: datetime,
        *,
        actor: SecurityContext | None = None,
    ) -> tuple[list[dict], bool]:
        """Measured ``AI_*`` usage from the LLM gateway, one event per flushed total.

        The engine does not say who ran the SQL, so these carry no user; the
        ``functions`` source still lists which statements called a function.
        Reading it requires the administrator's own metadata actor.
        """
        if actor is None:
            raise AIUsageUnavailable
        async with metadata_scope(actor, read_only=True) as connection:
            rows = await LLMUsageMetadataRepository(connection, actor).function_tokens(
                start,
                end,
                limit=SOURCE_LIMIT + 1,
            )
        events = [
            _event(
                f"function-usage:{r['usage_id']}",
                r["recorded_at"],
                "",
                "function_tokens",
                r["function_name"],
                r["model_name"],
                r["prompt_tokens"],
                r["completion_tokens"],
                r["total_tokens"],
                str(r["status"]).lower(),
                r["duration_ms"],
            )
            for r in rows[:SOURCE_LIMIT]
        ]
        return events, len(rows) > SOURCE_LIMIT

    async def dashboard(
        self,
        *,
        days: int = 7,
        source: str | None = None,
        model: str | None = None,
        user_name: str | None = None,
        offset: int = 0,
        limit: int = 25,
        now: datetime | None = None,
        actor: SecurityContext | None = None,
    ) -> dict:
        end = now or datetime.now(UTC)
        start = end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        previous_start, previous_end = start - timedelta(days=days), end - timedelta(days=days)
        loaders: dict[str, Callable[[datetime, datetime], Awaitable[tuple[list[dict], bool]]]] = {
            "assistant": partial(self._messages, actor=actor),
            "functions": partial(self._functions, actor=actor),
            "function_tokens": partial(self._function_tokens, actor=actor),
        }

        async def load(name: str, begin: datetime, finish: datetime) -> tuple[list[dict], bool]:
            return await asyncio.wait_for(loaders[name](begin, finish), timeout=15)

        keys = [(name, period) for name in loaders for period in ("current", "previous")]
        results = await asyncio.gather(
            *(
                load(
                    name,
                    start if period == "current" else previous_start,
                    end if period == "current" else previous_end,
                )
                for name, period in keys
            ),
            return_exceptions=True,
        )
        periods: dict[str, list[dict]] = {"current": [], "previous": []}
        coverage = []
        for (name, period), result in zip(keys, results, strict=True):
            failed = isinstance(result, BaseException)
            events, truncated = ([], False) if isinstance(result, BaseException) else result
            coverage.append(
                {
                    "source": name,
                    "period": period,
                    "status": "unavailable" if failed else "limited" if truncated else "available",
                    "activities": len(events),
                }
            )
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
            key=lambda e: (e["at"], e["id"]),
            reverse=True,
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
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": days,
            "previous_start": previous_start.isoformat(),
            "previous_end": previous_end.isoformat(),
            "summary": stats,
            "previous_summary": previous_stats if complete else None,
            "token_change_percent": round((stats["total_tokens"] - prior) / prior * 100, 1)
            if complete and prior
            else None,
            "daily": daily,
            "actions": _groups(current, "action"),
            "models": _groups(current, "model"),
            "users": _groups(current, "user_name"),
            "sources": _groups(current, "source"),
            "coverage": coverage,
            "partial": not complete,
            "source_limit": SOURCE_LIMIT,
            "filters": {
                "models": sorted({e["model"] for e in periods["current"] if e["model"]}),
                "users": sorted({e["user_name"] for e in periods["current"]}),
            },
            "activities": current[offset : offset + limit],
            "total": len(current),
            "offset": offset,
            "limit": limit,
        }


ai_usage_service = AIUsageService()
