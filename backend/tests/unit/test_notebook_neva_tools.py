"""Neva in a notebook: page proposals are checked, tools act only on the open notebook."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.assistant.app_context import NevaAppContext
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools import client_capability as capability_module
from app.modules.assistant.tools import notebook as notebook_tools
from app.modules.assistant.tools.client_capability import invoke_client_capability_tool
from app.modules.notebooks.document import NotebookDocument, make_cell

USER = {"username": "alice", "session_id": "s1", "active_role": "analyst",
        "assigned_roles": ["analyst"], "security_context_version": 1}


def _app(entity_type: str = "notebook", capabilities=("notebook.propose_cell",)):
    return NevaAppContext.model_validate(
        {
            "version": 1,
            "surface": {"id": "workspace.notebook", "route": "/workspaces", "title": "Notebook"},
            "entity": {"type": entity_type, "id": "nb-1", "name": "revenue.ipynb"},
            "capabilities": list(capabilities),
        }
    )


def _context(app=None, checked=None):
    return SimpleNamespace(app_context=app or _app(), checked_sql=checked or set(), user=None,
                           user_name="alice")


def _propose(**args):
    base = {"cell_id": "c1", "kind": "python", "engine": "none", "source": "x = 1",
            "mode": "insert_below"}
    base.update(args)
    return ToolInvocation(tool_call_id="t1", tool_name="invoke_client_capability",
                          arguments={"capability": "notebook.propose_cell", "args": base})


async def test_a_python_cell_that_parses_goes_to_the_page():
    outcome = await invoke_client_capability_tool.run(_propose(), _context())
    assert outcome.ok
    action = outcome.metadata["client_action"]
    assert action["capability"] == "notebook.propose_cell"
    assert action["args"]["mode"] == "insert_below"


async def test_python_that_does_not_parse_is_sent_back():
    outcome = await invoke_client_capability_tool.run(_propose(source="def broken(:"), _context())
    assert not outcome.ok and outcome.recoverable and "parse" in outcome.error


async def test_nova_sql_must_be_checked_first_and_duckdb_sql_need_not(monkeypatch):
    monkeypatch.setattr(capability_module, "_normalize", lambda sql: sql.strip().lower())
    unchecked = await invoke_client_capability_tool.run(
        _propose(kind="sql", engine="nova", source="SELECT 1"), _context()
    )
    assert not unchecked.ok and "validate_sql" in unchecked.error
    checked = await invoke_client_capability_tool.run(
        _propose(kind="sql", engine="nova", source="SELECT 1"), _context(checked={"select 1"})
    )
    assert checked.ok
    local = await invoke_client_capability_tool.run(
        _propose(kind="sql", engine="duckdb", source="SELECT * FROM read_csv('a.csv')"),
        _context(),
    )
    assert local.ok


@pytest.mark.parametrize(
    "args",
    [{"mode": "append"}, {"kind": "rust"}, {"engine": "spark"}, {"kind": "sql", "engine": "none"}],
)
async def test_malformed_proposals_are_sent_back(args):
    outcome = await invoke_client_capability_tool.run(_propose(**args), _context())
    assert not outcome.ok and outcome.recoverable


async def test_a_page_without_the_capability_gets_no_proposal():
    outcome = await invoke_client_capability_tool.run(_propose(), _context(_app(capabilities=())))
    assert not outcome.ok and "unavailable" in outcome.error


class FakeRuntime:
    def __init__(self) -> None:
        self.started = 0
        self.runs: list[dict] = []
        self.status_value = "READY"

    async def status(self, user, notebook_id):
        return {"runtime": {"status": self.status_value, "active_role": "analyst",
                            "nova_attached": True}}

    async def start(self, user, notebook_id):
        self.started += 1

    async def latest(self, user, notebook_id):
        return {"executions": [{"cell_id": "c1", "status": "failed", "source_revision": "x",
                                "error_message": "boom", "outputs": [
                                    {"type": "error", "ename": "KeyError", "evalue": "'region'",
                                     "traceback": ["a", "b"]},
                                    {"type": "table", "table": {"schema": [{"name": "a"}],
                                                                "rows": [[i] for i in range(50)],
                                                                "total_rows": 50,
                                                                "origin": "nova"}}]}],
                "variables": [{"name": "df", "type": "DataFrame"}]}

    async def run(self, user, notebook_id, **kwargs):
        self.runs.append(kwargs)
        return {"executions": [{"execution_id": "e1", "cell_id": kwargs["cell_id"]}]}

    async def execution(self, user, notebook_id, execution_id):
        return {"status": "succeeded", "outputs": [{"type": "stream", "text": "done"}]}


@pytest.fixture
def notebook(monkeypatch):
    from app.modules.notebooks import documents
    from app.modules.notebooks.runtime import service, state

    runtime = FakeRuntime()
    document = NotebookDocument(cells=[make_cell(kind="python", source="df['region']",
                                                 cell_id="c1")], revision=2)

    async def get(user, notebook_id):
        assert notebook_id == "nb-1"
        return {"name": "revenue.ipynb"}, document

    monkeypatch.setattr(documents.notebook_documents, "get", get)
    for name in ("status", "start", "latest", "run", "execution"):
        monkeypatch.setattr(service.notebook_runtime_service, name, getattr(runtime, name))

    async def get_execution(execution_id):
        return {"status": "succeeded"}

    monkeypatch.setattr(state, "get_execution", get_execution)
    return runtime


def _ctx(entity="notebook"):
    return SimpleNamespace(app_context=_app(entity), user=USER)


async def test_context_reads_cells_errors_and_bounded_tables(notebook):
    outcome = await notebook_tools.notebook_context_tool.run(
        ToolInvocation(
            tool_call_id="t", tool_name="notebook_context", arguments={"cell_id": "c1"}
        ),
        _ctx(),
    )
    assert outcome.ok and outcome.data["focus_cell"] == "c1"
    cell = outcome.data["cells"][0]
    assert cell["last_run"]["status"] == "failed" and cell["last_run"]["out_of_date"]
    outputs = cell["last_run"]["outputs"]
    assert outputs[0]["name"] == "KeyError" and len(outputs[1]["rows"]) == 10
    assert outcome.data["runtime"]["nova_data_role"] == "analyst"


async def test_tools_refuse_when_no_notebook_is_open(notebook):
    outcome = await notebook_tools.notebook_context_tool.run(
        ToolInvocation(tool_call_id="t", tool_name="notebook_context", arguments={}),
        _ctx("workspace_file"),
    )
    assert not outcome.ok


async def test_running_a_cell_starts_the_runtime_when_needed_and_reports_the_result(notebook):
    notebook.status_value = "STOPPED"
    outcome = await notebook_tools.notebook_run_cell_tool.run(
        ToolInvocation(tool_call_id="call-9", tool_name="notebook_run_cell",
                       arguments={"cell_id": "c1"}),
        _ctx(),
    )
    assert outcome.ok and outcome.data["status"] == "succeeded"
    assert notebook.started == 1
    assert notebook.runs[0]["idempotency_key"] == "neva-call-9"


async def test_running_an_unknown_cell_is_refused(notebook):
    outcome = await notebook_tools.notebook_run_cell_tool.run(
        ToolInvocation(
            tool_call_id="t", tool_name="notebook_run_cell", arguments={"cell_id": "zz"}
        ),
        _ctx(),
    )
    assert not outcome.ok and not notebook.runs


def test_run_and_install_need_approval_every_time():
    tools = (notebook_tools.notebook_run_cell_tool, notebook_tools.notebook_install_package_tool)
    for tool in tools:
        assert tool.requires_consent and tool.classification == "session_change"
    assert notebook_tools.notebook_context_tool.classification == "read_only"
