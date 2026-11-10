"""Nova Notebook documents: the .ipynb codec, validation and revisioned edits."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest

from app.modules.notebooks import documents as documents_module
from app.modules.notebooks.document import (
    MAX_CELLS,
    NotebookConflictError,
    NotebookDocument,
    NotebookFormatError,
    apply_edits,
    make_cell,
    parse_ipynb,
    starter_document,
    to_ipynb,
    validate_name,
)
from app.modules.notebooks.documents import NotebookDocumentService


def _doc(*cells) -> NotebookDocument:
    return NotebookDocument(cells=list(cells), revision=3)


def test_round_trip_keeps_ids_kinds_names_and_engine_and_drops_outputs():
    sql = make_cell(kind="sql", source="SELECT 1", name="sales", engine="nova")
    py = make_cell(kind="python", source="sales.df()\n")
    md = make_cell(kind="markdown", source="# Title\nbody")
    text = to_ipynb(_doc(md, sql, py))
    raw = json.loads(text)
    assert raw["nbformat"] == 4 and raw["nbformat_minor"] == 5
    assert raw["metadata"]["nova"]["revision"] == 3
    code = raw["cells"][1]
    assert code["cell_type"] == "code" and code["outputs"] == []
    assert code["metadata"]["nova"] == {"kind": "sql", "name": "sales", "engine": "nova"}
    back = parse_ipynb(text)
    assert [(c.id, c.kind, c.name, c.source) for c in back.cells] == [
        (md.id, "markdown", None, "# Title\nbody"),
        (sql.id, "sql", "sales", "SELECT 1"),
        (py.id, "python", None, "sales.df()\n"),
    ]
    assert back.cells[1].engine == "nova" and back.revision == 3


def test_plain_jupyter_notebooks_open_and_get_ids():
    text = json.dumps(
        {
            "nbformat": 4,
            "nbformat_minor": 4,
            "metadata": {"kernelspec": {"name": "python3"}},
            "cells": [
                {"cell_type": "code", "source": ["x = 1\n", "x"], "outputs": [{"data": {}}]},
                {"cell_type": "markdown", "source": "hi"},
                {"cell_type": "raw", "source": "raw text"},
            ],
        }
    )
    doc = parse_ipynb(text)
    assert [c.kind for c in doc.cells] == ["python", "markdown", "markdown"]
    assert doc.cells[0].source == "x = 1\nx"
    assert len({c.id for c in doc.cells}) == 3 and doc.revision == 0
    # Foreign notebook metadata survives a save.
    assert json.loads(to_ipynb(doc))["metadata"]["kernelspec"] == {"name": "python3"}


@pytest.mark.parametrize(
    "text",
    ["not json", json.dumps({"nbformat": 3, "cells": []}), json.dumps([1, 2])],
)
def test_non_notebooks_are_refused(text):
    with pytest.raises(NotebookFormatError):
        parse_ipynb(text)


def test_empty_file_is_an_empty_notebook():
    assert parse_ipynb("").cells == []


def test_duplicate_imported_ids_and_names_are_repaired():
    cells = [
        {"cell_type": "code", "id": "same", "source": "1", "metadata": {"nova": {"kind": "sql",
                                                                               "name": "a"}}},
        {"cell_type": "code", "id": "same", "source": "2", "metadata": {"nova": {"kind": "sql",
                                                                               "name": "a"}}},
    ]
    doc = parse_ipynb(json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {},
                                  "cells": cells}))
    assert doc.cells[0].id != doc.cells[1].id
    assert [c.name for c in doc.cells] == ["a", None]


@pytest.mark.parametrize("name", ["db", "duckdb", "print", "class", "__nova_x", "1abc", "a-b"])
def test_reserved_or_invalid_names_are_refused(name):
    with pytest.raises(NotebookFormatError):
        validate_name(name, kind="sql")


def test_markdown_cells_have_no_name():
    with pytest.raises(NotebookFormatError):
        validate_name("x", kind="markdown")


def test_edits_insert_update_move_delete_keep_ids_and_report_changed_sources():
    a = make_cell(kind="python", source="a = 1")
    b = make_cell(kind="sql", source="SELECT 1", name="b")
    doc = _doc(a, b)
    changed = apply_edits(
        doc,
        [
            {"op": "insert", "index": 1,
             "cell": {"kind": "markdown", "source": "note", "id": "m1"}},
            {"op": "update", "cell_id": b.id, "source": "SELECT 2"},
            {"op": "update", "cell_id": a.id, "name": "first"},
            {"op": "move", "cell_id": b.id, "index": 0},
        ],
    )
    assert [c.id for c in doc.cells] == [b.id, a.id, "m1"]
    assert changed == ["m1", b.id]  # a rename does not change what runs
    apply_edits(doc, [{"op": "delete", "cell_id": "m1"}])
    assert [c.id for c in doc.cells] == [b.id, a.id]


def test_retyping_a_cell_to_markdown_drops_its_name():
    cell = make_cell(kind="sql", source="SELECT 1", name="x")
    doc = _doc(cell)
    apply_edits(doc, [{"op": "update", "cell_id": cell.id, "kind": "markdown"}])
    assert doc.cells[0].kind == "markdown" and doc.cells[0].name is None


def test_engine_change_changes_the_source_revision():
    cell = make_cell(kind="sql", source="SELECT 1")
    before = cell.source_revision
    doc = _doc(cell)
    assert apply_edits(doc, [{"op": "update", "cell_id": cell.id, "engine": "nova"}]) == [cell.id]
    assert doc.cells[0].source_revision != before


@pytest.mark.parametrize(
    "edits",
    [
        [{"op": "explode"}],
        [{"op": "update", "cell_id": "missing", "source": "x"}],
        [{"op": "insert", "cell": {"kind": "rust"}}],
        [{"op": "update", "cell_id": "c1", "engine": "spark"}],
        [{"op": "insert", "cell": {"kind": "sql", "name": "dup"}},
         {"op": "insert", "cell": {"kind": "sql", "name": "dup"}}],
        [{"op": "move", "cell_id": "c1", "index": "top"}],
        [],
    ],
)
def test_invalid_edits_are_refused(edits):
    doc = _doc(make_cell(kind="sql", source="SELECT 1", cell_id="c1"))
    with pytest.raises(NotebookFormatError):
        apply_edits(doc, edits)


def test_cell_count_is_bounded():
    doc = _doc(*[make_cell(kind="python") for _ in range(MAX_CELLS)])
    with pytest.raises(NotebookFormatError):
        apply_edits(doc, [{"op": "insert", "cell": {"kind": "python"}}])


def test_oversized_source_is_refused():
    with pytest.raises(NotebookFormatError):
        make_cell(kind="python", source="x" * (256 * 1024 + 1))


def test_starter_notebook_runs_a_named_sql_cell_then_python():
    doc = starter_document()
    assert [c.kind for c in doc.cells] == ["markdown", "sql", "python"]
    assert doc.cells[1].name == "result_1"


class FakeWorkspace:
    def __init__(self) -> None:
        self.files: dict[str, dict] = {}

    async def create_file(self, username, parent_path, name, content):
        entry = {"id": "e1", "name": name, "path": name, "parent_path": parent_path,
                 "entry_type": "file", "user_name": username}
        self.files["e1"] = {"entry": entry, "content": content}
        return entry

    async def get_file(self, username, entry_id):
        stored = self.files[entry_id]
        return stored["entry"], stored["content"]

    async def update_file(self, username, entry_id, content):
        self.files[entry_id]["content"] = content
        return self.files[entry_id]["entry"]


@pytest.fixture
def workspace(monkeypatch):
    fake = FakeWorkspace()
    monkeypatch.setattr(documents_module, "workspace_service", fake)

    @asynccontextmanager
    async def no_lock(entry_id):
        yield

    monkeypatch.setattr(documents_module, "_document_lock", no_lock)

    async def no_audit(**kwargs):
        return None

    monkeypatch.setattr(documents_module, "write_audit_log", no_audit)
    return fake


async def test_service_creates_with_extension_and_edits_by_revision(workspace):
    service = NotebookDocumentService()
    entry, doc = await service.create("ana", parent_path="", name="revenue")
    assert entry["name"] == "revenue.ipynb" and doc.revision == 1
    cell_id = doc.cells[1].id
    _, edited, changed = await service.edit(
        "ana", "e1", base_revision=1,
        edits=[{"op": "update", "cell_id": cell_id, "source": "SELECT 7 AS n"}],
    )
    assert edited.revision == 2 and changed == [cell_id]
    _, reread = await service.get("ana", "e1")
    assert reread.revision == 2 and reread.cell(cell_id).source == "SELECT 7 AS n"


async def test_a_stale_edit_is_refused_with_the_current_revision(workspace):
    service = NotebookDocumentService()
    _, doc = await service.create("ana", parent_path="", name="n.ipynb")
    cell_id = doc.cells[0].id
    await service.edit("ana", "e1", base_revision=1,
                       edits=[{"op": "update", "cell_id": cell_id, "source": "# one"}])
    with pytest.raises(NotebookConflictError) as raised:
        await service.edit("ana", "e1", base_revision=1,
                           edits=[{"op": "update", "cell_id": cell_id, "source": "# two"}])
    assert raised.value.current_revision == 2
    _, current = await service.get("ana", "e1")
    assert current.cell(cell_id).source == "# one"


async def test_a_failed_edit_saves_nothing(workspace):
    service = NotebookDocumentService()
    _, doc = await service.create("ana", parent_path="", name="n.ipynb")
    before = workspace.files["e1"]["content"]
    with pytest.raises(NotebookFormatError):
        await service.edit("ana", "e1", base_revision=1, edits=[
            {"op": "delete", "cell_id": doc.cells[0].id},
            {"op": "update", "cell_id": "missing", "source": "x"},
        ])
    assert workspace.files["e1"]["content"] == before


async def test_importing_an_ipynb_keeps_its_cells(workspace):
    service = NotebookDocumentService()
    text = to_ipynb(_doc(make_cell(kind="python", source="print(1)", cell_id="keep")))
    _, doc = await service.create("ana", parent_path="", name="imported.ipynb", content=text)
    assert [c.id for c in doc.cells] == ["keep"] and doc.revision == 1


async def test_non_notebook_files_are_not_opened_as_notebooks(workspace):
    workspace.files["e2"] = {"entry": {"id": "e2", "name": "query.sql"}, "content": "SELECT 1"}
    with pytest.raises(Exception, match="not a notebook"):
        await NotebookDocumentService().get("ana", "e2")
