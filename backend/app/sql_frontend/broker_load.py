"""Broker Load from a stage: ``LOAD LABEL ... (DATA INFILE("@stage/...") ...)``.

The engine form names storage URLs and puts credentials in ``WITH BROKER``.
In Nova every file path is a stage path written as a string, for example
``"@raw_files/sales/2026-*.csv"``, and the stage supplies the location and the
credentials:

* the AST builder finds every ``DATA INFILE`` path and the job properties and
  refuses storage URLs, a broker name or broker properties (credentials), the
  Spark (``WITH RESOURCE``) and Hive-table (``DATA FROM TABLE``) forms and
  unknown job properties, before planning;
* the planner replaces each path with a stage slot whose access is ``read``,
  and the ``WITH BROKER`` clause (or the place it belongs) with one more read
  slot on the first path's stage that carries only the credentials;
* ``StageRuntime`` authorizes the read for the active role on every stage
  before any secret is resolved; the translator then renders the physical
  paths and ``WITH BROKER (<credentials>)`` immediately before the engine call.

The load job then runs inside the engine as the caller's session submitted it,
with the caller's active role: it is a one-off job like ``INSERT``, not a
standing background job that would run again later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import ParsedStatement
from app.sql_frontend.unload import _SECRET_HINTS, decode_literal

FORM = "LOAD LABEL"

#: Job properties of the pinned engine (``LoadStmt``, BROKER_LOAD.md). Anything
#: else is refused, so a credential or broker setting never passes next to the
#: stage's own.
LOAD_PROPERTIES = frozenset(
    {
        "timeout",
        "max_filter_ratio",
        "log_rejected_record_num",
        "load_mem_limit",
        "strict_mode",
        "timezone",
        "priority",
        "partial_update",
        "partial_update_mode",
        "merge_condition",
        "jsonpaths",
        "json_root",
        "strip_outer_array",
        "warehouse",
    }
)

#: ``@stage``, ``@schema.stage`` or ``@database.schema.stage``, then ``/``.
_PATH = re.compile(
    r"@(?P<name>[A-Za-z0-9_$-]+(?:\.[A-Za-z0-9_$-]+){0,2})/(?P<rest>.+)", re.DOTALL
)
#: Characters a stage file path may hold: names, globs (``* ? [ ] { } ^ ,``),
#: ``=`` and ``%`` (partition folders such as ``dt=2026-01-01 00%3A00``) and spaces.
_PATH_TEXT = re.compile(r"[A-Za-z0-9_.=%*?\[\]{}^,: +-]+")


@dataclass(frozen=True, slots=True)
class LoadPath:
    """One ``DATA INFILE`` path, rendered as the stage's physical path later."""

    kind: str
    path: str


@dataclass(frozen=True, slots=True)
class LoadBroker:
    """The ``WITH BROKER`` clause: rendered with the stage's credentials later.

    ``prefix`` is a space when the statement had no ``WITH BROKER`` and the
    clause is inserted after the data descriptions.
    """

    kind: str
    path: str
    prefix: str = ""


@dataclass(frozen=True, slots=True)
class LoadTarget:
    clause: LoadPath | LoadBroker
    start: int
    end: int
    qualifier: tuple[str, ...]
    segments: tuple[str, ...]
    is_directory: bool


def is_broker_load(parsed: ParsedStatement) -> bool:
    return type(parsed.statement_context).__name__ == "LoadStatementContext"


def _path(literal: Any) -> tuple[tuple[str, ...], tuple[str, ...], bool, str]:
    value = decode_literal(literal.getText()).strip()
    if not value.startswith("@"):
        raise SemanticError(
            f"{FORM} reads files only from a stage. Write each path as "
            "\"@<stage>/<folder>/<file>\", for example \"@raw_files/sales/2026-*.csv\"; "
            "storage URLs and local paths are refused"
        )
    match = _PATH.fullmatch(value)
    if match is None:
        raise SemanticError(
            f"{FORM} needs a file or pattern after the stage name, for example "
            "\"@raw_files/sales.csv\" or \"@raw_files/sales/*\""
        )
    rest = match.group("rest")
    is_directory = rest.endswith("/")
    segments = tuple(rest[:-1].split("/") if is_directory else rest.split("/"))
    for segment in segments:
        if (
            not segment
            or segment in {".", ".."}
            or segment.strip() != segment
            or not _PATH_TEXT.fullmatch(segment)
        ):
            raise SemanticError(
                f"{FORM}: a stage path may use letters, digits, '_', '-', '.', '=', '%', "
                "spaces and the wildcards * ? [ ] { } ^ between slashes, with no empty, "
                "'.' or '..' folders"
            )
    return tuple(match.group("name").split(".")), segments, is_directory, value


