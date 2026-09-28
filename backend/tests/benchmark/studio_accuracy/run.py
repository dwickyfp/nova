"""Studio accuracy benchmark runner.

    cd backend && uv run python -m tests.benchmark.studio_accuracy.run            # L0, offline
    cd backend && uv run python -m tests.benchmark.studio_accuracy.run --engine   # L2, StarRocks
    cd backend && NOVA_LIVE_ASSISTANT_TEST=1 \\
        uv run python -m tests.benchmark.studio_accuracy.run --live [--subset N]  # L3, model

Levels
------
* **L0 offline** (CI gate). The deterministic fast path must never return a
  *confident wrong* plan (``silent_wrong == 0``), and cases marked ``lexical``
  must resolve exactly.
* **L2 engine**. Each answerable case's expected plan is compiled by Nova and
  executed on ``NOVA_BENCH``; the rows must equal an independent gold query
  (``gold.py``). This checks the compiler and the time grammar, not a planner.
* **L3 live**. The real agent loop with the configured provider answers each
  question. The semantic result table must equal gold, out-of-scope questions
  must end in a clarification, and no forbidden tool may run.

``--baseline FILE`` fails the run when a gated metric drops more than two
points below the stored baseline, or when ``silent_wrong`` is non-zero.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import (
    SemanticFilter,
    SemanticHaving,
    SemanticOrder,
    SemanticPlan,
    SemanticPlanError,
    SemanticPlanner,
    SemanticTime,
    SemanticTopN,
    SemanticTransform,
)
from tests.benchmark.studio_accuracy.cases import Case, all_cases
from tests.benchmark.studio_accuracy.gold import gold_sql, normalize_rows
from tests.benchmark.studio_accuracy.model import bench_model

CURRENT_PHASE = int(os.environ.get("NOVA_BENCH_PHASE", "2"))
REPORT_DIR = Path(__file__).resolve().parents[4] / "docs" / "benchmarks" / "studio-accuracy"


@dataclass
class CaseResult:
    id: str
    category: str
    level: str
    passed: bool
    silent_wrong: bool = False
    skipped: bool = False
    detail: str = ""
    latency_ms: float | None = None
    provider_calls: int | None = None
    tokens: int | None = None
    tools: list[str] = field(default_factory=list)
    finish_reason: str | None = None
    #: Where the time went: before the first loop model call (planning and
    #: routing), loop model calls, and tool calls (including their own model use).
    timing: dict[str, float] = field(default_factory=dict)


# ── plan construction and comparison ────────────────────────────────────────

def plan_from_expect(model: SemanticModelIR, expect: dict[str, Any]) -> SemanticPlan:
    metrics = tuple(expect.get("metrics") or ())
    filters = []
    for name, value in (expect.get("filters") or {}).items():
        if isinstance(value, list):
            filters.append(SemanticFilter(name, "IN", list(value)))
        else:
            filters.append(SemanticFilter(name, "=", value))
    time = None
    if expect.get("range") or expect.get("grain"):
        metric = model.metric(metrics[0])
        dimension = metric.default_time_dimension if metric else None
        time = SemanticTime(
            str(dimension), grain=expect.get("grain"), range=expect.get("range"),
            compare=expect.get("compare"),
        )
    limit = expect.get("limit")
    top = expect.get("top_n_per_group")
    return SemanticPlan(
        metrics=metrics,
        dimensions=tuple(expect.get("dimensions") or ()),
        filters=tuple(filters),
        named_filters=tuple(expect.get("named_filters") or ()),
        time=time,
        order_by=(SemanticOrder(metrics[0]),) if limit else (),
        limit=limit,
        having=tuple(
            SemanticHaving(name, operator, value)
            for name, (operator, value) in (expect.get("having") or {}).items()
        ),
        transforms=(
            (SemanticTransform(metrics[0], expect["transform"]),)
            if expect.get("transform") else ()
        ),
        top_n_per_group=(
            SemanticTopN(int(top["n"]), tuple(top["partition_by"]), metrics[0]) if top else None
        ),
    )


def plan_differences(plan: SemanticPlan, expect: dict[str, Any]) -> list[str]:
    diffs = []
    if set(plan.metrics) != set(expect.get("metrics") or ()):
        diffs.append(f"metrics {list(plan.metrics)} != {expect.get('metrics')}")
    if set(plan.dimensions) != set(expect.get("dimensions") or ()):
        diffs.append(f"dimensions {list(plan.dimensions)} != {expect.get('dimensions') or []}")
    got_filters = {
        item.field: sorted(item.value) if isinstance(item.value, list) else item.value
        for item in plan.filters
    }
    want_filters = {
        key: sorted(value) if isinstance(value, list) else value
        for key, value in (expect.get("filters") or {}).items()
    }
    if got_filters != want_filters:
        diffs.append(f"filters {got_filters} != {want_filters}")
    if set(plan.named_filters) != set(expect.get("named_filters") or ()):
        diffs.append(f"named_filters {list(plan.named_filters)}")
    time = plan.time
    for key, got in (
        ("range", time.range if time else None),
        ("compare", time.compare if time else None),
        ("grain", time.grain if time else None),
    ):
        if got != expect.get(key):
            diffs.append(f"{key} {got!r} != {expect.get(key)!r}")
    if plan.limit != expect.get("limit"):
        diffs.append(f"limit {plan.limit} != {expect.get('limit')}")
    return diffs


def _confident(planned: Any) -> bool:
    return bool(
        planned is not None and planned.plan is not None
        and not planned.confidence.unresolved and planned.confidence.level == "high"
    )


# ── L0 ──────────────────────────────────────────────────────────────────────

def evaluate_offline(cases: list[Case], model: SemanticModelIR) -> list[CaseResult]:
    planner = SemanticPlanner()
    results = []
    for case in cases:
        if case.turns:
            results.append(CaseResult(case.id, case.category, "L0", True, skipped=True,
                                      detail="follow-up: live only"))
            continue
        try:
            planned = planner.plan(model, case.question)
        except SemanticPlanError as exc:
            planned = None
            detail = f"planner deferred: {exc}"
        else:
            detail = ""
        confident = _confident(planned)
        diffs = plan_differences(planned.plan, case.expect) if confident else []
        if case.outcome != "answer":
            silent_wrong = confident
            passed = not confident
            detail = detail or ("confident plan for an unanswerable question" if confident else "")
        elif case.phase > CURRENT_PHASE:
            silent_wrong = confident and bool(diffs)
            passed = not silent_wrong
        else:
            silent_wrong = confident and bool(diffs)
            if case.lexical:
                passed = confident and not diffs
                if not confident:
                    unresolved = planned.confidence.unresolved if planned else ()
                    detail = detail or f"fast path not confident: {unresolved}"
            else:
                passed = not silent_wrong
            detail = detail or "; ".join(diffs)
        results.append(CaseResult(case.id, case.category, "L0", passed,
                                  silent_wrong=silent_wrong, detail=detail))
    return results


# ── L2 ──────────────────────────────────────────────────────────────────────

async def _rows(execute, sql: str) -> list[list[Any]]:
    result = await execute(sql)
    return list(result.get("rows") or [])


async def evaluate_engine(
    cases: list[Case], model: SemanticModelIR, today: date, execute,
) -> list[CaseResult]:
    compiler = SemanticCompiler()
    results = []
    for case in cases:
        if case.outcome != "answer" or case.phase > CURRENT_PHASE:
            continue
        try:
            gold = gold_sql(case.expect, today)
        except ValueError as exc:
            results.append(CaseResult(case.id, case.category, "L2", True, skipped=True,
                                      detail=str(exc)))
            continue
        started = time.perf_counter()
        try:
            compiled = compiler.compile(model, plan_from_expect(model, case.expect)).sql
            got = await _rows(execute, compiled)
            want = await _rows(execute, gold)
        except Exception as exc:  # noqa: BLE001 - reported per case
            results.append(CaseResult(case.id, case.category, "L2", False,
                                      detail=f"{type(exc).__name__}: {exc}"[:400]))
            continue
        ordered = bool(case.expect.get("limit"))
        equal = normalize_rows(got, ordered=ordered) == normalize_rows(want, ordered=ordered)
        results.append(CaseResult(
            case.id, case.category, "L2", equal and bool(want),
            detail="" if equal and want else (
                "gold returned no rows" if not want else
                f"rows differ: got {normalize_rows(got)[:3]} want {normalize_rows(want)[:3]}"
            ),
            latency_ms=(time.perf_counter() - started) * 1000,
        ))
    return results


# ── L3 ──────────────────────────────────────────────────────────────────────

def _frame_payload(frame: str) -> dict[str, Any]:
    try:
        return json.loads(frame.split("data: ", 1)[1])
    except (IndexError, ValueError):
        return {}


async def _live_turn(loop, thread, question, context_factory, stamp):
    from app.modules.assistant.state import AssistantMessage

    context = context_factory()
    context.steps = []
    thread.messages.append(AssistantMessage(
        message_id=f"u{len(thread.messages)}", role="user", content=question,
        security_context=stamp,
    ))

    async def allow(_invocation, _classification):
        return True

    frames = [
        frame async for frame in loop.run(
            thread=thread, user_content=question, context=context, resolve_consent=allow,
        )
    ]
    text = "".join(
        _frame_payload(frame).get("text", "") for frame in frames
        if frame.startswith("event: text_delta")
    )
    tables = [
        _frame_payload(frame) for frame in frames if frame.startswith("event: table")
    ]
    tools = [
        _frame_payload(frame).get("tool_name", "") for frame in frames
        if frame.startswith("event: tool_call")
    ]
    finish = next(
        (_frame_payload(frame).get("finish_reason") for frame in reversed(frames)
         if frame.startswith("event: done")), None,
    )
    errors = [
        f"{payload.get('code')}: {str(payload.get('message'))[:200]}"
        for payload in (_frame_payload(frame) for frame in frames
                        if frame.startswith("event: error"))
    ]
    context.bench_errors = errors  # type: ignore[attr-defined]
    if os.environ.get("NOVA_BENCH_DUMP") == "1":
        for frame in frames:
            if frame.startswith(("event: tool_call", "event: error", "event: tool_status")):
                payload = _frame_payload(frame)
                print("   ", frame.split("\n", 1)[0][7:], json.dumps({
                    key: payload.get(key) for key in
                    ("tool_name", "sql_preview", "status", "code", "message", "result_summary")
                    if payload.get(key)
                }, ensure_ascii=False)[:600], file=sys.stderr)
        route = next((step for step in context.steps or []
                      if step.get("kind") == "runtime_decision"), {})
        print("    route:", route.get("intent"), route.get("selected_tools"), file=sys.stderr)
        check = next((step for step in context.steps or []
                      if step.get("kind") == "answer_verification"), {})
        print("    verify:", check.get("status"), check.get("unsupported"), file=sys.stderr)
        for table in tables:
            print("    table:", table.get("columns"), str(table.get("rows"))[:300], file=sys.stderr)
        print("    text:", text[:600].replace("\n", " "), file=sys.stderr)
    thread.messages.append(AssistantMessage(
        message_id=f"a{len(thread.messages)}", role="assistant", content=text,
        steps=list(context.steps or []), security_context=stamp,
    ))
    return text, tables, tools, finish, context


async def evaluate_live(
    cases: list[Case], model: SemanticModelIR, today: date, execute, *, subset: int | None,
    repeat: int = 1,
) -> list[CaseResult]:
    from app.core.security import encrypt_password
    from app.modules.agents.prompt import build_system_prompt
    from app.modules.agents.service import BUDGET_PROFILES
    from app.modules.agents.tools.compute_metrics import compute_metrics_tool
    from app.modules.agents.tools.semantic_query import SemanticQueryTool
    from app.modules.assistant.provider import assistant_provider
    from app.modules.assistant.security import observation_context, session_security
    from app.modules.assistant.service import AssistantLoop, LoopContext
    from app.modules.assistant.state import AssistantThread
    from app.modules.assistant.tools import ToolRegistry
    from tests.benchmark.studio_accuracy.model import bench_definition_parsed

    definition = bench_definition_parsed()
    semantic = SemanticQueryTool()

    async def resolve_model(_context, _question):
        return {"semantic_model_id": "nova_bench", "definition": definition,
                "_scoped_ir": model}

    semantic._resolve_model = resolve_model  # type: ignore[method-assign]
    from app.modules.agents.tools.describe_agent import DescribeAgentTool

    registry = ToolRegistry()
    registry.register(semantic)
    registry.register(compute_metrics_tool)
    agent = {
        "name": "Nova Bench Analyst",
        "description": "Answers commerce questions from the nova_bench Semantic View.",
        "default_tools": ["semantic_query", "compute_metrics"],
    }
    limits = BUDGET_PROFILES["analyst"]
    username, password, role = await bench_identity(execute)
    user = {
        "username": username,
        "encrypted_password": encrypt_password(password),
        "active_role": role,
        "assigned_roles": [role],
        "session_id": "nova-bench",
    }
    del password
    stamp = observation_context(session_security(user))
    selected = cases[:subset] if subset else cases
    # Each case runs ``repeat`` times; the model is not deterministic.
    selected = [case for case in selected for _ in range(max(1, repeat))]
    results = []
    for case in selected:
        if case.phase > CURRENT_PHASE:
            continue
        loop = AssistantLoop(
            provider=assistant_provider, registry=registry,
            max_iterations=limits.max_iterations,
            time_budget_seconds=float(limits.time_budget_seconds),
            system_prompt=build_system_prompt(agent, actual_tools=registry.names()),
            max_calls_per_tool=limits.max_calls_per_tool, iterative=True,
        )
        thread = AssistantThread(thread_id=f"bench-{case.id}", user_name=user["username"],
                                 title=case.id)
        thread.consent.always_allow_read_only = True

        def context_factory(case_id: str = case.id) -> LoopContext:
            # A Studio turn: scoped agent, one bound Semantic View already authorized.
            context = LoopContext(
                user_name=user["username"], user=dict(user), thread_id=f"bench-{case_id}",
                semantic_view_ids=["nova_bench"],
            )
            context.authorized_semantic_models = [{
                "semantic_model_id": "nova_bench", "definition": definition,
                "_scoped_ir": model,
            }]
            # The same scope projection Studio builds for a bound agent.
            context.agent_scope = DescribeAgentTool(
                registry, name=agent["name"]
            ).planning_scope(context)
            return context

        started = time.perf_counter()
        try:
            for turn in case.turns:
                await _live_turn(loop, thread, turn, context_factory, stamp)
            text, tables, tools, finish, context = await _live_turn(
                loop, thread, case.question, context_factory, stamp,
            )
        except Exception as exc:  # noqa: BLE001 - reported per case
            results.append(CaseResult(case.id, case.category, "L3", False,
                                      detail=f"{type(exc).__name__}: {exc}"[:400]))
            continue
        latency = (time.perf_counter() - started) * 1000
        usage = context.usage or {}
        errors = getattr(context, "bench_errors", [])
        if any(_is_infrastructure(error) for error in errors) or (
            finish == "planning_failed" and not tools
        ):
            # The provider or network failed, not the agent: excluded from accuracy.
            results.append(CaseResult(case.id, case.category, "L3", True, skipped=True,
                                      detail="infrastructure: " + " | ".join(errors)[:300],
                                      finish_reason=finish))
            print(f"SKIP  L3 {case.id}  infrastructure", file=sys.stderr, flush=True)
            continue
        forbidden = [tool for tool in tools if tool in {"query_execute", "query_mutate"}]
        passed, silent_wrong, detail = False, False, ""
        if case.outcome in {"clarify", "refuse"}:
            # Wrong only when it answers with numbers and never states the gap.
            silent_wrong = (
                finish == "stop" and _looks_like_answer(text) and not _states_limitation(text)
            )
            refused = case.outcome == "refuse" and finish in {
                "required_capability_unavailable", "policy_denied", "denied",
            }
            passed = (
                finish in {"clarification", "stop", "denied", "out_of_scope"} or refused
            ) and not silent_wrong
            detail = f"finish={finish}"
        else:
            try:
                want = normalize_rows(await _rows(execute, gold_sql(case.expect, today)))
            except ValueError:
                want = None
            semantic_tables = [
                normalize_rows(table.get("rows") or []) for table in tables
            ]
            ranked = bool(case.expect.get("limit"))
            matched = want is not None and any(
                rows == want or rows_contain(rows, want)
                # A ranked table of every group answers "top N" when its first N rows match.
                or (ranked and rows_contain(rows[:len(want)], want))
                for rows in semantic_tables
            )
            if not matched and case.answer and want:
                gold_rows = await _rows(execute, gold_sql(case.expect, today))
                expected = expected_answer_value(case.answer, gold_rows)
                matched = expected is not None and text_states_value(
                    text, expected, percent=case.answer.get("kind") in {"pct_change", "share"}
                )
            passed = matched and finish == "stop"
            silent_wrong = finish == "stop" and bool(semantic_tables) and not matched
            detail = f"finish={finish}" + ("" if matched else f"; tables={len(tables)}")
            if not matched:
                # What came back against what gold expected, so a failure needs no rerun.
                got = [
                    f"{table.get('columns')}:{(table.get('rows') or [])[:3]}" for table in tables
                ]
                detail += f"; want={(want or [])[:3]}; got={got}"[:900]
        if forbidden:
            passed = False
            detail += f"; forbidden={forbidden}"
        if errors:
            detail += "; errors=" + " | ".join(errors)
        results.append(CaseResult(
            case.id, case.category, "L3", passed, silent_wrong=silent_wrong, detail=detail,
            latency_ms=latency, tools=tools, finish_reason=finish,
            tokens=int(usage.get("total_tokens") or 0) or None,
            timing=_timing(context.steps or []),
        ))
        print(f"{'PASS' if passed else 'FAIL'}  L3 {case.id}  {detail}", file=sys.stderr,
              flush=True)
    return results


async def bench_identity(execute) -> tuple[str, str, str]:
    """The account live runs use: ``NOVA_BENCH_USER``/``NOVA_BENCH_PASSWORD`` when set.

    A provided account is used as-is (its default role becomes the active role);
    otherwise a least-privilege benchmark account is prepared. The password is
    read from the environment and never printed or stored.
    """
    username = os.environ.get("NOVA_BENCH_USER")
    password = os.environ.get("NOVA_BENCH_PASSWORD")
    if not username or not password:
        created_user, created_password = await ensure_bench_user(execute)
        return created_user, created_password, BENCH_ROLE
    from app.core.database import db

    async with db.user_conn(username, password) as conn, conn.cursor() as cursor:
        await cursor.execute("SELECT CURRENT_ROLE()")
        row = await cursor.fetchone()
    raw = str(row[0] if row else "")
    role = os.environ.get("NOVA_BENCH_ROLE") or raw.strip("[]` '").split(",")[0].strip("` '")
    if not role:
        raise RuntimeError("The benchmark account has no active role.")
    return username, password, role


BENCH_USER = "nova_bench_user"
BENCH_ROLE = "nova_bench_reader"


async def ensure_bench_user(execute) -> tuple[str, str]:
    """A least-privilege StarRocks account for live runs: SELECT on NOVA_BENCH only.

    Nova refuses ``root`` as a data identity, so the agent runs as this user.
    The password is random per run and lives only in memory.
    """
    import secrets

    password = secrets.token_urlsafe(24)
    for statement in (
        f"CREATE ROLE IF NOT EXISTS {BENCH_ROLE}",
        f"CREATE USER IF NOT EXISTS '{BENCH_USER}'@'%' IDENTIFIED BY '{password}'",
        f"ALTER USER '{BENCH_USER}'@'%' IDENTIFIED BY '{password}'",
        f"GRANT SELECT ON ALL TABLES IN DATABASE NOVA_BENCH TO ROLE {BENCH_ROLE}",
        f"GRANT {BENCH_ROLE} TO USER '{BENCH_USER}'@'%'",
        f"SET DEFAULT ROLE {BENCH_ROLE} TO '{BENCH_USER}'@'%'",
    ):
        await execute(statement)
    await _grant_ranger_read()
    return BENCH_USER, password


async def _grant_ranger_read() -> None:
    """Column-level Ranger authorization needs a policy, not only a SQL GRANT."""
    from app.modules.access_control.security_context import SecurityContext
    from app.modules.access_control.service import access_control_service

    admin = SecurityContext(principal="nova_admin", active_role="ACCOUNTADMIN")
    try:
        await access_control_service.grant_access(
            admin, role=BENCH_ROLE, catalog="default_catalog", database="NOVA_BENCH",
            table="*", accesses=["select"],
        )
    except Exception as exc:  # noqa: BLE001 - a stack without Ranger needs only the GRANT
        print(f"Ranger grant skipped: {type(exc).__name__}", file=sys.stderr)


def rows_contain(got: list[tuple[str, ...]], want: list[tuple[str, ...]]) -> bool:
    """Same rows, allowing extra columns (a date bucket, a rank) in the answer's table."""
    from collections import Counter

    if not want or len(got) != len(want):
        return False
    remaining = [Counter(row) for row in got]
    for row in want:
        needed = Counter(row)
        index = next(
            (position for position, cells in enumerate(remaining)
             if all(cells[key] >= count for key, count in needed.items())),
            None,
        )
        if index is None:
            return False
        remaining.pop(index)
    return True


