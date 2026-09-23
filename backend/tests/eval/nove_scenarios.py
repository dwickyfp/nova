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
    semantic_view_reference = ReferenceTool()
    create_view = EvalTool(
        "create_semantic_view",
        classification="destructive",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "tables": {"type": "array", "items": {"type": "string"}},
                "publish": {"type": "boolean"},
            },
            "required": ["name", "tables"],
        },
    )
    return [
        Scenario(
            name="nove_creates_semantic_view_after_approval",
            content="Buat dan publikasikan Semantic View sales dari sales.orders",
            tools=[create_view],
            turn_plan={
                "intent": "ui_operation",
                "tools": ["create_semantic_view"],
                "required_tools": ["create_semantic_view"],
                "skills": [],
                "ml_task": None,
            },
            script=[
                tool_call_frame(
                    "create",
                    name="create_semantic_view",
                    arguments={
                        "name": "sales_view",
                        "tables": ["sales.orders"],
                        "publish": True,
                    },
                ),
                text_frame("Semantic View sales_view berhasil dipublikasikan."),
            ],
            checks=[
                check("write executed", used_tool("create_semantic_view")),
                check(
                    "approval requested",
                    lambda result: result.consent_prompts
                    == [("create_semantic_view", "destructive")],
                ),
                check("completed", finished_with("stop")),
            ],
        ),
        Scenario(
            name="nove_semantic_view_creation_help_does_not_require_query",
            content="apakah kamu bisa bantu aku membuat semantic view atas data ku ?",
            tools=[semantic_view_reference, EvalTool("semantic_view_query")],
            script=[
                tool_call_frame(
                    "docs", name="search_knowledge", arguments={"query": "membuat Semantic View"}
                ),
                tool_call_frame(
                    "details",
                    name="search_knowledge",
                    arguments={"query": "Semantic View metric dimension"},
                ),
                text_frame(
                    "Bisa. Sebutkan tabel sumber, kolom penghubung, serta metrik "
                    "dan dimensi yang ingin dipakai."
                ),
            ],
            checks=[
                check(
                    "both references retrieved",
                    lambda result: len(semantic_view_reference.runs) == 2,
                ),
                check("no published view query", did_not_use_tool("semantic_view_query")),
                check("no query consent", did_not_prompt()),
                check("answered without data gate", finished_with("stop")),
            ],
        ),
        Scenario(
            name="nove_explains_ai_search_without_querying_data",
            content="AI Search ini fitur untuk apa?",
            tools=[ReferenceTool(), EvalTool("ai_search")],
            script=[
                tool_call_frame(
                    "docs", name="search_knowledge", arguments={"query": "AI Search fitur"}
                ),
                text_frame(
                    "AI Search membantu mencari data sumber berdasarkan kata atau kemiripan makna."
                ),
            ],
            checks=[
                check("reference retrieved", used_tool("search_knowledge")),
                check("did not search user data", did_not_use_tool("ai_search")),
                check("no consent needed for explanation", did_not_prompt()),
                check("no implementation source", lambda result: "knowledge:" not in result.text),
                check("completed", finished_with("stop")),
            ],
        ),
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
                    "Mulai dari SQL yang dijalankan dan pesan error yang muncul."
                ),
            ],
            checks=[
                check("retrieved", used_tool("search_knowledge")),
                check("reference is not a data action", did_not_prompt()),
                check("answer uses guidance", answer_contains("SQL yang dijalankan")),
                check("no implementation source", lambda result: "knowledge:" not in result.text),
                check("complete", finished_with("stop")),
            ],
        ),
        Scenario(
            name="nove_documentation_cannot_replace_data_evidence",
            content="Tampilkan jumlah transaksi bulan ini",
            tools=[ReferenceTool(), EvalTool("query_execute")],
            turn_plan={
                "intent": "raw_sql_query",
                "tools": ["search_knowledge", "query_execute"],
                "required_tools": ["query_execute"],
                "ml_task": None,
            },
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
