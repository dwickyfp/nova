"""Nove regressions for authoring, reference retrieval, and multi-step queries."""

from app.modules.assistant.context import ContextManager
from app.modules.assistant.tools.search_knowledge import search_knowledge_tool
from tests.benchmark.harness import text_frame, tool_call_frame
from tests.eval.harness import (
    EvalTool,
    Scenario,
    answer_contains,
    check,
    did_not_prompt,
    did_not_use_tool,
    finished_with,
    used_tool,
)


class ReferenceTool(EvalTool):
    requires_consent = False

    def __init__(self) -> None:
        super().__init__("search_knowledge", parameters=search_knowledge_tool.parameters)

    async def run(self, invocation, context):
        self.runs.append(invocation)
        return await search_knowledge_tool.run(invocation, context)


def nove_scenarios() -> list[Scenario]:
    query = EvalTool("query_execute")
    return [
        Scenario(
            name="nove_tool_result_cannot_overflow_next_provider_call",
            content="SELECT 1",
            tools=[EvalTool("query_execute", summary="x" * 8000)],
            script=[tool_call_frame("data"), text_frame("should never run")],
            context_manager=ContextManager(token_budget=1000),
            read_only_grant=True,
            checks=[
                check("query ran", used_tool("query_execute")),
                check("stopped before second call", lambda result: result.provider_calls == 1),
                check("bounded context", finished_with("context_overflow")),
            ],
        ),
        Scenario(
            name="nove_concept_needs_no_database",
            content="Apa itu revenue?",
            tools=[EvalTool("query_execute")],
            script=[
                text_frame(
                    "Revenue berarti pendapatan; definisi metrik mengikuti model bisnis Anda."
                )
            ],
            checks=[
                check("no execution", did_not_use_tool("query_execute")),
                check("no consent", did_not_prompt()),
                check("complete", finished_with("stop")),
            ],
        ),
        Scenario(
            name="nove_sql_authoring_needs_no_execution",
            content="Buat SQL untuk revenue bulanan",
            tools=[EvalTool("query_execute")],
            script=[text_frame("Berikan nama tabel dan kolom pendapatan untuk menyusun SQL.")],
            checks=[
                check("no execution", did_not_use_tool("query_execute")),
                check("complete", finished_with("stop")),
            ],
        ),
        Scenario(
            name="nove_schema_then_data_query",
            content="Tampilkan jumlah transaksi bulan ini",
            tools=[query],
            read_only_grant=True,
            script=[
                tool_call_frame("inspect", sql="SHOW TABLES"),
                tool_call_frame("data", sql="SELECT count(*) FROM transactions"),
                text_frame("Hasil query telah diperiksa."),
            ],
            checks=[
                check("both steps executed", lambda result: len(query.runs) == 2),
                check("read grant respected", did_not_prompt()),
                check("complete", finished_with("stop")),
            ],
        ),
        Scenario(
            name="nove_diagnosis_can_retrieve_documentation",
            content="Cari penyebab query lambat",
            tools=[ReferenceTool()],
            script=[
                tool_call_frame(
                    "docs", name="search_knowledge", arguments={"query": "query lambat penyebab"}
                ),
                text_frame(
                    "Menurut knowledge:query-troubleshooting, mulai dari SQL dan error aktual."
                ),
            ],
            checks=[
                check("retrieved", used_tool("search_knowledge")),
                check("reference is not a data action", did_not_prompt()),
                check("source cited", answer_contains("knowledge:query-troubleshooting")),
                check("complete", finished_with("stop")),
            ],
        ),
        Scenario(
            name="nove_documentation_cannot_replace_data_evidence",
            content="Tampilkan jumlah transaksi bulan ini",
            tools=[ReferenceTool(), EvalTool("query_execute")],
            script=[
                tool_call_frame(
                    "docs", name="search_knowledge", arguments={"query": "transactions"}
                ),
                text_frame("Total transaksi adalah 99."),
            ],
            checks=[
                check("retrieved", used_tool("search_knowledge")),
                check("no invented total", lambda result: "99" not in result.text),
                check("evidence gate", finished_with("required_capability_incomplete")),
            ],
        ),
        Scenario(
            name="nove_entity_search_falls_back_to_authorized_query",
            content="Cari pelanggan Sari",
            tools=[EvalTool("query_execute")],
            script=[tool_call_frame("data", sql="SELECT 1"), text_frame("Query selesai.")],
            checks=[
                check("query ran", used_tool("query_execute")),
                check("consent remains required", lambda result: len(result.consent_prompts) == 1),
                check("complete", finished_with("stop")),
            ],
        ),
    ]
