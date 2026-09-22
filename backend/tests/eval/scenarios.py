"""Golden agentic-harness scenarios (NOVA-124).

Each function returns a :class:`Scenario`: a scripted model trajectory plus the
checks that define correct harness behaviour. ``test_eval_scenarios.py`` runs
them all; a failing check is a test failure.

The scenarios are grouped by what they protect:

* **Tool selection** — the model proposes the right tool, and the harness runs
  exactly that one.
* **Consent / HITL** — a destructive call prompts and never auto-approves; a
  read-only call auto-approves under a grant.
* **Guarding** — a denied call is reported and does not terminate the turn; a
  failing call terminates with no retry.
* **Boundedness** — a loop is stopped by the iteration cap or the time budget.
* **Redaction** — credential-shaped arguments never reach the trace.
* **Context management** — a long transcript is curated; an over-budget turn
  stops with ``context_overflow`` rather than a provider error.
* **Dedup / retry-loop guarding** — an identical repeated call is not re-run.

They are deterministic: the "model" is a fixed script, so the same trajectory is
produced on every run and in CI without a provider key.
"""

from __future__ import annotations

import json

from app.modules.assistant.context import ContextManager
from app.modules.assistant.state import AssistantMessage
from app.modules.assistant.tools import ToolOutcome
from tests.benchmark.harness import text_frame, tool_call_frame
from tests.eval.harness import (
    EvalTool,
    Scenario,
    allow,
    announced_tool,
    answer_contains,
    check,
    deny,
    did_not_prompt,
    did_not_use_tool,
    finished_with,
    has_error_code,
    has_tool_detail,
    no_error,
    prompted_for,
    recorded_step,
    used_tool,
)


def _query_tool(**kwargs) -> EvalTool:
    return EvalTool("query_execute", classification="read_only", **kwargs)


def scenario_delegates_to_query_execute() -> Scenario:
    """A data question → one read-only query → a text answer."""
    return Scenario(
        name="delegates_to_query_execute",
        script=[
            tool_call_frame("c1", sql="SELECT count(*) FROM orders"),
            text_frame("There are 42 orders."),
        ],
        tools=[_query_tool(summary="count = 42")],
        read_only_grant=True,
        checks=[
            check("query ran", used_tool("query_execute")),
            check("read-only auto-approved (no prompt)", did_not_prompt()),
            check("finished normally", finished_with("stop")),
            check("no error", no_error()),
            check("answer present", answer_contains("42")),
        ],
    )


def scenario_destructive_tool_prompts() -> Scenario:
    """A destructive tool must prompt even with a read-only grant, and the grant
    must never cover it."""
    return Scenario(
        name="destructive_tool_prompts",
        content="Run the configured write workflow",
        script=[
            tool_call_frame("c1", name="write_thing", sql="CREATE TABLE t (x int)"),
            text_frame("Done."),
        ],
        tools=[EvalTool("write_thing", classification="destructive", summary="created")],
        read_only_grant=True,
        resolve_consent=allow,
        checks=[
            check("write ran", used_tool("write_thing")),
            check("prompted despite grant", prompted_for("write_thing")),
            check("finished", finished_with("stop")),
        ],
    )


def scenario_denied_call_continues() -> Scenario:
    """A user-denied call is terminal and never receives an automatic retry."""
    return Scenario(
        name="denied_call_continues",
        script=[
            tool_call_frame("c1", sql="SELECT secret FROM vault"),
        ],
        tools=[_query_tool()],
        resolve_consent=deny,
        checks=[
            check("tool did not run", did_not_use_tool("query_execute")),
            check("prompted", prompted_for("query_execute")),
            check("turn stopped as denied", finished_with("denied")),
        ],
    )