def expected_answer_value(spec: dict[str, Any], rows: list[list[Any]]) -> Any:
    """The derived number a correct answer states, from gold rows (label, value)."""
    from decimal import Decimal

    values = {}
    for row in rows:
        labels = [cell for cell in row if isinstance(cell, str)]
        numbers = [Decimal(str(cell)) for cell in row if not isinstance(cell, str)
                   and cell is not None]
        if labels and numbers:
            values[labels[0]] = numbers[-1]
    kind = spec.get("kind")
    try:
        if kind == "pct_change":
            before, after = values[spec["from"]], values[spec["to"]]
            return (after - before) / abs(before) * 100
        if kind == "difference":
            return abs(values[spec["a"]] - values[spec["b"]])
        if kind == "share":
            return values[spec["label"]] / sum(values.values()) * 100
    except (KeyError, ArithmeticError):
        return None
    return None


def text_states_value(text: str, expected: Any, *, percent: bool) -> bool:
    """True when some number in ``text``, at its own display precision, equals ``expected``."""
    from decimal import Decimal

    from app.modules.assistant.answer_contract import (
        _NUMBER,
        _candidates,
        _display_matches,
        _mask_dates,
        _percent_values,
    )

    target = abs(Decimal(str(expected)))
    for match in _NUMBER.finditer(_mask_dates(text)):
        literal = match.group(1).strip()
        if literal.endswith("%") != percent:
            continue
        shown = _percent_values(literal) if percent else _candidates(literal.rstrip("% "))
        if shown and _display_matches(target, {abs(value) for value in shown}, literal):
            return True
    return False


