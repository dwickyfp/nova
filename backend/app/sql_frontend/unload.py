"""Unload to a stage: ``INSERT INTO FILES``, ``SELECT ... INTO OUTFILE`` and ``EXPORT TABLE``.

The engine forms write to a storage URL with credentials in the statement.
In Nova the target is always a stage path written as a string, for example
``'@sales_exports/2026/'``, and the stage supplies the location and the
credentials:

* the parser finds the target clause and checks every option against the
  documented unload options; a storage URL, a credential or broker property,
  or an unknown option is refused here, before planning;
* the planner replaces the whole clause with one stage slot whose access is
  ``write``, carrying only the stage path and the allowed options;
* ``StageRuntime`` authorizes the write for the active role before any secret
  is resolved; the translator then renders the clause with the physical path
  and the credentials, immediately before the engine call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import ParsedStatement

#: Options each form accepts, from the pinned engine's documentation and
#: ``OutFileClause``. Anything else is refused, so a location, credential or
#: broker setting can never reach the engine next to the stage's own.
FILES_OPTIONS = frozenset(
    {
        "format",
        "compression",
        "partition_by",
        "single",
        "target_max_file_size",
        "csv.column_separator",
        "csv.row_delimiter",
        "csv.include_header",
        "csv.enclose",
        "csv.escape",
        "parquet.use_legacy_encoding",
        "parquet.version",
    }
)
OUTFILE_OPTIONS = frozenset(
    {
        "column_separator",
        "line_delimiter",
        "max_file_size",
        "compression_type",
        "use_dictionary",
        "max_row_group_bytes",
    }
)
EXPORT_OPTIONS = frozenset(
    {"column_separator", "line_delimiter", "load_mem_limit", "timeout", "include_query_id"}
)

_SECRET_HINTS = (
    "aws.", "azure.", "gcp.", "fs.", "hadoop.", "kerberos", "broker", "username",
    "user", "password", "key", "secret", "token", "credential", "endpoint", "region",
    "path",
)

#: ``@stage``, ``@schema.stage`` or ``@database.schema.stage``, then ``/``.
_TARGET = re.compile(
    r"@(?P<name>[A-Za-z0-9_$-]+(?:\.[A-Za-z0-9_$-]+){0,2})/(?P<rest>.*)", re.DOTALL
)
_SEGMENT = re.compile(r"[A-Za-z0-9_.=-]+")

FORMS = {
    "files": "INSERT INTO FILES",
    "outfile": "SELECT ... INTO OUTFILE",
    "export": "EXPORT TABLE",
}


@dataclass(frozen=True, slots=True)
class UnloadClause:
    """A validated unload target. Holds no location or secret.

    ``options`` keep the user's key and value literals exactly as written, so
    the engine decodes separators and escapes as it would for native SQL.
    """

    kind: str
    path: str
    options: tuple[tuple[str, str], ...] = ()
    file_format: str | None = None
    mode: str | None = None


@dataclass(frozen=True, slots=True)
class UnloadTarget:
    clause: UnloadClause
    start: int
    end: int
    qualifier: tuple[str, ...]
    segments: tuple[str, ...]
    is_directory: bool


def decode_literal(text: str) -> str:
    """The value of a SQL string literal (quotes, doubled quotes, backslashes)."""
    quote, body = text[0], text[1:-1]
    out: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            nxt = body[index + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r", "0": "\0"}.get(nxt, nxt))
            index += 2
            continue
        if char == quote and index + 1 < len(body) and body[index + 1] == quote:
            out.append(quote)
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _clauses(parsed: ParsedStatement) -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    root = parsed.statement_context
    for node in walk_nodes(root):
        kind = type(node).__name__
        if kind == "InsertStatementContext" and node.FILES() is not None:
            found.append(("files", node))
        elif kind == "OutfileContext":
            found.append(("outfile", node))
        elif kind == "ExportStatementContext":
            found.append(("export", node))
    return found


def has_unload_clause(parsed: ParsedStatement) -> bool:
    return bool(_clauses(parsed))


def _option_error(form: str, key: str, allowed: frozenset[str]) -> SemanticError:
    lowered = key.lower()
    if any(hint in lowered for hint in _SECRET_HINTS):
        # The key is not echoed: it names a credential, and error text is
        # returned and audited.
        return SemanticError(
            f"{form} to a stage takes no storage location, credential or broker "
            "options: the stage supplies them"
        )
    return SemanticError(
        f"{form} does not support the option '{key}'. Supported options: "
        + ", ".join(sorted(allowed))
    )


def _options(form: str, property_list: Any, allowed: frozenset[str]) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    seen: set[str] = set()
    for prop in property_list.property_() if property_list is not None else []:
        key_text, value_text = prop.key.getText(), prop.value.getText()
        key = decode_literal(key_text).strip().lower()
        if key not in allowed:
            raise _option_error(form, decode_literal(key_text).strip(), allowed)
        if key in seen:
            raise SemanticError(f"{form} sets the option '{key}' more than once")
        seen.add(key)
        options.append((key_text, value_text))
    return options


def _target(form: str, literal: Any) -> tuple[tuple[str, ...], tuple[str, ...], bool, str]:
    value = decode_literal(literal.getText()).strip()
    match = _TARGET.fullmatch(value)
    if not value.startswith("@"):
        raise SemanticError(
            f"{form} writes only to a stage. Write the target as '@<stage>/<folder>/', "
            "for example '@sales_exports/daily/'; storage URLs and local paths are refused"
        )
    if match is None:
        raise SemanticError(
            f"{form} needs a folder or file name prefix after the stage name, for example "
            "'@sales_exports/daily/' or '@sales_exports/daily/orders_'"
        )
    rest = match.group("rest")
    is_directory = rest == "" or rest.endswith("/")
    segments = tuple(part for part in rest.split("/")) if rest else ()
    if is_directory and segments:
        segments = segments[:-1]
    for segment in segments:
        if not _SEGMENT.fullmatch(segment) or segment in {".", ".."}:
            raise SemanticError(
                f"{form}: the stage path may use letters, digits, '_', '-', '.' and '=' "
                "between slashes, with no empty, '.' or '..' folders"
            )
    return tuple(match.group("name").split(".")), segments, is_directory, value


def unload_targets(parsed: ParsedStatement) -> tuple[UnloadTarget, ...]:
    """Every unload clause, validated. Raises ``SemanticError`` on a refusal."""
    targets: list[UnloadTarget] = []
    for kind, node in _clauses(parsed):
        form = FORMS[kind]
        if kind == "files":
            plist = node.propertyList()
            if isinstance(plist, list):
                plist = plist[0] if plist else None
            path_literal = None
            remaining = []
            for prop in plist.property_() if plist is not None else []:
                if decode_literal(prop.key.getText()).strip().lower() == "path":
                    if path_literal is not None:
                        raise SemanticError(f"{form} sets the option 'path' more than once")
                    path_literal = prop.value
                else:
                    remaining.append(prop)
            if path_literal is None:
                raise SemanticError(
                    f"{form} needs a 'path' that names a stage folder, for example "
                    "\"path\" = \"@sales_exports/daily/\""
                )
            qualifier, segments, is_directory, path = _target(form, path_literal)

            class _Props:
                def property_(self, props=remaining):  # noqa: N802
                    return props

            options = _options(form, _Props(), FILES_OPTIONS)
            start = node.FILES().getSymbol().start
            end = plist.stop.stop + 1
            clause = UnloadClause(kind, path, tuple(options))
        elif kind == "outfile":
            qualifier, segments, is_directory, path = _target(form, node.string())
            properties = node.properties()
            options = _options(
                form, properties.propertyList() if properties else None, OUTFILE_OPTIONS
            )
            file_format = node.fileFormat()
            fmt = None
            if file_format is not None:
                fmt = file_format.getChild(2).getText()
            start, end = node.start.start, node.stop.stop + 1
            clause = UnloadClause(kind, path, tuple(options), file_format=fmt)
        else:
            qualifier, segments, is_directory, path = _target(form, node.string())
            broker = node.brokerDesc()
            if broker is not None and (broker.name is not None or broker.props is not None):
                raise SemanticError(
                    "EXPORT TABLE to a stage takes no broker name or broker properties: "
                    "the stage supplies the storage location and credentials. "
                    "Write WITH BROKER alone, or leave it out"
                )
            properties = node.properties()
            options = _options(
                form, properties.propertyList() if properties else None, EXPORT_OPTIONS
            )
            mode = None
            if node.SYNC() is not None:
                mode = "SYNC"
            elif node.ASYNC() is not None:
                mode = "ASYNC"
            start, end = node.TO().getSymbol().start, node.stop.stop + 1
            clause = UnloadClause(kind, path, tuple(options), mode=mode)
        targets.append(
            UnloadTarget(clause, start, end, qualifier, segments, is_directory)
        )
    return tuple(targets)


def _assignments(options: tuple[tuple[str, str], ...]) -> list[str]:
    return [f"{key}={value}" for key, value in options]


def render_unload_clause(
    clause: UnloadClause, physical_path: str, credentials: list[str]
) -> str:
    """The engine clause for one target. Carries secrets: execution only."""

    def quoted(value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"

    if clause.kind == "files":
        params = [f"'path'={quoted(physical_path)}", *_assignments(clause.options), *credentials]
        return f"FILES({', '.join(params)})"
    if clause.kind == "outfile":
        text = f"INTO OUTFILE {quoted(physical_path)}"
        if clause.file_format:
            text += f" FORMAT AS {clause.file_format}"
        params = [*_assignments(clause.options), *credentials]
        return text + f" PROPERTIES({', '.join(params)})"
    if clause.kind == "export":
        text = f"TO {quoted(physical_path)}"
        if clause.mode:
            text += f" WITH {clause.mode} MODE"
        if clause.options:
            text += f" PROPERTIES({', '.join(_assignments(clause.options))})"
        return text + f" WITH BROKER ({', '.join(credentials)})"
    raise ValueError("Unknown unload form")


def physical_unload_path(
    clause: UnloadClause, bucket: str, base_prefix: str, path_parts: list[str],
    is_directory: bool,
) -> str:
    """``s3://bucket/<stage root>/<folders>/[prefix]``; ``s3a://`` for EXPORT."""
    scheme = "s3a" if clause.kind == "export" else "s3"
    parts = [base_prefix.strip("/"), *path_parts]
    path = "/".join(part for part in parts if part)
    if is_directory or not path_parts:
        path += "/"
    return f"{scheme}://{bucket}/{path}"