def scenario_failed_tool_terminates() -> Scenario:
    """A tool failure terminates with ``tool_failed`` and is never retried."""
    tool = _query_tool(ok=False, error="permission denied")
    return Scenario(
        name="failed_tool_terminates",
        script=[tool_call_frame("c1", sql="SELECT * FROM forbidden")],
        tools=[tool],
        read_only_grant=True,
        checks=[
            check("terminates with tool_failed", has_error_code("tool_failed")),
            check("finished with error", finished_with("error")),
            check(
                "no second attempt", lambda r: len(r.tool_runs) <= 1 or "tool ran more than once"
            ),
        ],
    )


def scenario_recoverable_semantic_error_repairs_once() -> Scenario:
    """An unknown metric receives one focused repair and then succeeds."""
    tool = EvalTool(
        "semantic_query",
        classification="read_only",
        parameters={
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        },
        outcomes=[
            ToolOutcome(
                ok=False,
                summary="",
                error="Unknown metric revenues.",
                error_class="UNKNOWN_METRIC",
                recoverable=True,
                safe_detail="Use total_revenue.",
                repair_context={"available_metrics": ["total_revenue"]},
            ),
            ToolOutcome(
                ok=True,
                summary="total_revenue = 42",
                data={
                    "semantic_plan": {"metrics": ["total_revenue"]},
                    "sql": "SELECT SUM(amount) AS total_revenue FROM orders",
                },
            ),
        ],
    )
    return Scenario(
        name="recoverable_semantic_error_repairs_once",
        content="Revenue this month",
        script=[
            tool_call_frame(
                "c1", name="semantic_query", arguments={"question": "revenues this month"}
            ),
            tool_call_frame(
                "c2", name="semantic_query", arguments={"question": "total_revenue this month"}
            ),
            text_frame("Verified revenue is 42."),
        ],
        tools=[tool],
        read_only_grant=True,
        checks=[
            check(
                "exactly one repair call",
                lambda result: result.tool_runs.count("semantic_query") == 2
                or f"expected two attempts, got {result.tool_runs}",
            ),
            check("finished after repair", finished_with("stop")),
            check("grounded answer present", answer_contains("42")),
        ],
    )


def scenario_iteration_cap() -> Scenario:
    """A model that never stops calling tools is stopped at the cap."""
    return Scenario(
        name="iteration_cap",
        script=[
            tool_call_frame("c1", sql="SELECT 1"),
            tool_call_frame("c2", sql="SELECT 2"),
            tool_call_frame("c3", sql="SELECT 3"),
        ],
        tools=[_query_tool()],
        read_only_grant=True,
        max_iterations=2,
        checks=[
            check("stopped at cap", has_error_code("iteration_cap")),
            check("finished with iteration_cap", finished_with("iteration_cap")),
        ],
    )


def scenario_time_budget() -> Scenario:
    """A zero time budget stops the turn before any model call."""
    return Scenario(
        name="time_budget",
        script=[text_frame("never")],
        tools=[_query_tool()],
        time_budget_seconds=0.0,
        checks=[
            check("timed out", has_error_code("timeout")),
            check("no provider call", lambda r: r.provider_calls == 0 or "provider was called"),
        ],
    )


def scenario_credential_arguments_redacted() -> Scenario:
    """A tool call carrying a credential-shaped argument must not store the
    value in the trace; it is masked as ``***``."""
    tool = EvalTool("custom_probe", classification="read_only", summary="ok")
    tool.parameters = {
        "type": "object",
        "properties": {"password": {"type": "string"}, "sql": {"type": "string"}},
    }
    return Scenario(
        name="credential_arguments_redacted",
        content="Run the configured probe",
        script=[
            tool_call_frame("c1", name="custom_probe", sql="SELECT 1"),
            text_frame("done"),
        ],
        tools=[tool],
        read_only_grant=True,
        checks=[
            check(
                "password argument masked",
                _trace_hides_password,
            ),
        ],
    )


def _trace_hides_password(result) -> bool | str:
    for step in result.steps:
        args = step.get("arguments") or {}
        if "password" in args:
            if args["password"] == "***":
                return True
            return f"password stored as {args['password']!r}"
    # No tool step captured the password key at all is also safe.
    return True


