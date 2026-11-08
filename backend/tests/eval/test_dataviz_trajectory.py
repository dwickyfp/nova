"""Dataviz requests through the real bounded loop (docs/arch-19-studio-dataviz.md).

* An unsupported result is explained, never planned or applied, and the turn
  ends normally: the resolver's verified refusal settles the add steps the
  planner required.
* A supported result is planned, the consent prompt shows the server's diff,
  and the change is applied once only after consent; a denial applies nothing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.agents.tools import dataviz
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from app.modules.superset.previews import Locator
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import TurnResult

USER = {
    "username": "alice",
    "active_role": "ANALYST",
    "roles": ["ANALYST"],
    "security_context_version": 3,
    "session_id": "s-alice",
}
PLAN = {
    "intent": "ui_operation",
    "tools": ["dataviz_list_results", "dataviz_check_result", "dataviz_plan_add", "dataviz_apply"],
    "required_tools": ["dataviz_check_result", "dataviz_plan_add", "dataviz_apply"],
    "skills": [],
    "ml_task": None,
}


class _Unused:
    """A governed tool the planner may require; these turns must not need it."""

    classification = "read_only"
    requires_consent = False
    parameters = {"type": "object", "properties": {}, "additionalProperties": True}

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = name

    def preview(self, invocation):
        return self.name

    async def run(self, invocation, context):
        raise AssertionError(f"{self.name} should not run")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    dataviz.register(registry)
    for name in ("semantic_query", "diagnose_change"):
        registry.register(_Unused(name))
    return registry


@pytest.fixture
def located(monkeypatch):
    async def locator(context, user, result_id):
        return Locator("t1", "m1", "content-3", {"component": "GeoMap", "evidence_id": "e2"}), {
            "result_id": result_id,
            "title": "Routes",
        }

    monkeypatch.setattr(dataviz, "_locator", locator)


async def _run(provider, consent, *, dashboard_id=None):
    context = LoopContext("alice", user=USER, thread_id="t1")
    context.dataviz = {"dashboard_id": dashboard_id, "edit_state": "clean"}
    return TurnResult(
        frames=[
            frame
            async for frame in AssistantLoop(
                provider=provider, registry=_registry(), system_prompt="test"
            ).run(
                thread=AssistantThread("t1", "alice", "Dataviz"),
                user_content="tambahkan peta tadi ke dashboard",
                context=context,
                resolve_consent=consent,
            )
        ]
    )


@pytest.mark.asyncio
async def test_an_unsupported_map_is_explained_and_nothing_is_planned(monkeypatch, located):
    import app.modules.superset.dataviz as service

    monkeypatch.setattr(
        service,
        "check_locator",
        AsyncMock(
            return_value={
                "status": "UNSUPPORTED",
                "reason_code": "COMPUTED_RESULT_NOT_QUERYABLE",
                "message": "computed by route_optimization",
                "can_add": False,
                "alternatives": [{"strategy_id": "requery.governed", "meaning": "partial"}],
            }
        ),
    )
    preview = AsyncMock()
    apply = AsyncMock()
    monkeypatch.setattr(service, "preview_change", preview)
    monkeypatch.setattr(service, "apply_change", apply)
    prompts: list[str] = []

    async def consent(invocation, classification):
        prompts.append(invocation.tool_name)
        return True

    provider = ScriptedProvider(
        [
            tool_call_frame("c1", name="dataviz_check_result", arguments={"result_id": "R3"}),
            text_frame(
                "Peta rute ini dihitung oleh optimasi rute, jadi belum bisa ditaruh di "
                "Dataviz. Mau saya ambil daftar pemberhentiannya sebagai tabel?"
            ),
        ],
        turn_plan=PLAN,
    )
    result = await _run(provider, consent)
    assert result.finish_reason == "stop"
    assert "required_capability_incomplete" not in "".join(result.frames)
    preview.assert_not_awaited()
    apply.assert_not_awaited()
    assert "dataviz_apply" not in prompts


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_a_supported_result_is_applied_once_only_after_consent(monkeypatch, located, allowed):
    import app.modules.superset.dataviz as service

    monkeypatch.setattr(
        service,
        "check_locator",
        AsyncMock(
            return_value={
                "status": "SUPPORTED",
                "reason_code": "OK",
                "message": "Ready",
                "can_add": True,
                "alternatives": [],
            }
        ),
    )
    monkeypatch.setattr(
        service,
        "preview_change",
        AsyncMock(
            return_value={
                "action_token": "tok-1",
                "changes": ["Add the chart “Units” to “Sales”."],
                "revision": "sha256:r1",
                "target": {"mode": "existing", "dashboard_id": 7},
            }
        ),
    )
    apply = AsyncMock(return_value={"status": "SUCCEEDED", "dashboard_id": 7, "chart_id": 9})
    monkeypatch.setattr(service, "apply_change", apply)
    previews: list[str] = []

    async def consent(invocation, classification):
        tool = _registry().get(invocation.tool_name)
        previews.append(tool.preview(invocation))
        assert classification == "session_change"
        return allowed

    provider = ScriptedProvider(
        [
            tool_call_frame("c1", name="dataviz_check_result", arguments={"result_id": "R1"}),
            tool_call_frame(
                "c2", name="dataviz_plan_add", arguments={"result_id": "R1", "dashboard": "current"}
            ),
            tool_call_frame("c3", name="dataviz_apply", arguments={"action_token": "tok-1"}),
            text_frame("Grafik sudah ditambahkan ke dashboard Sales."),
        ],
        turn_plan=PLAN,
    )
    result = await _run(provider, consent, dashboard_id=7)
    assert previews == ["Add the chart “Units” to “Sales”."]
    if allowed:
        apply.assert_awaited_once()
        assert apply.call_args.kwargs["open_dashboard_id"] == 7
        assert apply.call_args.kwargs["editor_state"] == "clean"
        assert result.finish_reason == "stop"
    else:
        apply.assert_not_awaited()
        assert result.finish_reason == "denied"


@pytest.mark.asyncio
async def test_a_dashboard_answer_satisfies_the_governed_data_requirement(monkeypatch):
    import app.modules.superset.dashboard_query as module
    from app.modules.superset.dashboard_query import DashboardAnswer

    answer = DashboardAnswer(
        title="Change in net_revenue by region",
        columns=["region", "net_revenue", "net_revenue (previous)", "change", "share_of_change"],
        rows=[["Jawa", 800.0, 700.0, 100.0, 0.8], ["Bali", 125.0, 100.0, 25.0, 0.2]],
        facts={
            "filter_state_status": "verified",
            "filters_applied": [],
            "period": "2026-10-01 : 2026-11-01",
            "compare_period": "2026-09-01 : 2026-10-01",
            "change": 125.0,
        },
    )
    query = AsyncMock(return_value=answer)
    monkeypatch.setattr(module, "query_dashboard_dataset", query)
    provider = ScriptedProvider(
        [
            tool_call_frame(
                "q1",
                name="dataviz_query",
                arguments={
                    "dataset_id": 9,
                    "metrics": ["net_revenue"],
                    "dimensions": ["region"],
                    "period": "2026-10-01 : 2026-11-01",
                    "compare_period": "2026-09-01 : 2026-10-01",
                },
            ),
            text_frame(
                "Net revenue naik 125.0 dari September ke Oktober 2026; Jawa menyumbang 100.0."
            ),
        ],
        turn_plan={
            "intent": "compound_analytics",
            "tools": ["dataviz_query", "semantic_query", "diagnose_change"],
            "required_tools": ["semantic_query", "diagnose_change"],
            "skills": [],
            "ml_task": None,
        },
    )

    async def consent(_invocation, _classification):
        return True

    result = await _run(provider, consent, dashboard_id=7)
    query.assert_awaited_once()
    assert query.call_args.kwargs["dashboard_id"] == 7
    joined = "".join(result.frames)
    assert result.finish_reason == "stop", joined[-400:]
    assert "data_evidence_incomplete" not in joined
    assert "required_capability_incomplete" not in joined


@pytest.mark.asyncio
@pytest.mark.parametrize("settled", [("dataviz_context",), ()])
async def test_context_the_server_already_gave_settles_its_tool(settled):
    provider = ScriptedProvider(
        [text_frame("Dashboard Sales berisi dua grafik penjualan; filternya belum dipilih.")],
        turn_plan={
            "intent": "direct_answer",
            "tools": ["dataviz_context"],
            "required_tools": ["dataviz_context"],
            "skills": [],
            "ml_task": None,
        },
    )
    context = LoopContext("alice", user=USER, thread_id="t1")
    context.dataviz = {"dashboard_id": 7, "edit_state": "clean"}
    context.settled_capabilities = settled

    async def consent(_invocation, _classification):
        return True

    result = TurnResult(
        frames=[
            frame
            async for frame in AssistantLoop(
                provider=provider,
                registry=_registry(),
                system_prompt="test",
            ).run(
                thread=AssistantThread("t1", "alice", "Dataviz"),
                user_content="dashboard apa?",
                context=context,
                resolve_consent=consent,
            )
        ]
    )
    expected = "stop" if settled else "required_capability_incomplete"
    assert result.finish_reason == expected


@pytest.mark.asyncio
async def test_on_an_open_dashboard_its_own_query_answers_a_ranking(monkeypatch):
    """The planner asks for semantic_query and compute_metrics; the Dataviz
    assistant has neither on an open dashboard, and its dataviz_query answers."""
    import app.modules.superset.dashboard_query as module
    from app.modules.agents.dataviz_assistant import DASHBOARD_ALIASES
    from app.modules.superset.dashboard_query import DashboardAnswer

    answer = DashboardAnswer(
        title="net_revenue by salesman",
        columns=["salesman", "net_revenue"],
        rows=[["Total", 925.0], ["Yusuf", 500.0], ["Ani", 425.0]],
        facts={
            "filter_state_status": "verified",
            "filters_applied": [],
            "adjusted": [],
            "concepts": [{"qualified_name": "Penjualan.NetRevenue"}],
        },
    )
    query = AsyncMock(return_value=answer)
    monkeypatch.setattr(module, "query_dashboard_dataset", query)
    provider = ScriptedProvider(
        [
            tool_call_frame(
                "q1",
                name="dataviz_query",
                arguments={"dataset_id": 9, "metrics": ["net_revenue"], "dimensions": ["salesman"]},
            ),
            text_frame("Yusuf tertinggi dengan net revenue 500.0 dari total 925.0."),
        ],
        turn_plan={
            "intent": "compound_analytics",
            "tools": ["ontology_resolve", "dataviz_query"],
            "required_tools": ["semantic_query", "compute_metrics", "ontology_resolve"],
            "skills": [],
            "ml_task": None,
        },
    )
    registry = ToolRegistry()
    dataviz.register(registry)
    registry.register(_Unused("ontology_resolve"))
    context = LoopContext("alice", user=USER, thread_id="t1")
    context.dataviz = {"dashboard_id": 7, "edit_state": "clean"}
    context.capability_aliases = dict(DASHBOARD_ALIASES)

    async def consent(_invocation, _classification):
        return True

    result = TurnResult(
        frames=[
            frame
            async for frame in AssistantLoop(
                provider=provider, registry=registry, system_prompt="test"
            ).run(
                thread=AssistantThread("t1", "alice", "Dataviz"),
                user_content="siapa salesman tertinggi?",
                context=context,
                resolve_consent=consent,
            )
        ]
    )
    joined = "".join(result.frames)
    query.assert_awaited_once()
    assert result.finish_reason == "stop", joined[-400:]
    assert "required_capability" not in joined


@pytest.mark.asyncio
@pytest.mark.parametrize("on_dashboard", [True, False])
async def test_lineage_is_answered_from_verified_dashboard_metadata(monkeypatch, on_dashboard):
    """'Where does this data come from?' needs no rows on an open dashboard;
    the same question elsewhere still needs governed data."""
    import app.modules.superset.dashboard_context as module

    monkeypatch.setattr(
        module,
        "dashboard_context",
        AsyncMock(return_value={"available": True, "title": "Sales", "charts": [], "datasets": []}),
    )
    call = tool_call_frame("c1", name="dataviz_context")
    call["tool_calls"][0]["function"]["arguments"] = "{}"
    provider = ScriptedProvider(
        [
            call,
            text_frame("Grafiknya memakai dataset retail sales dari Semantic View Retail Sales."),
            text_frame("Grafiknya memakai dataset retail sales dari Semantic View Retail Sales."),
        ],
        turn_plan={
            "intent": "semantic_analytics",
            "tools": ["dataviz_context"],
            "required_tools": ["dataviz_context"],
            "skills": [],
            "ml_task": None,
        },
    )
    context = LoopContext("alice", user=USER, thread_id="t1")
    context.agent_scope = {"surface": "studio"}
    if on_dashboard:
        context.dataviz = {"dashboard_id": 7, "edit_state": "clean"}

    async def consent(_invocation, _classification):
        return True

    result = TurnResult(
        frames=[
            frame
            async for frame in AssistantLoop(
                provider=provider, registry=_registry(), system_prompt="test"
            ).run(
                thread=AssistantThread("t1", "alice", "Dataviz"),
                user_content="data dashboard ini dari mana?",
                context=context,
                resolve_consent=consent,
            )
        ]
    )
    expected = "stop" if on_dashboard else "data_evidence_incomplete"
    assert result.finish_reason == expected


@pytest.mark.asyncio
async def test_a_mislabelled_dashboard_question_still_reaches_its_data(monkeypatch):
    """The planner calls a ranking question a clarification and selects no
    tool; on an open dashboard its own query stays in reach."""
    import app.modules.superset.dashboard_query as module
    from app.modules.agents.dataviz_assistant import DASHBOARD_ALIASES
    from app.modules.superset.dashboard_query import DashboardAnswer

    query = AsyncMock(
        return_value=DashboardAnswer(
            title="net_revenue by region",
            columns=["region", "net_revenue"],
            rows=[["Total", 9.0], ["Jawa", 5.0]],
            facts={"filter_state_status": "verified", "filters_applied": [], "adjusted": []},
        )
    )
    monkeypatch.setattr(module, "query_dashboard_dataset", query)
    provider = ScriptedProvider(
        [
            tool_call_frame(
                "q1",
                name="dataviz_query",
                arguments={"dataset_id": 9, "metrics": ["net_revenue"], "dimensions": ["region"]},
            ),
            text_frame("Jawa tertinggi dengan net revenue 5.0 dari total 9.0."),
        ],
        turn_plan={
            "intent": "clarification",
            "tools": [],
            "required_tools": [],
            "skills": [],
            "ml_task": None,
        },
    )
    registry = ToolRegistry()
    dataviz.register(registry)
    context = LoopContext("alice", user=USER, thread_id="t1")
    context.dataviz = {"dashboard_id": 7, "edit_state": "clean"}
    context.capability_aliases = dict(DASHBOARD_ALIASES)

    async def consent(_invocation, _classification):
        return True

    result = TurnResult(
        frames=[
            frame
            async for frame in AssistantLoop(
                provider=provider, registry=registry, system_prompt="test"
            ).run(
                thread=AssistantThread("t1", "alice", "Dataviz"),
                user_content="region mana yang tertinggi?",
                context=context,
                resolve_consent=consent,
            )
        ]
    )
    query.assert_awaited_once()
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_a_yes_in_the_next_turn_applies_the_prepared_change(monkeypatch):
    """The change was prepared in an earlier turn; the user's "yes" applies it
    (after consent), and the preparation the planner listed again is moot."""
    import app.modules.superset.dataviz as service

    apply = AsyncMock(return_value={"dashboard_id": 7, "chart_id": 79, "verified": True})
    monkeypatch.setattr(service, "apply_change", apply)
    provider = ScriptedProvider(
        [
            tool_call_frame("a1", name="dataviz_apply", arguments={"action_token": "tok-1"}),
            text_frame("Tabel sudah ditambahkan ke dashboard 7."),
        ],
        turn_plan=PLAN,
    )
    prompts: list[str] = []

    async def consent(invocation, _classification):
        prompts.append(invocation.tool_name)
        return True

    result = await _run(provider, consent, dashboard_id=7)
    apply.assert_awaited_once()
    assert apply.call_args.kwargs["token"] == "tok-1"
    assert prompts == ["dataviz_apply"]
    assert result.finish_reason == "stop"