def _timing(steps: list[dict[str, Any]]) -> dict[str, float]:
    providers = [step for step in steps if step.get("kind") == "provider"]
    tools = [step for step in steps if step.get("kind") == "tool"]
    first = min((step.get("started_offset_ms") or 0 for step in providers), default=0.0)
    return {
        "before_loop_ms": round(float(first), 1),
        "loop_model_ms": round(sum(float(step.get("duration_ms") or 0) for step in providers), 1),
        "tool_ms": round(sum(float(step.get("duration_ms") or 0) for step in tools), 1),
        "loop_model_calls": len(providers),
    }


def _is_infrastructure(error: str) -> bool:
    return any(marker in error for marker in (
        "provider_error", "provider_unavailable", "hostname could not be resolved",
        "timed out", "Connection", "503", "502", "429",
    ))


def _states_limitation(text: str) -> bool:
    import re

    return bool(re.search(
        r"tidak (?:tersedia|ada|memiliki|dapat|bisa|tercakup)|belum (?:ada|tersedia)|"
        r"not (?:available|covered|included)|does not (?:have|include)|doesn't have|"
        r"no (?:\w+ ){0,3}(?:metric|data|column)|can(?:no|')t (?:answer|compute)|"
        r"outside (?:the|this|my) scope|di luar cakupan|can(?:no|')t|won't|will not|"
        r"not (?:allowed|permitted)|tidak (?:diizinkan|boleh)|refus",
        text, re.I,
    ))