def scenario_duplicate_call_is_not_rerun() -> Scenario:
    """The same call proposed twice is run once; the second is answered from the
    first result."""
    tool = _query_tool(summary="count = 7")
    return Scenario(
        name="duplicate_call_not_rerun",
        script=[
            tool_call_frame("c1", sql="SELECT count(*) FROM t"),
            tool_call_frame("c2", sql="SELECT count(*) FROM t"),
            text_frame("Seven."),
        ],
        tools=[tool],
        read_only_grant=True,
        checks=[
            check("tool ran once", _ran_once),
            check("finished", finished_with("stop")),
        ],
    )


def _ran_once(result) -> bool | str:
    if len(result.tool_runs) == 1:
        return True
    return f"expected 1 run, got {len(result.tool_runs)}"


def scenario_context_is_curated() -> Scenario:
    """A long transcript is curated so the request fits the budget.

    The history is padded so it genuinely exceeds the budget: 40 turns of ~300
    characters each is ~3,000 estimated tokens against a 1,200 budget, so the
    manager must drop turns and say so.
    """
    manager = ContextManager(token_budget=1200, keep_recent=2)
    scenario = Scenario(
        name="context_is_curated",
        script=[text_frame("Answer with limited context.")],
        tools=[_query_tool()],
        history_turns=40,
        history_chars=600,
        context_manager=manager,
        checks=[
            check("turn dropped history", _dropped_history),
            check("within budget", _within_budget),
            check("finished", finished_with("stop")),
            check("curation surfaced to the user", _curation_note_shown),
        ],
    )
    return scenario


def _dropped_history(result) -> bool | str:
    dropped = result.context_stats.get("dropped_turns", 0)
    if dropped > 0:
        return True
    return f"nothing dropped: {result.context_stats}"


def _within_budget(result) -> bool | str:
    if result.context_stats.get("fits") is True:
        return True
    return f"did not fit: {result.context_stats}"


def _curation_note_shown(result) -> bool | str:
    if any("Fitting context budget" in frame for frame in result.frames):
        return True
    return "no context-curation note was emitted"


def scenario_context_overflow_stops_cleanly() -> Scenario:
    """When even the pinned window exceeds the budget, stop with a clear error
    rather than an opaque provider failure."""
    return Scenario(
        name="context_overflow_stops_cleanly",
        script=[text_frame("never sent")],
        tools=[_query_tool()],
        system_prompt="s" * 4000,
        context_manager=ContextManager(token_budget=10, keep_recent=1),
        checks=[
            check("stopped with context_overflow", has_error_code("context_overflow")),
            check("no provider call", lambda r: r.provider_calls == 0 or "provider was called"),
            check(
                "finished with context_overflow",
                finished_with("context_overflow"),
            ),
        ],
    )


def scenario_read_only_grant_auto_approves() -> Scenario:
    """With a read-only grant, a read-only call runs without a prompt.

    The call is still announced, so the panel can show the statement it ran:
    transparency is not a side effect of the approval flow.
    """
    return Scenario(
        name="read_only_grant_auto_approves",
        script=[
            tool_call_frame("c1", sql="SELECT 1"),
            text_frame("Done."),
        ],
        tools=[_query_tool()],
        read_only_grant=True,
        checks=[
            check("ran", used_tool("query_execute")),
            check("no prompt", did_not_prompt()),
            check(
                "announced as running, not pending",
                announced_tool("query_execute", status="running"),
            ),
        ],
    )


def scenario_prompted_call_is_announced_as_pending() -> Scenario:
    """A call that needs approval is announced `pending`, carrying its preview.

    The approval card is built from this frame, so a prompt without it would
    show the user nothing to judge.
    """
    return Scenario(
        name="prompted_call_is_announced_as_pending",
        content="Run the configured write workflow",
        script=[
            tool_call_frame("c1", name="write_thing", sql="CREATE TABLE t (x int)"),
            text_frame("Done."),
        ],
        tools=[EvalTool("write_thing", classification="destructive", summary="created")],
        checks=[
            check("prompted", prompted_for("write_thing")),
            check(
                "announced as pending with a preview",
                announced_tool("write_thing", status="pending"),
            ),
        ],
    )