def _properties(property_list: Any) -> None:
    seen: set[str] = set()
    for prop in property_list.property_() if property_list is not None else []:
        written = decode_literal(prop.key.getText()).strip()
        key = written.lower()
        if key not in LOAD_PROPERTIES:
            if any(hint in key for hint in _SECRET_HINTS):
                # Not echoed: the key names a credential, and errors are audited.
                raise SemanticError(
                    f"{FORM} from a stage takes no storage location, credential or broker "
                    "properties: the stage supplies them"
                )
            raise SemanticError(
                f"{FORM} does not support the property '{written}'. Supported properties: "
                + ", ".join(sorted(LOAD_PROPERTIES))
            )
        if key in seen:
            raise SemanticError(f"{FORM} sets the property '{key}' more than once")
        seen.add(key)


def broker_load_targets(parsed: ParsedStatement) -> tuple[LoadTarget, ...]:
    """Every file path and the broker slot of a ``LOAD LABEL``, validated.

    Returns ``()`` for any other statement. Raises ``SemanticError`` on a
    refusal; no refused path, key or value is echoed.
    """
    root = parsed.statement_context
    if not is_broker_load(parsed):
        return ()
    if root.resource is not None:
        raise SemanticError(
            "LOAD ... WITH RESOURCE (Spark Load) is not available in Nova. "
            "Use WITH BROKER and stage paths: the engine reads the files itself"
        )
    if root.system is not None:
        raise SemanticError(f"{FORM} does not support BY <system>")
    if root.data is None:
        raise SemanticError(
            f"{FORM} needs at least one DATA INFILE(\"@stage/...\") INTO TABLE ... description"
        )
    broker = root.broker
    if broker is not None and (broker.name is not None or broker.props is not None):
        raise SemanticError(
            f"{FORM} from a stage takes no broker name or broker properties: the stage "
            "supplies the storage location and credentials. Write WITH BROKER alone, "
            "or leave it out"
        )
    _properties(root.props)
    targets: list[LoadTarget] = []
    for desc in (n for n in walk_nodes(root.data) if type(n).__name__ == "DataDescContext"):
        if desc.srcTableName is not None:
            raise SemanticError(
                "DATA FROM TABLE loads from a Hive table through Spark Load and is not "
                "available in Nova. Load files with DATA INFILE(\"@stage/...\")"
            )
        for literal in desc.srcFiles.string():
            qualifier, segments, is_directory, path = _path(literal)
            targets.append(
                LoadTarget(
                    LoadPath("load_path", path),
                    literal.start.start,
                    literal.stop.stop + 1,
                    qualifier,
                    segments,
                    is_directory,
                )
            )
    if not targets:
        raise SemanticError(f"{FORM} needs at least one DATA INFILE path")
    first = targets[0]
    if broker is not None:
        start, end, prefix = broker.start.start, broker.stop.stop + 1, ""
    else:
        start = end = root.data.stop.stop + 1
        prefix = " "
    targets.append(
        LoadTarget(
            LoadBroker("load_broker", first.clause.path, prefix),
            start,
            end,
            first.qualifier,
            first.segments,
            first.is_directory,
        )
    )
    return tuple(targets)


def physical_load_path(bucket: str, base_prefix: str, path_parts: list[str],
                       is_directory: bool) -> str:
    """``s3://bucket/<stage root>/<folders>/<file or pattern>``."""
    parts = [base_prefix.strip("/"), *path_parts]
    path = "/".join(part for part in parts if part)
    if is_directory:
        path += "/"
    return f"s3://{bucket}/{path}"


def render_load_clause(
    clause: LoadPath | LoadBroker, physical_path: str, credentials: list[str]
) -> str:
    """The engine text for one slot. A broker slot carries secrets: execution only."""
    if isinstance(clause, LoadPath):
        return "'" + physical_path.replace("\\", "\\\\").replace("'", "''") + "'"
    return f"{clause.prefix}WITH BROKER ({', '.join(credentials)})"
