"""Bounded counters for one assistant attempt, without request content or identities."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx

from app.common.ssrf_guard import BlockedEndpointError
from app.modules.assistant.context import estimate_messages_tokens

MAX_COUNT = 1_000_000_000
_REASONS = frozenset(
    {
        "custom_planner_unmeasured",
        "custom_transport_unmeasured",
        "failed_dispatch_unmeasured",
        "nested_tool_requests_unmeasured",
        "decision_requests_unmeasured",
        "background_translation",
        "background_summary",
        "prior_attempt_unmeasured",
        "blocked_transport_unmeasured",
        "counter_limit_exceeded",
    }
)
_current: ContextVar[AttemptMeasurements | None] = ContextVar(
    "assistant_measurements",
    default=None,
)
_Owner = TypeVar("_Owner", bound=Callable)
_metadata_executor: ContextVar[asyncio.Task | None] = ContextVar(
    "assistant_metadata_executor",
    default=None,
)


@dataclass
class AttemptMeasurements:
    collaborative: bool = False
    resumed: bool = False
    tool_dispatches: int = 0
    provider_invocations: int = 0
    provider_dispatches: int = 0
    metadata_reads: int = 0
    reported_total_tokens: int = 0
    reported_peak_prompt_tokens: int = 0
    estimated_peak_context_tokens: int = 0
    missing_usage: bool = False
    missing_prompt_usage: bool = False
    provider_unavailable: set[str] = field(default_factory=set)
    metadata_failed: bool = False
    metadata_direct_unobserved: bool = False
    pending_usage: int = 0
    overflowed: set[str] = field(default_factory=set)
    active: bool = True

    def increment(self, name: str) -> None:
        if name not in {
            "tool_dispatches",
            "provider_invocations",
            "provider_dispatches",
            "metadata_reads",
            "pending_usage",
        }:
            raise ValueError("Unknown measurement counter")
        if not self.active:
            return
        value = getattr(self, name)
        if value >= MAX_COUNT:
            self.overflowed.add(name)
            self.unavailable_provider("counter_limit_exceeded")
            self.metadata_failed = True
            return
        setattr(self, name, value + 1)

    def unavailable_provider(self, reason: str) -> None:
        if reason not in _REASONS:
            raise ValueError("Unknown measurement coverage reason")
        if self.active:
            self.provider_unavailable.add(reason)

    def response(self, message: Any, *, dispatched: bool = False, decision: bool = False) -> None:
        if not self.active:
            return
        if dispatched and self.pending_usage:
            self.pending_usage -= 1
        usage = message.get("usage") if isinstance(message, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        prompt = usage.get("prompt_tokens")
        total = usage.get("total_tokens")
        if decision:
            if "prompt_tokens" not in usage:
                prompt = usage.get("input_tokens")
            if "total_tokens" not in usage:
                output = usage.get("completion_tokens", usage.get("output_tokens"))
                total = (
                    prompt + output
                    if (type(prompt) is int and prompt >= 0 and type(output) is int and output >= 0)
                    else None
                )
        if type(total) is int and 0 <= total <= MAX_COUNT - self.reported_total_tokens:
            self.reported_total_tokens += total
        else:
            self.missing_usage = True
        if type(prompt) is int and 0 <= prompt <= MAX_COUNT:
            self.reported_peak_prompt_tokens = max(self.reported_peak_prompt_tokens, prompt)
        else:
            self.missing_prompt_usage = True

    def observation(self, decision: Any) -> tuple[dict[str, int], dict[str, Any]]:
        if decision is not None:
            from app.modules.assistant.decision import DecisionSession

            if type(decision) is not DecisionSession or (
                decision.calls
                and getattr(decision._post, "__func__", None) is not DecisionSession._post
            ):
                self.unavailable_provider("decision_requests_unmeasured")
        if self.resumed:
            self.unavailable_provider("prior_attempt_unmeasured")
        counts: dict[str, int] = {}
        unavailable: dict[str, list[str]] = {}
        if self.resumed or "tool_dispatches" in self.overflowed:
            unavailable["tool_calls"] = [
                *(["prior_attempt_unmeasured"] if self.resumed else []),
                *(["counter_limit_exceeded"] if "tool_dispatches" in self.overflowed else []),
            ]
        else:
            counts["tool_calls"] = self.tool_dispatches
        if not self.collaborative and not self.resumed and not self.tool_dispatches:
            counts["participants"] = 1
        else:
            unavailable["participants"] = ["complete_participant_set_unavailable"]
        reasons = sorted(self.provider_unavailable)
        if reasons:
            unavailable["provider_calls"] = reasons
        else:
            counts["provider_calls"] = self.provider_dispatches
        for key, missing, value in (
            ("total_tokens", self.missing_usage, self.reported_total_tokens),
            ("context_tokens", self.missing_prompt_usage, self.reported_peak_prompt_tokens),
        ):
            missing_reasons = [
                *reasons,
                *(["provider_usage_missing"] if missing or self.pending_usage else []),
            ]
            if missing_reasons:
                unavailable[key] = missing_reasons
            else:
                counts[key] = value
        metadata_reasons = []
        if not metadata_read_coverage():
            metadata_reasons.append("metadata_owner_uninstrumented")
        if self.metadata_failed:
            metadata_reasons.append("failed_metadata_read_unmeasured")
        if self.metadata_direct_unobserved:
            metadata_reasons.append("direct_metadata_borrow_unmeasured")
        if self.resumed:
            metadata_reasons.append("prior_attempt_unmeasured")
        if metadata_reasons:
            unavailable["metadata_reads"] = metadata_reasons
        else:
            counts["metadata_reads"] = self.metadata_reads
        return counts, {
            "version": "2",
            "scope": "current_loop_attempt",
            "provider_call_basis": "httpx_dispatch_attempts_including_retries_and_redirects",
            "metadata_read_basis": "executed_result_set_reads",
            "provider_invocations": self.provider_invocations,
            "tool_dispatches": self.tool_dispatches,
            "reported_total_tokens": self.reported_total_tokens,
            "reported_peak_prompt_tokens": self.reported_peak_prompt_tokens,
            "estimated_peak_context_tokens": self.estimated_peak_context_tokens,
            "observed_counts": {
                "provider_calls": self.provider_dispatches,
                "tool_calls": self.tool_dispatches,
                "participants": 1,
                "metadata_reads": self.metadata_reads,
            },
            "unavailable": unavailable,
        }


@contextmanager
def measurement_scope(measured: AttemptMeasurements | None) -> Iterator[None]:
    token = _current.set(measured)
    try:
        yield
    finally:
        _current.reset(token)


def current_measurements() -> AttemptMeasurements | None:
    measured = _current.get()
    return measured if measured is not None and measured.active else None


def provider_started(messages: list[dict] | None = None, extra: Any = None) -> None:
    measured = current_measurements()
    if measured is None:
        return
    measured.increment("provider_invocations")
    # Keep the same bounded context estimate used by Nova; retain no source content.
    import json

    size = estimate_messages_tokens(messages or [])
    if extra:
        size += len(json.dumps(extra, separators=(",", ":"), default=str)) // 4
    measured.estimated_peak_context_tokens = max(measured.estimated_peak_context_tokens, size)


def provider_response(message: Any, *, decision: bool = False) -> None:
    measured = current_measurements()
    if measured is not None:
        measured.response(message, dispatched=True, decision=decision)


@asynccontextmanager
async def observe_http_dispatches(client: Any) -> AsyncIterator[None]:
    measured = current_measurements()
    if measured is None:
        yield
        return
    if type(client) is not httpx.AsyncClient:
        measured.unavailable_provider("custom_transport_unmeasured")
        yield
        return

    async def dispatched(_request: httpx.Request) -> None:
        if current_measurements() is measured:
            measured.increment("provider_dispatches")
            measured.increment("pending_usage")

    hooks = client.event_hooks["request"]
    if any(getattr(hook, "__nova_attempt__", None) is measured for hook in hooks):
        yield
        return
    dispatched.__nova_attempt__ = measured
    hooks.append(dispatched)
    try:
        yield
    except BlockedEndpointError:
        measured.unavailable_provider("blocked_transport_unmeasured")
        raise
    finally:
        hooks.remove(dispatched)


def metadata_reads_observed(owner: _Owner) -> _Owner:
    """Declare an existing database owner instrumented; do not wrap its execution."""
    owner.__nova_metadata_read_observer__ = True
    return owner


def metadata_borrows_observed(owner: _Owner) -> _Owner:
    """Declare the existing connection owner reports unobserved direct borrows."""
    owner.__nova_metadata_borrow_observer__ = True
    return owner


@contextmanager
def metadata_execution_scope() -> Iterator[None]:
    """Cover only the current task's execute_system borrow, never child tasks."""
    token = _metadata_executor.set(asyncio.current_task())
    try:
        yield
    finally:
        _metadata_executor.reset(token)


def record_metadata_borrow() -> None:
    """Call on system_conn entry to disclose direct, unobserved cursor access."""
    measured = current_measurements()
    if measured is not None and _metadata_executor.get() is not asyncio.current_task():
        measured.metadata_direct_unobserved = True


def metadata_read_coverage() -> bool:
    from app.core.database import db

    return (
        getattr(db.execute_system, "__nova_metadata_read_observer__", False) is True
        and getattr(db.system_conn, "__nova_metadata_borrow_observer__", False) is True
    )


def record_metadata_read() -> None:
    """Call once after metadata execution returns a cursor description, before fetch."""
    measured = current_measurements()
    if measured is not None:
        measured.increment("metadata_reads")


def record_metadata_read_failure() -> None:
    """Unknown result shape after failure cannot prove a complete read count."""
    measured = current_measurements()
    if measured is not None:
        measured.metadata_failed = True