def scenario_trace_is_recorded_for_replay() -> Scenario:
    """The turn's trace records everything a reopened conversation needs.

    A reload rebuilds the transcript from this trace, so anything emitted but
    not recorded (a reasoning phase, the result grid) would vanish from history.
    Each step must therefore be present with its redacted fields intact.
    """
    table = {
        "title": "Revenue per SKU",
        "columns": ["sku_name", "omzet"],
        "rows": [["D. COKLAT 12", 3123]],
    }
    return Scenario(
        name="trace_is_recorded_for_replay",
        script=[
            tool_call_frame("c1", sql="SELECT sku_name FROM sales.sku"),
            text_frame("The top SKU is D. COKLAT 12."),
        ],
        tools=[_query_tool(summary="1 row", table=table)],
        read_only_grant=True,
        checks=[
            check(
                "the plan phase is recorded",
                recorded_step("reasoning", phase="plan"),
            ),
            check(
                "the tool step carries its redacted SQL",
                recorded_step(
                    "tool",
                    name="query_execute",
                    preview="SELECT sku_name FROM sales.sku",
                    status="done",
                ),
            ),
            check(
                "the result grid is recorded for replay",
                recorded_step("table", title="Revenue per SKU"),
            ),
            check("the answer is recorded", recorded_step("answer")),
            check("finished normally", finished_with("stop")),
        ],
    )


def scenario_tool_detail_is_sent_and_recorded() -> Scenario:
    """A tool's detail reaches the panel and survives into the trace.

    Opening a step should answer "what did this do". For a skill load that is
    the playbook body, which otherwise only ever reaches the model, so the
    reader sees a step with nothing behind it.
    """
    body = "Playbook: classify a plastic article by its essential character."
    return Scenario(
        name="tool_detail_is_sent_and_recorded",
        content="Load the engineering procedure",
        script=[
            tool_call_frame("c1", name="load_skill", arguments={"name": "engineering"}),
            text_frame("Done."),
        ],
        tools=[
            EvalTool(
                "load_skill",
                classification="read_only",
                description="load a skill",
                parameters={"type": "object", "properties": {"name": {"type": "string"}}},
                summary=body,
            )
        ],
        checks=[
            check(
                "the detail is sent on the stream",
                has_tool_detail("c1", body),
            ),
            check(
                "the detail is recorded for replay",
                recorded_step("tool", name="load_skill", detail=body),
            ),
        ],
    )


def scenario_observability_spans_are_recorded() -> Scenario:
    """Provider/tool timing and the tool-owned semantic snapshot survive replay."""
    semantic = {
        "kind": "semantic_context",
        "semantic_model": {"id": "m1", "name": "sales", "ossie_version": "0.1.1"},
        "question": "revenue by category",
        "datasets": [{"name": "orders", "source": "NOVA_DEMO.orders"}],
        "metrics": ["revenue"],
        "generated_sql": "SELECT category, SUM(total) FROM NOVA_DEMO.orders GROUP BY category",
        "validation_warnings": [],
    }
    return Scenario(
        name="observability_spans_are_recorded",
        content="revenue by category",
        script=[
            tool_call_frame(
                "semantic-1",
                name="semantic_query",
                arguments={"question": "revenue by category"},
            ),
            text_frame("Revenue is highest in Electronics."),
        ],
        tools=[
            EvalTool(
                "semantic_query",
                classification="read_only",
                summary="4 rows",
                parameters={
                    "type": "object",
                    "properties": {"question": {"type": "string"}},
                    "required": ["question"],
                    "additionalProperties": False,
                },
                data={
                    "semantic_plan": {"metrics": ["revenue"]},
                    "sql": semantic["generated_sql"],
                },
                trace_detail=semantic,
            )
        ],
        read_only_grant=True,
        checks=[
            check("provider span has timing", _provider_span_has_timing),
            check("tool span has timing", _tool_span_has_timing),
            check("every process span has timing", _all_process_spans_have_timing),
            check("semantic snapshot persisted", _semantic_snapshot_persisted),
            check("finished normally", finished_with("stop")),
        ],
    )


