"""Neva regressions for authoring, reference retrieval, and multi-step queries."""

from app.modules.assistant.context import ContextManager
from app.modules.assistant.tools.search_knowledge import search_knowledge_tool
from tests.benchmark.harness import text_frame, tool_call_frame
from tests.eval.harness import (
    EvalTool,
    Scenario,
    answer_contains,
    check,
    deny,
    did_not_prompt,
    did_not_use_tool,
    finished_with,
    prompted_for,
    used_tool,
)

_NOTEBOOK_CELL = {
    "type": "object",
    "properties": {"cell_id": {"type": "string"}},
    "required": ["cell_id"],
}
_NOTEBOOK_READ = {"type": "object", "properties": {"cell_id": {"type": "string"}}}


def _notebook_tools() -> tuple[EvalTool, EvalTool]:
    context = EvalTool(
        "notebook_context",
        parameters=_NOTEBOOK_READ,
        summary="Read 3 cells of revenue.ipynb; cell c2 failed with KeyError 'region'",
    )
    run = EvalTool(
        "notebook_run_cell",
        classification="session_change",
        parameters=_NOTEBOOK_CELL,
        summary="The cell succeeded: 7 rows, revenue by month",
    )
    return context, run


def notebook_scenarios() -> list[Scenario]:
    """Neva in a notebook: reads first, runs a cell only with approval."""
    context, run = _notebook_tools()
    denied_context, denied_run = _notebook_tools()
    explain_context, explain_run = _notebook_tools()
    return [
        Scenario(
            name="neva_runs_a_notebook_cell_after_approval",
            content="Jalankan cell c2 di notebook ini",
            tools=[context, run],
            turn_plan={
                "intent": "ui_operation",
                "tools": ["notebook_context", "notebook_run_cell"],
                "required_tools": ["notebook_run_cell"],
                "skills": [],
                "ml_task": None,
            },
            script=[
                tool_call_frame("read", name="notebook_context", arguments={"cell_id": "c2"}),
                tool_call_frame("run", name="notebook_run_cell", arguments={"cell_id": "c2"}),
                text_frame("Cell c2 selesai: 7 baris, pendapatan per bulan."),
            ],
            checks=[
                check("read the notebook first", used_tool("notebook_context")),
                check("running asked for approval", prompted_for("notebook_run_cell")),
                check(
                    "approval names a notebook change",
                    lambda result: ("notebook_run_cell", "session_change")
                    in result.consent_prompts,
                ),
                check("the approved cell ran once", lambda result: len(run.runs) == 1),
                check("completed", finished_with("stop")),
            ],
        ),
        Scenario(
            name="neva_does_not_run_a_cell_the_person_declined",
            content="Run the revenue cell",
            tools=[denied_context, denied_run],
            read_only_grant=True,
            turn_plan={
                "intent": "ui_operation",
                "tools": ["notebook_context", "notebook_run_cell"],
                "required_tools": ["notebook_run_cell"],
                "skills": [],
                "ml_task": None,
            },
            resolve_consent=deny,
            script=[
                tool_call_frame("read", name="notebook_context", arguments={"cell_id": "c2"}),
                tool_call_frame("run", name="notebook_run_cell", arguments={"cell_id": "c2"}),
            ],
            checks=[
                check("asked before running", prompted_for("notebook_run_cell")),
                check("nothing ran", lambda result: not denied_run.runs),
                check("stopped as declined", finished_with("denied")),
            ],
        ),
        Scenario(
            name="neva_explains_a_notebook_error_without_running_anything",
            content="Kenapa cell kedua error?",
            tools=[explain_context, explain_run],
            read_only_grant=True,
            turn_plan={
                "intent": "capability_help",
                "tools": ["notebook_context"],
                "required_tools": [],
                "skills": [],
                "ml_task": None,
            },
            script=[
                tool_call_frame("read", name="notebook_context", arguments={"cell_id": "c2"}),
                text_frame(
                    "Cell kedua mencari kolom region yang tidak ada di hasilnya; "
                    "kolomnya bernama city. Ganti region menjadi city."
                ),
            ],
            checks=[
                check("read the notebook", used_tool("notebook_context")),
                check("did not run code", did_not_use_tool("notebook_run_cell")),
                check("no approval needed", did_not_prompt()),
                check("names the cause", answer_contains("city")),
                check("completed", finished_with("stop")),
            ],
        ),
    ]


class ReferenceTool(EvalTool):
    requires_consent = False

    def __init__(self) -> None:
        super().__init__("search_knowledge", parameters=search_knowledge_tool.parameters)

    async def run(self, invocation, context):
        self.runs.append(invocation)
        return await search_knowledge_tool.run(invocation, context)


def neva_scenarios() -> list[Scenario]:
    return [*_neva_scenarios(), *notebook_scenarios()]


def _neva_scenarios() -> list[Scenario]:
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
            name="neva_creates_semantic_view_after_approval",
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
            name="neva_semantic_view_creation_help_does_not_require_query",
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
            name="neva_explains_ai_search_without_querying_data",
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
            name="neva_tool_result_cannot_overflow_next_provider_call",
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
            name="neva_concept_needs_no_database",
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
            name="neva_sql_authoring_needs_no_execution",
            content="Buat SQL untuk revenue bulanan",
            tools=[EvalTool("query_execute")],
            script=[text_frame("Berikan nama tabel dan kolom pendapatan untuk menyusun SQL.")],
            checks=[
                check("no execution", did_not_use_tool("query_execute")),
                check("complete", finished_with("stop")),
            ],
        ),
        Scenario(
            name="neva_schema_then_data_query",
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
            name="neva_diagnosis_can_retrieve_documentation",
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
            name="neva_documentation_cannot_replace_data_evidence",
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
            name="neva_entity_search_falls_back_to_authorized_query",
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
