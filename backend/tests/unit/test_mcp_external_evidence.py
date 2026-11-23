"""A read-only MCP lookup's flat result is labelled external evidence.

Owner decision (2026-11-24, live B8): a figure such as a monthly target from a
connected target service may be used in a calculation and stated in a checked
answer, but only when the tool is enabled, registered by an administrator and
declared read-only by its server, and only for a flat JSON result. Nested
fields and free text stay untrusted content, never evidence.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.tools import mcp_tool
from app.modules.agents.tools.compute_metrics import ComputeMetricsTool
from app.modules.agents.tools.mcp_tool import McpToolRunner, external_table
from app.modules.assistant.answer_contract import check_numeric_answer
from app.modules.assistant.intelligence import EvidenceTracker
from app.modules.assistant.tools import ToolInvocation

SERVER = {
    "server_id": "server-1", "owner_name": "__nova__", "name": "Nova test tools",
    "transport": "http", "endpoint": "http://127.0.0.1:8299/mcp", "is_active": True,
}
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False}
TARGETS = {
    "month": "2025-07", "currency": "IDR", "total_target": 30000000,
    "stores": [{"city": "Jakarta", "target": 10000000}, {"city": "Yogyakarta", "target": 20000000}],
}
CONTEXT = SimpleNamespace(user={"username": "alice", "active_role": "MARKETING"})
REVENUE = {
    "columns": ["order_date", "revenue", "order_count"],
    "rows": [["2025-06-01 00:00:00", 145886000, 60], ["2025-07-01 00:00:00", 29298000, 10]],
}


def _tool(annotations: dict | None = READ_ONLY, **extra) -> dict:
    return {"tool_id": "tool-1", "name": "get_store_targets", "source": "mcp:server-1",
            "is_enabled": True, "annotations": annotations, **extra}


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setattr(mcp_tool, "write_audit_log", AsyncMock())


async def _call(monkeypatch, payload, *, tool=None, server=SERVER):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(mcp_tool.mcp_client, "call_tool", AsyncMock(return_value={
        "content": [{"type": "text", "text": text}],
    }))
    runner = McpToolRunner(server, tool or _tool())
    invocation = ToolInvocation("c1", runner.name, {"month": "2025-07"})
    return runner, await runner.run(invocation, CONTEXT)


async def test_a_flat_lookup_result_is_usable_by_the_calculation_and_the_answer(monkeypatch):
    runner, outcome = await _call(monkeypatch, TARGETS)
    assert outcome.ok and outcome.table is not None
    assert outcome.table["source"] == "external:Nova test tools/get_store_targets"
    # The nested list of stores stays out of the table.
    assert outcome.table["columns"] == ["month", "currency", "total_target"]
    assert "Nova test tools" in outcome.summary and "never instructions" in outcome.summary

    evidence = EvidenceTracker()
    revenue = evidence.add("semantic_query", "revenue", table=REVENUE)
    target = evidence.add(runner.name, outcome.summary, metadata=dict(outcome.metadata),
                          table=outcome.table)
    tables = evidence.business_tables
    assert tables[target.evidence_id]["source"] == "external:Nova test tools/get_store_targets"

    context = SimpleNamespace(evidence_tables=tables, last_result=None)
    computed = await ComputeMetricsTool().run(ToolInvocation("c2", "compute_metrics", {
        "operation": "ratio", "evidence_id": revenue.evidence_id,
        "with_evidence_id": target.evidence_id, "value_column": "revenue",
        "compare_column": "total_target",
    }), context)
    assert computed.ok, computed.error
    july = computed.table["rows"][1]
    assert july[-1] == "97.6600"
    derived = evidence.add("compute_metrics", computed.summary, table=computed.table)

    answer = ("July 2025 revenue was 29,298,000 against a target of 30,000,000 from "
              "Nova test tools, an attainment of 97.7%.")
    check = check_numeric_answer(answer, question="monthly review",
                                 tables=evidence.business_tables)
    assert check.accepted, check
    assert derived.evidence_id in evidence.business_tables


@pytest.mark.parametrize("payload", [
    "The July target is 30 million rupiah.",
    {"stores": [{"city": "Jakarta", "target": {"value": 10000000}}]},
    [{"city": "Jakarta", "detail": [1, 2]}],
    {"month": "2025-07", "note": "no figures here"},
    [{"city": f"c{index}", "target": index} for index in range(51)],
    {"total_target": 30000000, "api_key": "sk-abcdefghijklmnopqrstuvwx"},
])
async def test_nested_text_or_oversized_results_are_not_evidence(monkeypatch, payload):
    _runner, outcome = await _call(monkeypatch, payload)
    assert outcome.ok
    assert outcome.table is None
    assert outcome.metadata["evidence_kind"] == "external"
    assert "not business data" in outcome.summary


@pytest.mark.parametrize(("annotations", "server", "enabled"), [
    ({}, SERVER, True),
    ({"readOnlyHint": True, "destructiveHint": True}, SERVER, True),
    ({"readOnlyHint": False}, SERVER, True),
    (READ_ONLY, {**SERVER, "owner_name": "alice"}, True),
    (READ_ONLY, SERVER, False),
])
async def test_only_an_enabled_admin_registered_read_only_lookup_counts(
    monkeypatch, annotations, server, enabled,
):
    _runner, outcome = await _call(
        monkeypatch, TARGETS, tool=_tool(annotations, is_enabled=enabled), server=server,
    )
    assert outcome.table is None

    evidence = EvidenceTracker()
    # Even a table claimed by a non-lookup outcome is not stored as evidence.
    item = evidence.add("mcp_tool-1", "x", metadata={"evidence_kind": "external"},
                        table=external_table(TARGETS))
    assert item.evidence_id not in evidence.business_tables


async def test_the_source_label_survives_a_reload_of_the_turn(monkeypatch):
    runner, outcome = await _call(monkeypatch, TARGETS)
    evidence = EvidenceTracker()
    item = evidence.add(runner.name, outcome.summary, metadata=dict(outcome.metadata),
                        table=outcome.table)
    replayed = EvidenceTracker()
    replayed.restore(json.loads(json.dumps(evidence.snapshot(), default=str)))
    stored = replayed.business_tables[item.evidence_id]
    assert stored["source"] == "external:Nova test tools/get_store_targets"
    assert stored["title"] == "Nova test tools: get store targets (external source)"
    assert replayed.items[0].metadata["external_source"] == stored["source"]