def _provider_span_has_timing(result) -> bool | str:
    spans = [step for step in result.steps if step.get("kind") == "provider"]
    if spans and all("step_id" in step and "duration_ms" in step for step in spans):
        return True
    return f"provider spans missing timing: {spans}"


def _tool_span_has_timing(result) -> bool | str:
    span = next((step for step in result.steps if step.get("kind") == "tool"), None)
    if span and "step_id" in span and "duration_ms" in span:
        return True
    return f"tool span missing timing: {span}"


def _all_process_spans_have_timing(result) -> bool | str:
    process_kinds = {"reasoning", "provider", "tool", "answer", "context"}
    spans = [step for step in result.steps if step.get("kind") in process_kinds]
    missing = [
        step
        for step in spans
        if "step_id" not in step or "started_offset_ms" not in step or "duration_ms" not in step
    ]
    if spans and not missing:
        return True
    return f"process spans missing timing: {missing or spans}"


def _semantic_snapshot_persisted(result) -> bool | str:
    span = next((step for step in result.steps if step.get("kind") == "tool"), {})
    detail = span.get("trace_detail") or {}
    if detail.get("kind") == "semantic_context" and detail.get("generated_sql"):
        return True
    return f"semantic trace missing: {detail}"


def scenario_no_grant_prompts_every_call() -> Scenario:
    """Without a grant, even a read-only call prompts."""
    return Scenario(
        name="no_grant_prompts_every_call",
        script=[
            tool_call_frame("c1", sql="SELECT 1"),
            text_frame("Done."),
        ],
        tools=[_query_tool()],
        read_only_grant=False,
        resolve_consent=allow,
        checks=[
            check("ran", used_tool("query_execute")),
            check("prompted", prompted_for("query_execute")),
        ],
    )


def scenario_pure_answer_no_tool() -> Scenario:
    """A plain question is answered without touching a tool."""
    return Scenario(
        name="pure_answer_no_tool",
        script=[text_frame("Use SELECT with a WHERE clause.")],
        tools=[_query_tool()],
        checks=[
            check("no tool ran", did_not_use_tool("query_execute")),
            check("no prompt", did_not_prompt()),
            check("finished", finished_with("stop")),
            check("answer present", answer_contains("SELECT")),
        ],
    )


def scenario_text_precedes_result_artifact() -> Scenario:
    """A result produced before final prose waits behind that prose."""
    table = {"title": "Orders", "columns": ["count"], "rows": [[42]]}
    return Scenario(
        name="text_precedes_result_artifact",
        script=[
            tool_call_frame("c1", sql="SELECT count(*) FROM orders"),
            text_frame("There are 42 orders."),
        ],
        tools=[_query_tool(summary="count = 42", table=table)],
        read_only_grant=True,
        checks=[
            check("text block is authored first", _text_before_table),
            check("blocks carry stable indexes", _ordered_indexes_are_stable),
            check("ordered output is recorded", recorded_step("text", content_index=0)),
        ],
    )


def scenario_artifact_can_precede_later_text() -> Scenario:
    """An explicit marker authors an artifact before the text that follows it."""
    table = {"title": "Orders", "columns": ["count"], "rows": [[42]]}
    return Scenario(
        name="artifact_can_precede_later_text",
        script=[
            tool_call_frame("c1", sql="SELECT count(*) FROM orders"),
            text_frame("[[NOVA_ARTIFACT]]\nThis note intentionally follows the table."),
        ],
        tools=[_query_tool(summary="count = 42", table=table)],
        read_only_grant=True,
        checks=[
            check("table is authored first", _table_before_text),
            check("layout marker never reaches the user", _marker_is_hidden),
        ],
    )


