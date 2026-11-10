"""Notebook documents: the ``.ipynb`` codec, validation and structural edits.

A notebook is a Workspace file whose content is a Jupyter nbformat 4.5
document. Nova keeps three things in it beyond plain Jupyter:

* every cell's stable ``id`` (nbformat 4.5 has one), which never changes on
  reorder, rename or retype, so outputs and history attach to the cell, not to
  its position;
* ``cell.metadata.nova``: the Nova kind (``python``, ``sql``, ``markdown``),
  the result name of a SQL cell and the SQL engine (``duckdb`` or ``nova``).
  SQL cells are stored as Jupyter code cells, so other Jupyter tools still open
  the file and see the SQL text;
* ``metadata.nova.revision``: a counter every saved edit increments. An edit
  names the revision it was made against and is refused when another edit got
  there first (lost-update protection).

Outputs are never stored in the file: they belong to executions, which Nova
keeps separately with their own retention.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import keyword
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

CellKind = Literal["python", "sql", "markdown"]
SqlEngine = Literal["duckdb", "nova"]

CELL_KINDS: tuple[str, ...] = ("python", "sql", "markdown")
SQL_ENGINES: tuple[str, ...] = ("duckdb", "nova")

#: Bounds on what one document may hold. Large enough for real notebooks,
#: small enough that a save or a parse never becomes a resource problem.
MAX_CELLS = 2000
MAX_CELL_SOURCE_BYTES = 256 * 1024
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_NAME_LENGTH = 64

#: A cell result name: a plain identifier, usable from Python and as
#: ``{{name}}`` in a SQL cell.
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: nbformat 4.5 cell id rule.
_CELL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: Names a cell may not take: the runtime's own globals and Python builtins.
RESERVED_NAMES = frozenset(
    {"db", "duckdb", "nova", "display", "get_ipython", "In", "Out", "exit", "quit"}
    | {name for name in dir(builtins) if not name.startswith("_")}
)


class NotebookFormatError(ValueError):
    """The content is not a notebook Nova can open, or an edit is invalid."""


class NotebookConflictError(Exception):
    """An edit was made against a revision that is no longer current."""

    def __init__(self, current_revision: int) -> None:
        super().__init__("The notebook changed since this edit was made")
        self.current_revision = current_revision


@dataclass
class Cell:
    id: str
    kind: CellKind
    source: str = ""
    name: str | None = None
    engine: SqlEngine = "duckdb"
    #: Jupyter metadata Nova does not interpret, kept as it was read.
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def source_revision(self) -> str:
        """Identifies exactly what would run: kind, engine and source."""
        digest = hashlib.sha256(
            f"{self.kind}\x00{self.engine if self.kind == 'sql' else ''}\x00{self.source}".encode()
        ).hexdigest()
        return f"sha256:{digest[:32]}"

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "name": self.name,
            "engine": self.engine if self.kind == "sql" else None,
            "source_revision": self.source_revision,
        }


@dataclass
class NotebookDocument:
    cells: list[Cell] = field(default_factory=list)
    revision: int = 0
    #: Notebook-level Jupyter metadata Nova does not interpret.
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    def cell(self, cell_id: str) -> Cell:
        for cell in self.cells:
            if cell.id == cell_id:
                return cell
        raise NotebookFormatError("Cell not found")

    def index_of(self, cell_id: str) -> int:
        for index, cell in enumerate(self.cells):
            if cell.id == cell_id:
                return index
        raise NotebookFormatError("Cell not found")

    def to_api(self) -> dict[str, Any]:
        return {"revision": self.revision, "cells": [cell.to_api() for cell in self.cells]}


def new_cell_id() -> str:
    return uuid.uuid4().hex[:16]


def validate_name(name: str | None, *, kind: str) -> str | None:
    """A cell name is optional; when set it must be a usable identifier."""
    if name is None or name == "":
        return None
    if kind == "markdown":
        raise NotebookFormatError("Markdown cells have no result name")
    if len(name) > MAX_NAME_LENGTH or not _NAME.match(name):
        raise NotebookFormatError(
            "A cell name starts with a letter or underscore and holds only letters, "
            "digits and underscores"
        )
    if name in RESERVED_NAMES or keyword.iskeyword(name) or name.startswith("__nova"):
        raise NotebookFormatError(f"'{name}' is reserved in the notebook runtime")
    return name


def _validate_source(source: str) -> str:
    if not isinstance(source, str):
        raise NotebookFormatError("Cell source must be text")
    if len(source.encode("utf-8")) > MAX_CELL_SOURCE_BYTES:
        raise NotebookFormatError("A cell holds at most 256 KB of source")
    return source


def make_cell(
    *,
    kind: str,
    source: str = "",
    name: str | None = None,
    engine: str | None = None,
    cell_id: str | None = None,
) -> Cell:
    if kind not in CELL_KINDS:
        raise NotebookFormatError("A cell is Python, SQL or Markdown")
    if engine is not None and engine not in SQL_ENGINES:
        raise NotebookFormatError("A SQL cell runs on DuckDB or Nova")
    if cell_id is not None and not _CELL_ID.match(cell_id):
        raise NotebookFormatError("Invalid cell id")
    return Cell(
        id=cell_id or new_cell_id(),
        kind=kind,  # type: ignore[arg-type]
        source=_validate_source(source),
        name=validate_name(name, kind=kind),
        engine=(engine or "duckdb") if kind == "sql" else "duckdb",  # type: ignore[arg-type]
    )


def _ensure_unique(document: NotebookDocument) -> None:
    if len(document.cells) > MAX_CELLS:
        raise NotebookFormatError(f"A notebook holds at most {MAX_CELLS} cells")
    ids: set[str] = set()
    names: set[str] = set()
    for cell in document.cells:
        if cell.id in ids:
            raise NotebookFormatError("Two cells share one id")
        ids.add(cell.id)
        if cell.name:
            if cell.name in names:
                raise NotebookFormatError(f"Two cells are named '{cell.name}'")
            names.add(cell.name)


def _join_source(value: Any) -> str:
    if isinstance(value, list):
        return "".join(str(part) for part in value)
    if isinstance(value, str):
        return value
    raise NotebookFormatError("Cell source must be text")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_ipynb(text: str) -> NotebookDocument:
    """Read nbformat 4 JSON into a document; outputs are dropped.

    An empty file is an empty notebook. Cells without an id (nbformat < 4.5)
    get one, so an imported notebook is editable at once.
    """
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise NotebookFormatError("The notebook is larger than 8 MB")
    if not text.strip():
        return NotebookDocument()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise NotebookFormatError("The file is not a Jupyter notebook (invalid JSON)") from exc
    if not isinstance(raw, dict) or raw.get("nbformat") != 4 or not isinstance(
        raw.get("cells"), list
    ):
        raise NotebookFormatError("The file is not a Jupyter notebook (nbformat 4)")
    metadata = _as_dict(raw.get("metadata"))
    nova_meta = _as_dict(metadata.get("nova"))
    revision = nova_meta.get("revision", 0)
    document = NotebookDocument(
        revision=revision if isinstance(revision, int) and revision >= 0 else 0,
        extra_metadata={k: v for k, v in metadata.items() if k != "nova"},
    )
    seen_ids: set[str] = set()
    for raw_cell in raw["cells"]:
        if not isinstance(raw_cell, dict):
            raise NotebookFormatError("A notebook cell is not an object")
        cell_type = raw_cell.get("cell_type")
        cell_meta = _as_dict(raw_cell.get("metadata"))
        cell_nova = _as_dict(cell_meta.get("nova"))
        if cell_type == "markdown":
            kind = "markdown"
        elif cell_type == "code":
            kind = "sql" if cell_nova.get("kind") == "sql" else "python"
        elif cell_type == "raw":
            kind = "markdown"
        else:
            raise NotebookFormatError("A notebook cell has an unknown type")
        cell_id = raw_cell.get("id")
        if not isinstance(cell_id, str) or not _CELL_ID.match(cell_id) or cell_id in seen_ids:
            cell_id = new_cell_id()
        seen_ids.add(cell_id)
        name = cell_nova.get("name") if isinstance(cell_nova.get("name"), str) else None
        engine = cell_nova.get("engine") if cell_nova.get("engine") in SQL_ENGINES else None
        try:
            name = validate_name(name, kind=kind)
        except NotebookFormatError:
            name = None
        cell = Cell(
            id=cell_id,
            kind=kind,  # type: ignore[arg-type]
            source=_validate_source(_join_source(raw_cell.get("source", ""))),
            name=name,
            engine=(engine or "duckdb") if kind == "sql" else "duckdb",  # type: ignore[arg-type]
            extra_metadata={k: v for k, v in cell_meta.items() if k != "nova"},
        )
        document.cells.append(cell)
    # Imported names may collide; the first keeps its name.
    names: set[str] = set()
    for cell in document.cells:
        if cell.name and cell.name in names:
            cell.name = None
        elif cell.name:
            names.add(cell.name)
    _ensure_unique(document)
    return document


def _source_lines(source: str) -> list[str]:
    return source.splitlines(keepends=True)


def to_ipynb(document: NotebookDocument) -> str:
    """Write the document as nbformat 4.5 JSON, without outputs."""
    cells: list[dict[str, Any]] = []
    for cell in document.cells:
        meta = dict(cell.extra_metadata)
        nova: dict[str, Any] = {"kind": cell.kind}
        if cell.name:
            nova["name"] = cell.name
        if cell.kind == "sql":
            nova["engine"] = cell.engine
        meta["nova"] = nova
        if cell.kind == "markdown":
            cells.append(
                {
                    "cell_type": "markdown",
                    "id": cell.id,
                    "metadata": meta,
                    "source": _source_lines(cell.source),
                }
            )
        else:
            code_cell: dict[str, Any] = {
                "cell_type": "code",
                "id": cell.id,
                "metadata": meta,
                "source": _source_lines(cell.source),
                "execution_count": None,
                "outputs": [],
            }
            cells.append(code_cell)
    metadata = dict(document.extra_metadata)
    metadata.setdefault(
        "kernelspec", {"name": "nova-python", "display_name": "Python (Nova)", "language": "python"}
    )
    metadata.setdefault("language_info", {"name": "python"})
    metadata["nova"] = {"revision": document.revision, "format": 1}
    payload = {"nbformat": 4, "nbformat_minor": 5, "metadata": metadata, "cells": cells}
    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise NotebookFormatError("The notebook is larger than 8 MB")
    return text


def starter_document() -> NotebookDocument:
    """What a new notebook opens with: a heading and a first query."""
    return NotebookDocument(
        cells=[
            make_cell(kind="markdown", source="# Untitled analysis"),
            make_cell(kind="sql", name="result_1", source="SELECT 42 AS answer"),
            make_cell(kind="python", source="result_1.df()"),
        ]
    )


# ── Structural edits ─────────────────────────────────────────────────────────

EditOp = dict[str, Any]


def _bounded_index(index: Any, size: int) -> int:
    if not isinstance(index, int) or isinstance(index, bool):
        raise NotebookFormatError("A position must be a whole number")
    return max(0, min(index, size))


def apply_edits(document: NotebookDocument, edits: list[EditOp]) -> list[str]:
    """Apply edits in order; return the ids of cells whose source changed.

    Supported operations:

    * ``insert``: ``{index, cell: {kind, source?, name?, engine?, id?}}``
    * ``update``: ``{cell_id, source?, name?, kind?, engine?}``
    * ``delete``: ``{cell_id}``
    * ``move``: ``{cell_id, index}`` (the index in the list after removal)

    The whole list applies or none of it does: the caller edits a copy.
    """
    if not isinstance(edits, list) or not edits:
        raise NotebookFormatError("An edit lists at least one operation")
    if len(edits) > 500:
        raise NotebookFormatError("At most 500 operations per edit")
    changed: list[str] = []
    for edit in edits:
        if not isinstance(edit, dict):
            raise NotebookFormatError("An operation is an object")
        op = edit.get("op")
        if op == "insert":
            spec = edit.get("cell")
            if not isinstance(spec, dict):
                raise NotebookFormatError("insert needs a cell")
            cell_id = spec.get("id")
            if cell_id is not None and any(c.id == cell_id for c in document.cells):
                raise NotebookFormatError("A cell with this id already exists")
            cell = make_cell(
                kind=str(spec.get("kind", "python")),
                source=spec.get("source", "") or "",
                name=spec.get("name"),
                engine=spec.get("engine"),
                cell_id=cell_id,
            )
            index = _bounded_index(edit.get("index", len(document.cells)), len(document.cells))
            document.cells.insert(index, cell)
            changed.append(cell.id)
        elif op == "update":
            cell = document.cell(str(edit.get("cell_id")))
            before = cell.source_revision
            if "kind" in edit:
                kind = edit["kind"]
                if kind not in CELL_KINDS:
                    raise NotebookFormatError("A cell is Python, SQL or Markdown")
                cell.kind = kind
                if kind == "markdown":
                    cell.name = None
            if "engine" in edit:
                if edit["engine"] not in SQL_ENGINES:
                    raise NotebookFormatError("A SQL cell runs on DuckDB or Nova")
                cell.engine = edit["engine"]
            if "source" in edit:
                cell.source = _validate_source(edit["source"])
            if "name" in edit:
                cell.name = validate_name(edit["name"], kind=cell.kind)
            if cell.source_revision != before:
                changed.append(cell.id)
        elif op == "delete":
            index = document.index_of(str(edit.get("cell_id")))
            document.cells.pop(index)
        elif op == "move":
            index = document.index_of(str(edit.get("cell_id")))
            cell = document.cells.pop(index)
            target = _bounded_index(edit.get("index"), len(document.cells))
            document.cells.insert(target, cell)
        else:
            raise NotebookFormatError("Unknown notebook operation")
    _ensure_unique(document)
    return changed


def copy_document(document: NotebookDocument) -> NotebookDocument:
    return NotebookDocument(
        cells=[
            Cell(
                id=c.id,
                kind=c.kind,
                source=c.source,
                name=c.name,
                engine=c.engine,
                extra_metadata=dict(c.extra_metadata),
            )
            for c in document.cells
        ],
        revision=document.revision,
        extra_metadata=dict(document.extra_metadata),
    )
