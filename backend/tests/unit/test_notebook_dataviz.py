"""Add to Dataviz from a notebook: only a result Nova can run again qualifies."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.modules.notebooks.document import NotebookDocument, make_cell
from app.modules.notebooks.runtime.worker import NotebookRuntimeWorker
from app.modules.superset import previews

USER = {"username": "alice", "active_role": "analyst"}


def _setup(monkeypatch, *, table: dict, role="analyst", source_revision=None):
    from app.modules.notebooks import documents, repository
    from app.modules.workspaces import service as workspace_module

    cell = make_cell(kind="sql", source="SELECT region FROM nova.s.t", cell_id="c1")
    document = NotebookDocument(cells=[cell], revision=1)

    async def get_execution(username, notebook_id, execution_id):
        return {"status": "succeeded", "cell_kind": "sql", "active_role": role, "cell_id": "c1",
                "source_revision": source_revision or cell.source_revision, "output_key": "k"}

    async def get(username, notebook_id):
        return {"name": "sales.ipynb"}, document

    async def read(key):
        return json.dumps([{"type": "table", "table": table}]).encode()

    monkeypatch.setattr(repository.notebook_repository, "get_execution", get_execution)
    monkeypatch.setattr(documents.notebook_documents, "get_for", get)
    monkeypatch.setattr(workspace_module.workspace_service, "read_internal_object", read)


LOCATOR = previews.Locator(thread_id="notebook:nb-1", message_id="e1", content_id="table")
TABLE = {"schema": [{"name": "region"}], "rows": [["Jakarta"]], "origin": "local_duckdb"}


async def test_a_checked_nova_query_can_be_added(monkeypatch):
    _setup(monkeypatch, table={**TABLE, "nova_sql": "SELECT region FROM default_catalog.s.t"})
    resolved = await previews._resolve_notebook(LOCATOR, USER)
    assert resolved.sql == "SELECT region FROM default_catalog.s.t"
    assert resolved.provenance == "tool_step" and resolved.provenance_issue is None


async def test_a_notebook_only_result_explains_why_not(monkeypatch):
    _setup(monkeypatch, table=TABLE)
    resolved = await previews._resolve_notebook(LOCATOR, USER)
    assert resolved.sql is None and resolved.provenance == "none"
    assert "only Nova catalogs" in (resolved.provenance_issue or "")


async def test_a_changed_cell_or_another_role_cannot_be_added(monkeypatch):
    _setup(monkeypatch, table={**TABLE, "nova_sql": "SELECT 1"}, source_revision="sha256:old")
    changed = await previews._resolve_notebook(LOCATOR, USER)
    assert changed.sql is None and "changed" in (changed.provenance_issue or "")
    _setup(monkeypatch, table={**TABLE, "nova_sql": "SELECT 1"}, role="admin")
    with pytest.raises(previews.PreviewError, match="another role"):
        await previews._resolve_notebook(LOCATOR, USER)


@pytest.fixture
def nova(monkeypatch):
    ran: list[str] = []
    answer = {"error": None}

    class Service:
        async def execute_statements(self, **kwargs):
            ran.append(kwargs["sql"])
            assert kwargs["username"] == "alice" and kwargs["role"] == "analyst"
            return [SimpleNamespace(error=answer["error"])]

    async def resolve(session_id):
        return SimpleNamespace(username="alice", active_role="analyst",
                               security_context_version=1, encrypted_password="e",
                               session_id=session_id)

    import app.modules.query.service as query_module
    from app.modules.access_control import data_tokens

    monkeypatch.setattr(query_module, "query_service", Service())
    monkeypatch.setattr(data_tokens, "resolve_session", resolve)
    return ran, answer


async def test_the_worker_keeps_a_candidate_only_when_nova_accepts_it(nova):
    ran, answer = nova
    worker = NotebookRuntimeWorker(provider=SimpleNamespace())
    job = {"session_id": "s1", "source": "SELECT 1", "engine": None}
    candidate = {"nova_candidate": "SELECT region FROM default_catalog.s.t;"}
    assert await worker._nova_sql(job, candidate, {}) == "SELECT region FROM default_catalog.s.t"
    assert ran == ["SELECT * FROM (SELECT region FROM default_catalog.s.t) AS nova_check LIMIT 0"]
    answer["error"] = "Unknown function list_sum"
    assert await worker._nova_sql(job, candidate, {}) is None
    assert await worker._nova_sql(job, {}, {}) is None


async def test_an_old_nova_engine_cell_keeps_its_source(nova):
    worker = NotebookRuntimeWorker(provider=SimpleNamespace())
    job = {"session_id": "s1", "source": "SELECT 1;", "engine": "nova"}
    assert await worker._nova_sql(job, {}, {"origin": "nova"}) == "SELECT 1"