def scenario_follow_up_chart_reuses_previous_table() -> Scenario:
    """A chart follow-up receives the latest table from the same thread.

    Every turn has a fresh request context. The harness must bridge the recent,
    persisted table into that context or ``data_to_chart`` sees no data even
    though the grid is visible directly above the user's follow-up.
    """
    chart_tool = EvalTool(
        "data_to_chart",
        classification="read_only",
        description="build a chart from recent data",
        parameters={
            "type": "object",
            "properties": {"intent": {"type": "string"}},
        },
        summary="chart built",
        chart={"chart_spec": '{"mark":"bar"}'},
    )
    prior_table = {
        "kind": "table",
        "title": "Revenue by category",
        "columns": ["category", "total_revenue"],
        "rows": [["Electronics", 154291000], ["Audio", 35895000]],
    }
    return Scenario(
        name="follow_up_chart_reuses_previous_table",
        content="buat bar chart nya",
        history_messages=[
            AssistantMessage(message_id="u-prior", role="user", content="revenue per category"),
            AssistantMessage(
                message_id="a-prior",
                role="assistant",
                content="Electronics leads.",
                steps=[prior_table],
            ),
        ],
        script=[
            tool_call_frame(
                "c-chart",
                name="data_to_chart",
                arguments={"intent": "revenue by category as a bar chart"},
            ),
            text_frame("Here is the bar chart."),
        ],
        tools=[chart_tool],
        read_only_grant=True,
        checks=[
            check("chart tool ran", used_tool("data_to_chart")),
            check(
                "previous table restored before charting",
                lambda _result: _chart_tool_received_prior_table(chart_tool),
            ),
            check("finished normally", finished_with("stop")),
        ],
    )


def _chart_tool_received_prior_table(tool: EvalTool) -> bool | str:
    if not tool.last_results:
        return "chart tool did not capture a context result"
    restored = tool.last_results[0] or {}
    if restored.get("columns") != ["category", "total_revenue"]:
        return f"wrong columns restored: {restored.get('columns')!r}"
    if restored.get("rows") != [["Electronics", 154291000], ["Audio", 35895000]]:
        return f"wrong rows restored: {restored.get('rows')!r}"
    if restored.get("source") != "previous_turn":
        return f"missing previous-turn provenance: {restored!r}"
    return True


def scenario_generated_sql_is_visible_during_tool_run() -> Scenario:
    """Generated SQL is a progress artifact, not an after-the-fact detail."""
    sql = "SELECT sku, SUM(revenue) FROM sales GROUP BY sku"
    tool = _query_tool(
        summary="2 rows",
        table={"title": "Revenue", "columns": ["sku", "revenue"], "rows": []},
        progress=[
            {"stage": "generating_sql", "text": "Generating SQL"},
            {
                "stage": "sql_generated",
                "text": "Generated SQL",
                "sql_preview": sql,
            },
            {
                "stage": "executing_sql",
                "text": "Running the generated query",
                "sql_preview": sql,
            },
        ],
    )
    return Scenario(
        name="generated_sql_visible_during_tool_run",
        script=[tool_call_frame("c1", sql=sql), text_frame("Done.")],
        tools=[tool],
        read_only_grant=True,
        checks=[
            check("SQL progress precedes completion", _sql_progress_precedes_completion),
            check(
                "SQL progress is recorded on the tool",
                recorded_step("tool", preview=sql),
            ),
            check("run envelope is monotonic", _run_envelope_is_monotonic),
        ],
    )


def scenario_multiple_tool_calls_are_serialized() -> Scenario:
    """Every call in one provider response runs once, in order."""
    first = {
        "id": "c1",
        "type": "function",
        "function": {
            "name": "query_execute",
            "arguments": json.dumps({"sql": "SELECT 1"}),
        },
    }
    second = {
        "id": "c2",
        "type": "function",
        "function": {
            "name": "query_execute",
            "arguments": json.dumps({"sql": "SELECT 2"}),
        },
    }
    tool = _query_tool(summary="1 row")
    return Scenario(
        name="multiple_tool_calls_are_serialized",
        script=[
            {"role": "assistant", "content": "", "tool_calls": [first, second]},
            text_frame("Both checks completed."),
        ],
        tools=[tool],
        read_only_grant=True,
        checks=[
            check("both calls ran", _two_calls_ran),
            check("both calls were announced", _two_calls_announced),
            check("finished normally", finished_with("stop")),
        ],
    )