def _looks_like_answer(text: str) -> bool:
    import re

    return bool(re.search(r"\d", text))


# ── report ───────────────────────────────────────────────────────────────────

def summarize(results: list[CaseResult]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    by_level: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        by_level[result.level].append(result)
    for level, items in by_level.items():
        scored = [item for item in items if not item.skipped]
        categories: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "total": 0})
        for item in scored:
            categories[item.category]["total"] += 1
            categories[item.category]["passed"] += int(item.passed)
        latencies = sorted(item.latency_ms for item in scored if item.latency_ms is not None)
        summary[level] = {
            "accuracy": round(sum(item.passed for item in scored) / max(len(scored), 1), 4),
            "cases": len(scored),
            "skipped": len(items) - len(scored),
            "silent_wrong": sum(item.silent_wrong for item in scored),
            "categories": {
                name: {**value, "accuracy": round(value["passed"] / value["total"], 4)}
                for name, value in sorted(categories.items())
            },
            "p50_ms": latencies[len(latencies) // 2] if latencies else None,
            **_consistency(scored),
            "mean_timing_ms": {
                key: round(sum(item.timing.get(key, 0) for item in scored) / max(len(scored), 1), 1)
                for key in ("before_loop_ms", "loop_model_ms", "tool_ms", "loop_model_calls")
            } if any(item.timing for item in scored) else None,
            "p95_ms": latencies[int(len(latencies) * 0.95)] if latencies else None,
        }
    return summary


def _consistency(scored: list[CaseResult]) -> dict[str, Any]:
    """Share of cases that passed every repetition, and the ones that did not."""
    runs: dict[str, list[bool]] = defaultdict(list)
    for item in scored:
        runs[item.id].append(item.passed)
    if not runs or all(len(values) == 1 for values in runs.values()):
        return {}
    flaky = sorted(case for case, values in runs.items() if any(values) and not all(values))
    stable = sum(all(values) for values in runs.values())
    return {"consistency": round(stable / len(runs), 4), "flaky": flaky}


def gate(summary: dict[str, Any], baseline: dict[str, Any] | None) -> list[str]:
    failures = []
    for level, values in summary.items():
        if values["silent_wrong"]:
            failures.append(f"{level}: silent_wrong={values['silent_wrong']}")
        previous = (baseline or {}).get(level)
        if previous and values["accuracy"] < previous["accuracy"] - 0.02:
            failures.append(
                f"{level}: accuracy {values['accuracy']} < baseline {previous['accuracy']} - 0.02"
            )
    return failures


async def _engine_execute():
    from app.core.database import db

    await db.init_system_pool()
    return db.execute_system, db.close_system_pool


async def main_async(args: argparse.Namespace) -> int:
    today = date.today()
    model = bench_model()
    cases = all_cases(today)
    if args.category:
        cases = [case for case in cases if case.category in args.category]
    if args.case:
        cases = [case for case in cases if case.id in args.case]
    if args.per_category:
        # A stratified sample keeps live runs affordable while covering every category.
        taken: dict[str, int] = defaultdict(int)
        sample = []
        for case in cases:
            if taken[case.category] < args.per_category:
                taken[case.category] += 1
                sample.append(case)
        cases = sample
    results = evaluate_offline(cases, model)
    close = None
    if args.engine or args.live:
        execute, close = await _engine_execute()
        try:
            if args.load:
                from tests.benchmark.studio_accuracy.dataset import load

                await load(execute, today)
            if args.engine:
                results += await evaluate_engine(cases, model, today, execute)
            if args.live:
                if os.environ.get("NOVA_LIVE_ASSISTANT_TEST") != "1":
                    print("--live needs NOVA_LIVE_ASSISTANT_TEST=1", file=sys.stderr)
                    return 2
                results += await evaluate_live(cases, model, today, execute,
                                               subset=args.subset, repeat=args.repeat)
        finally:
            await close()
    summary = summarize(results)
    report = {
        "date": today.isoformat(),
        "phase": CURRENT_PHASE,
        "summary": summary,
        "failures": [asdict(item) for item in results if not item.passed and not item.skipped],
    }
    baseline = json.loads(Path(args.baseline).read_text()) if args.baseline else None
    failures = gate(summary, (baseline or {}).get("summary"))
    report["gate_failures"] = failures
    if args.write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / f"{today.isoformat()}.json").write_text(json.dumps(report, indent=2))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for level, values in summary.items():
            print(f"{level}: accuracy={values['accuracy']} cases={values['cases']} "
                  f"silent_wrong={values['silent_wrong']} skipped={values['skipped']}")
            for name, value in values["categories"].items():
                print(f"    {name:16} {value['passed']:>3}/{value['total']:<3} "
                      f"{value['accuracy']:.2f}")
        for item in report["failures"][: args.show]:
            print(f"FAIL {item['level']} {item['id']}: {item['detail']}")
        for failure in failures:
            print(f"GATE {failure}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Studio accuracy benchmark")
    parser.add_argument("--engine", action="store_true", help="L2: execute on StarRocks")
    parser.add_argument("--live", action="store_true", help="L3: run the agent with a model")
    parser.add_argument("--load", action="store_true", help="(re)build NOVA_BENCH first")
    parser.add_argument("--subset", type=int, default=None)
    parser.add_argument("--per-category", type=int, default=None)
    parser.add_argument("--repeat", type=int, default=1, help="L3: runs per case")
    parser.add_argument("--category", action="append")
    parser.add_argument("--case", action="append")
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--write", action="store_true", help="write docs/benchmarks report")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show", type=int, default=40)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
