"""Agentic-harness evaluation framework (NOVA-124).

The benchmark suite (``tests/benchmark``) measures the loop's **overhead**. This
suite measures its **behaviour**: given a scripted model trajectory, did the
harness do the right thing? It is the harness's unit of *quality* regression
testing, in the spirit of an agent eval: deterministic, offline, and asserting
on the trajectory (which tools ran, how the turn terminated, whether anything
leaked) rather than on wall-clock.

Why scripted rather than a live model? Three reasons:

* **Determinism.** A pass/fail gate must not depend on a model's mood. A
  scripted provider makes "the model proposed a destructive statement" a fixture,
  not a flake.
* **No credentials.** CI has no provider key. The eval must run in the unit-test
  job.
* **Coverage of rare paths.** A real model almost never proposes
  ``DROP ROLE ACCOUNTADMIN`` or a tool call with a credential in the arguments;
  a scripted trajectory exercises the guard every run.

A scenario is a ``ScriptedProvider`` script plus checks. The runner drives one
turn to completion, captures every SSE frame, and evaluates the checks against a
``TurnResult``. ``pytest`` turns a failing check into a test failure; the same
runner can be called from a script to print a scorecard.

The framework is deliberately small and dependency-free, mirroring the harness
it complements.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.modules.assistant.context import ContextManager
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantMessage, AssistantThread
from app.modules.assistant.tools import (
    ToolInvocation,
    ToolOutcome,
    ToolRegistry,
    report_tool_progress,
)


@dataclass
class TurnResult:
    """Everything one scripted turn produced, for checks to assert on."""

    frames: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    context_stats: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    text: str = ""
    provider_calls: int = 0
    tool_runs: list[str] = field(default_factory=list)
    consent_prompts: list[tuple[str, str]] = field(default_factory=list)

    # ── derived views the checks use ─────────────────────────────────────────

    @property
    def events(self) -> list[str]:
        """The SSE event names, in order."""
        return [_event_name(frame) for frame in self.frames]

    @property
    def error_codes(self) -> list[str]:
        return [_error_code(frame) for frame in self.frames if _event_name(frame) == "error"]

    @property
    def finish_reason(self) -> str | None:
        for frame in reversed(self.frames):
            if _event_name(frame) == "done":
                return _json_field(frame, "finish_reason")
        return None

    @property
    def tool_calls_proposed(self) -> list[str]:
        """Tool names the model proposed (from ``tool_call`` frames)."""
        names: list[str] = []
        for frame in self.frames:
            if _event_name(frame) == "tool_call":
                name = _json_field(frame, "tool_name")
                if name:
                    names.append(name)
        return names


class EvalTool:
    """A controllable fake tool for a scenario.

    ``classification`` drives the consent gate exactly as a real tool's does, so
    a scenario can assert that a destructive call prompted and a read-only one
    auto-approved under a grant.
    """

    def __init__(
        self,
        name: str,
        *,
        classification: str = "read_only",
        description: str = "eval tool",
        parameters: dict[str, Any] | None = None,
        ok: bool = True,
        summary: str = "1 row (eval)",
        error: str | None = None,
        table: dict[str, Any] | None = None,
        chart: dict[str, Any] | None = None,
        citations: list[dict[str, Any]] | None = None,
        progress: list[dict[str, Any]] | None = None,
    ) -> None:
        self.name = name
        self.classification = classification
        self.description = description
        self.parameters = parameters or {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        }
        self.ok = ok
        self.summary = summary
        self.error = error
        self.table = table
        self.chart = chart
        self.citations = citations
        self.progress = progress or []
        self.runs: list[ToolInvocation] = []

    def preview(self, invocation: ToolInvocation) -> str:
        return str(invocation.arguments.get("sql", "SELECT 1"))

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        self.runs.append(invocation)
        for item in self.progress:
            report_tool_progress(
                context,
                stage=str(item.get("stage") or "running"),
                text=str(item.get("text") or "Working"),
                sql_preview=(
                    str(item["sql_preview"]) if item.get("sql_preview") is not None else None
                ),
            )
        if self.ok:
            return ToolOutcome(
                ok=True,
                summary=self.summary,
                table=self.table,
                chart=self.chart,
                citations=self.citations,
            )
        return ToolOutcome(ok=False, summary="", error=self.error or "eval failure")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Scenario:
    """A scripted agent turn plus the checks that define correct behaviour.

    ``script`` is the provider's message list. ``content`` is the user turn.
    ``checks`` is a list of ``(name, callable)``; each callable takes the
    ``TurnResult`` and returns ``True`` or raises/returns a string on failure.
    """

    name: str
    script: list[dict[str, Any]]
    checks: list[tuple[str, Callable[[TurnResult], bool | str]]] = field(default_factory=list)
    content: str = "run the task"
    tools: list[EvalTool] = field(default_factory=list)
    read_only_grant: bool = False
    resolve_consent: Callable[[ToolInvocation, str], Awaitable[bool | None]] | None = None
    history_turns: int = 0
    history_chars: int = 0
    system_prompt: str = "eval system prompt"
    context_manager: ContextManager | None = None
    max_iterations: int = 8
    time_budget_seconds: float = 60.0


def _event_name(frame: str) -> str:
    for line in frame.splitlines():
        if line.startswith("event: "):
            return line[len("event: ") :].strip()
    return ""


def _data(frame: str) -> dict[str, Any]:
    for line in frame.splitlines():
        if line.startswith("data: "):
            try:
                return json.loads(line[len("data: ") :])
            except json.JSONDecodeError:
                return {}
    return {}


def _json_field(frame: str, key: str) -> Any:
    return _data(frame).get(key)


def _error_code(frame: str) -> str:
    return str(_json_field(frame, "code") or "")


async def run_scenario(scenario: Scenario) -> TurnResult:
    """Drive one scripted turn and return what it produced."""
    registry = ToolRegistry()
    for tool in scenario.tools:
        registry.register(tool)

    from tests.benchmark.harness import ScriptedProvider

    provider = ScriptedProvider(script=list(scenario.script))

    thread = AssistantThread(thread_id="eval", user_name="eval", title="Eval")
    pad = "x" * scenario.history_chars
    for i in range(scenario.history_turns):
        thread.messages.append(
            AssistantMessage(message_id=f"u{i}", role="user", content=f"question {i} {pad}")
        )
        thread.messages.append(
            AssistantMessage(message_id=f"a{i}", role="assistant", content=f"answer {i} {pad}")
        )
    thread.consent.always_allow_read_only = scenario.read_only_grant
    thread.messages.append(
        AssistantMessage(message_id="cur", role="user", content=scenario.content)
    )

    loop = AssistantLoop(
        provider=provider,
        registry=registry,
        max_iterations=scenario.max_iterations,
        time_budget_seconds=scenario.time_budget_seconds,
        system_prompt=scenario.system_prompt,
        context_manager=scenario.context_manager,
    )
    context = LoopContext(user_name="eval", thread_id="eval")

    resolver = scenario.resolve_consent or _record_and_allow

    prompts: list[tuple[str, str]] = []

    async def consent(invocation: ToolInvocation, classification: str) -> bool | None:
        prompts.append((invocation.tool_name, classification))
        return await resolver(invocation, classification)

    frames: list[str] = []
    async for frame in loop.run(
        thread=thread,
        user_content=scenario.content,
        context=context,
        resolve_consent=consent,
    ):
        frames.append(frame)

    text = "".join(_data(f).get("text", "") for f in frames if _event_name(f) == "text_delta")

    return TurnResult(
        frames=frames,
        steps=list(context.steps or []),
        context_stats=dict(context.context_stats or {}),
        usage=dict(context.usage or {}),
        text=text,
        provider_calls=provider.calls,
        tool_runs=[inv.tool_name for tool in scenario.tools for inv in tool.runs],
        consent_prompts=prompts,
    )


async def _record_and_allow(_inv: ToolInvocation, _cls: str) -> bool:
    return True


async def allow(_inv: ToolInvocation, _cls: str) -> bool:
    return True


async def deny(_inv: ToolInvocation, _cls: str) -> bool:
    return False


@dataclass
class ScenarioReport:
    scenario: str
    checks: list[CheckResult]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def summary(self) -> str:
        bad = [c for c in self.checks if not c.passed]
        if not bad:
            return f"PASS {self.scenario} ({len(self.checks)} checks)"
        lines = [f"FAIL {self.scenario}:"]
        lines += [f"  - {c.name}: {c.detail}" for c in bad]
        return "\n".join(lines)


def evaluate(scenario: Scenario, result: TurnResult) -> ScenarioReport:
    """Run every check against a turn result. A check returns ``True`` to pass,
    ``False`` (or a truthy string) to fail with that string as the detail."""
    checks: list[CheckResult] = []
    for name, check in scenario.checks:
        try:
            outcome = check(result)
        except Exception as exc:  # noqa: BLE001 - a raising check is a failure
            checks.append(CheckResult(name, False, f"raised {type(exc).__name__}: {exc}"))
            continue
        if outcome is True:
            checks.append(CheckResult(name, True))
        elif outcome is False:
            checks.append(CheckResult(name, False, "check returned False"))
        else:
            checks.append(CheckResult(name, False, str(outcome)))
    return ScenarioReport(scenario.name, checks)


# ── reusable checks ──────────────────────────────────────────────────────────


def used_tool(name: str) -> Callable[[TurnResult], bool | str]:
    def _check(result: TurnResult) -> bool | str:
        if name in result.tool_runs:
            return True
        return f"expected {name} to run; ran {result.tool_runs}"

    return _check


def did_not_use_tool(name: str) -> Callable[[TurnResult], bool | str]:
    def _check(result: TurnResult) -> bool | str:
        if name not in result.tool_runs:
            return True
        return f"{name} must not have run"

    return _check


def finished_with(reason: str | None) -> Callable[[TurnResult], bool | str]:
    def _check(result: TurnResult) -> bool | str:
        if result.finish_reason == reason:
            return True
        return f"expected finish_reason {reason!r}, got {result.finish_reason!r}"

    return _check


def has_error_code(code: str) -> Callable[[TurnResult], bool | str]:
    def _check(result: TurnResult) -> bool | str:
        if code in result.error_codes:
            return True
        return f"expected error {code!r}; errors {result.error_codes}"

    return _check


def no_error() -> Callable[[TurnResult], bool | str]:
    def _check(result: TurnResult) -> bool | str:
        if not result.error_codes:
            return True
        return f"expected no error; got {result.error_codes}"

    return _check


def prompted_for(tool_name: str) -> Callable[[TurnResult], bool | str]:
    def _check(result: TurnResult) -> bool | str:
        if any(name == tool_name for name, _cls in result.consent_prompts):
            return True
        return f"expected a consent prompt for {tool_name}; prompts {result.consent_prompts}"

    return _check


def did_not_prompt() -> Callable[[TurnResult], bool | str]:
    def _check(result: TurnResult) -> bool | str:
        if not result.consent_prompts:
            return True
        return f"expected no consent prompt; prompts {result.consent_prompts}"

    return _check


def announced_tool(name: str, *, status: str | None = None) -> Callable[[TurnResult], bool | str]:
    """A ``tool_call`` frame named the tool, with a redacted preview.

    The frame is emitted for every call, approved or not, because it is the only
    carrier of the SQL preview the panel shows. ``status`` optionally pins the
    frame's status: ``running`` for a covered call, ``pending`` for a prompt.
    """

    def _check(result: TurnResult) -> bool | str:
        for frame in result.frames:
            if _event_name(frame) != "tool_call":
                continue
            if _json_field(frame, "tool_name") != name:
                continue
            if status is not None and _json_field(frame, "status") != status:
                continue
            if not str(_json_field(frame, "sql_preview") or ""):
                return f"{name} announced with an empty preview"
            return True
        return f"{name} was not announced on the stream"

    return _check


def answer_contains(fragment: str) -> Callable[[TurnResult], bool | str]:
    def _check(result: TurnResult) -> bool | str:
        if fragment in result.text:
            return True
        return f"answer did not contain {fragment!r}: {result.text[:120]!r}"

    return _check


def recorded_step(kind: str, **fields: object) -> Callable[[TurnResult], bool | str]:
    """The turn's stored trace has a step of this kind with these fields.

    The trace is what a reopened conversation is rebuilt from, so a step that is
    emitted but not recorded is a step that disappears on reload.
    """

    def _check(result: TurnResult) -> bool | str:
        for step in result.steps:
            if step.get("kind") != kind:
                continue
            if all(step.get(key) == value for key, value in fields.items()):
                return True
        return f"no recorded {kind} step matching {fields}; trace {result.steps}"

    return _check


def has_tool_detail(tool_call_id: str, text: str) -> Callable[[TurnResult], bool | str]:
    """A ``tool_detail`` frame carried this text for this call."""

    def _check(result: TurnResult) -> bool | str:
        for frame in result.frames:
            if _event_name(frame) != "tool_detail":
                continue
            if _json_field(frame, "tool_call_id") != tool_call_id:
                continue
            if _json_field(frame, "text") == text:
                return True
        return f"no tool_detail for {tool_call_id} matching the expected text"

    return _check


def check(name: str, fn: Callable[[TurnResult], bool | str]) -> tuple[str, Callable]:
    return (name, fn)