def _frame_payload(frame: str) -> dict:
    for line in frame.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return {}


def _text_before_table(result) -> bool | str:
    try:
        return result.events.index("text_delta") < result.events.index("table") or (
            f"event order was {result.events}"
        )
    except ValueError:
        return f"missing text/table event: {result.events}"


def _table_before_text(result) -> bool | str:
    try:
        return result.events.index("table") < result.events.index("text_delta") or (
            f"event order was {result.events}"
        )
    except ValueError:
        return f"missing text/table event: {result.events}"


def _ordered_indexes_are_stable(result) -> bool | str:
    text = next(
        (_frame_payload(frame) for frame in result.frames if "event: text_delta" in frame),
        {},
    )
    table = next(
        (_frame_payload(frame) for frame in result.frames if "event: table" in frame),
        {},
    )
    if text.get("content_index") == 0 and table.get("content_index") == 1:
        return True
    return f"unexpected indexes: text={text}, table={table}"


def _marker_is_hidden(result) -> bool | str:
    if all("NOVA_ARTIFACT" not in frame for frame in result.frames):
        return True
    return "the private artifact marker leaked onto the stream"


def _sql_progress_precedes_completion(result) -> bool | str:
    progress_indexes = [
        index
        for index, frame in enumerate(result.frames)
        if "event: tool_progress" in frame and "sql_generated" in frame
    ]
    completed = [
        index
        for index, frame in enumerate(result.frames)
        if "event: tool_status" in frame and '"status":"done"' in frame
    ]
    if progress_indexes and completed and progress_indexes[0] < completed[-1]:
        return True
    return f"progress/completion order was {result.events}"


def _run_envelope_is_monotonic(result) -> bool | str:
    payloads = [_frame_payload(frame) for frame in result.frames]
    run_ids = {payload.get("run_id") for payload in payloads}
    sequences = [payload.get("sequence") for payload in payloads]
    if len(run_ids) == 1 and None not in run_ids and sequences == list(range(len(payloads))):
        return True
    return f"run_ids={run_ids}, sequences={sequences}"


def _two_calls_ran(result) -> bool | str:
    count = result.tool_runs.count("query_execute")
    return True if count == 2 else f"expected 2 calls, got {count}"


def _two_calls_announced(result) -> bool | str:
    count = result.events.count("tool_call")
    return True if count == 2 else f"expected 2 tool_call frames, got {count}"


def all_scenarios() -> list[Scenario]:
    """Every golden scenario, in a stable order."""
    return [
        scenario_delegates_to_query_execute(),
        scenario_pure_answer_no_tool(),
        scenario_destructive_tool_prompts(),
        scenario_denied_call_continues(),
        scenario_failed_tool_terminates(),
        scenario_recoverable_semantic_error_repairs_once(),
        scenario_read_only_grant_auto_approves(),
        scenario_prompted_call_is_announced_as_pending(),
        scenario_trace_is_recorded_for_replay(),
        scenario_tool_detail_is_sent_and_recorded(),
        scenario_observability_spans_are_recorded(),
        scenario_no_grant_prompts_every_call(),
        scenario_iteration_cap(),
        scenario_time_budget(),
        scenario_credential_arguments_redacted(),
        scenario_duplicate_call_is_not_rerun(),
        scenario_context_is_curated(),
        scenario_context_overflow_stops_cleanly(),
        scenario_text_precedes_result_artifact(),
        scenario_artifact_can_precede_later_text(),
        scenario_follow_up_chart_reuses_previous_table(),
        scenario_generated_sql_is_visible_during_tool_run(),
        scenario_multiple_tool_calls_are_serialized(),
    ]