# ── SHOW EXPORT: physical paths become stage paths ───────────────────────────


async def export_location_map(username: str, role: str | None) -> list[tuple[str, str]]:
    """Physical roots of the stages the caller may read, longest first."""
    from app.core.config import require_storage_connection
    from app.modules.stages.service import caller_actor, stage_service

    rows = await stage_service.list_stages(caller_actor(username, role))
    mapping: dict[str, str] = {}
    for row in rows:
        try:
            bucket = require_storage_connection(row["storage_connection"]).bucket
        except Exception:  # noqa: BLE001 - an unconfigured connection maps nothing
            continue
        prefix = (row.get("base_prefix") or "").strip("/") or (
            f"{row['database_name']}/{row['schema_name']}/{row['name']}"
        )
        for scheme in ("s3", "s3a"):
            mapping[f"{scheme}://{bucket}/{prefix}"] = "@" + str(row["name"])
    return sorted(mapping.items(), key=lambda item: -len(item[0]))


_STORAGE_URL = re.compile(r"\b(?:s3a?|s3n|hdfs|oss|cosn|ks3|bos|afs|viewfs|gs|wasbs?|abfss?|file)://[^\s\"',;]*")


def logical_export_text(text: str, location_map: list[tuple[str, str]]) -> str:
    """A ``SHOW EXPORT`` value with stage paths for known stages, others hidden."""
    for physical, logical in location_map:
        text = re.sub(re.escape(physical) + r"(?=/|$|[\s\"',;])", logical, text)
    return _STORAGE_URL.sub("<external location>", text)
