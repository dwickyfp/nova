"""What a pipe is, decoded from its statement: pure functions, no I/O.

``CREATE PIPE`` keeps the StarRocks syntax. Its body is one
``INSERT INTO <table> [(columns)] SELECT ... FROM <files>`` where ``<files>`` is
either ``FILES("path" = "@stage/dir/*.parquet", "format" = ...)`` or the Nova
shorthand ``@stage/dir/*.csv``. Nova supplies the storage location and the
credentials of the stage at each run, so the statement may not carry any.

At each run a batch of files replaces the source: the stored INSERT is parsed
again, the source relation becomes a union of one ``FILES()`` read per file,
and ``WITH LABEL`` names the batch. The label is a digest of the pipe and its
exact file versions, so the engine refuses to load the same batch twice.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.sql_frontend.errors import SemanticError

#: Defaults are the StarRocks ones: poll every 300 s, at most 1 GB or 256 files a batch.
DEFAULT_POLL_INTERVAL = 300
DEFAULT_BATCH_SIZE = 1024**3
DEFAULT_BATCH_FILES = 256
MAX_POLL_INTERVAL = 86400
MAX_BATCH_FILES = 100000
PROPERTY_NAMES = ("AUTO_INGEST", "POLL_INTERVAL", "BATCH_SIZE", "BATCH_FILES")
_SIZE_UNITS = {
    "": 1, "B": 1, "K": 1024, "KB": 1024, "M": 1024**2, "MB": 1024**2,
    "G": 1024**3, "GB": 1024**3, "T": 1024**4, "TB": 1024**4,
}
FORMATS = frozenset({"parquet", "orc", "csv", "json", "avro"})


class PipeDefinitionError(SemanticError):
    """The pipe statement cannot be run as written; the message says why."""


@dataclass(frozen=True, slots=True)
class PipeProperties:
    auto_ingest: bool = True
    poll_interval: int = DEFAULT_POLL_INTERVAL
    batch_size: int = DEFAULT_BATCH_SIZE
    batch_files: int = DEFAULT_BATCH_FILES

    def as_text(self) -> dict[str, str]:
        return {
            "AUTO_INGEST": "TRUE" if self.auto_ingest else "FALSE",
            "POLL_INTERVAL": str(self.poll_interval),
            "BATCH_SIZE": format_size(self.batch_size),
            "BATCH_FILES": str(self.batch_files),
        }


@dataclass(frozen=True, slots=True)
class PipeSource:
    """Where the files are, as written: resolved to one registered stage at execution.

    ``path`` is every segment after the stage name (possibly a ``database`` and
    ``schema`` prefix the stage lookup consumes). ``pattern`` is the file glob.
    ``options`` are the remaining ``FILES()`` properties of the ``files`` form.
    """

    form: str
    stage_name: str
    path: tuple[str, ...]
    pattern: str
    format: str
    options: tuple[tuple[str, str], ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "form": self.form, "stage_name": self.stage_name, "path": list(self.path),
            "pattern": self.pattern, "format": self.format,
            "options": [list(item) for item in self.options],
        }


@dataclass(frozen=True, slots=True)
class PipeDefinition:
    name: tuple[str, ...]
    insert_sql: str = field(repr=False)
    target: tuple[str, ...]
    source: PipeSource
    properties: PipeProperties
    or_replace: bool = False
    if_not_exists: bool = False


def _text(parsed: Any, node: Any) -> str:
    return parsed.normalized_sql[node.start.start: node.stop.stop + 1]


def string_value(text: str) -> str:
    """The value of a quoted SQL string literal, as the engine reads it."""
    if len(text) < 2 or text[0] not in "'\"" or text[-1] != text[0]:
        raise PipeDefinitionError("Expected a quoted string")
    body = text[1:-1]
    if "\\" in body:
        raise PipeDefinitionError("Backslash escapes are not supported in pipe properties")
    return body.replace(text[0] * 2, text[0])


def property_pairs(parsed: Any, property_list: Any) -> list[tuple[str, str]]:
    return [
        (string_value(_text(parsed, item.key)), string_value(_text(parsed, item.value)))
        for item in property_list.property_()
    ]


def parse_size(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+)\s*([A-Za-z]*)\s*", value)
    if not match or match.group(2).upper() not in _SIZE_UNITS:
        raise PipeDefinitionError(
            f"BATCH_SIZE must be a size such as 1GB, 512MB or 1048576, not {value!r}"
        )
    size = int(match.group(1)) * _SIZE_UNITS[match.group(2).upper()]
    if size <= 0:
        raise PipeDefinitionError("BATCH_SIZE must be greater than zero")
    return size


def format_size(size: int) -> str:
    for unit in ("TB", "GB", "MB", "KB"):
        if size % _SIZE_UNITS[unit] == 0:
            return f"{size // _SIZE_UNITS[unit]}{unit}"
    return str(size)


def _positive(name: str, value: str, upper: int) -> int:
    if not re.fullmatch(r"\s*\d+\s*", value) or not 0 < int(value) <= upper:
        raise PipeDefinitionError(f"{name} must be a whole number from 1 to {upper}")
    return int(value)


def parse_properties(
    pairs: Iterable[tuple[str, str]], base: PipeProperties | None = None,
) -> PipeProperties:
    """Apply ``"KEY" = "value"`` pairs to ``base`` (the defaults when ``None``)."""
    current = base or PipeProperties()
    auto_ingest, poll_interval = current.auto_ingest, current.poll_interval
    batch_size, batch_files = current.batch_size, current.batch_files
    seen: set[str] = set()
    for raw_key, value in pairs:
        key = raw_key.strip().upper()
        if key in seen:
            raise PipeDefinitionError(f"Property {key} is given twice")
        seen.add(key)
        if key == "AUTO_INGEST":
            if value.strip().upper() not in {"TRUE", "FALSE"}:
                raise PipeDefinitionError("AUTO_INGEST must be TRUE or FALSE")
            auto_ingest = value.strip().upper() == "TRUE"
        elif key == "POLL_INTERVAL":
            poll_interval = _positive(key, value, MAX_POLL_INTERVAL)
        elif key == "BATCH_SIZE":
            batch_size = parse_size(value)
        elif key == "BATCH_FILES":
            batch_files = _positive(key, value, MAX_BATCH_FILES)
        else:
            raise PipeDefinitionError(
                f"Unknown pipe property {raw_key!r}; use " + ", ".join(PROPERTY_NAMES)
            )
    return PipeProperties(auto_ingest, poll_interval, batch_size, batch_files)


def name_parts(node: Any) -> tuple[str, ...]:
    from app.sql_frontend.streams import name_parts as parts

    return parts(node)


def split_name(parts: Sequence[str], database: str | None, what: str) -> tuple[str, str]:
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) == 1 and database:
        return database, parts[0]
    if len(parts) == 1:
        raise PipeDefinitionError(f"Select a database, or name the {what} as database.name")
    raise PipeDefinitionError(f"A {what} name has at most two parts: database.name")


def _stage_reference(text: str) -> Any:
    """Decode a ``@stage/...`` path with the central stage-reference grammar."""
    from app.sql_frontend.errors import SQLSyntaxError
    from app.sql_frontend.parser import parse_statement
    from app.sql_frontend.stages import stage_view

    try:
        view = stage_view(parse_statement("LIST " + text))
    except (SQLSyntaxError, ValueError):
        raise PipeDefinitionError(f"{text!r} is not a stage path such as @stage/folder/*.csv") \
            from None
    if len(view.stage_refs) != 1:
        raise PipeDefinitionError(f"{text!r} is not a stage path such as @stage/folder/*.csv")
    return view.stage_refs[0]


def _source_from_reference(reference: Any, form: str, file_format: str | None,
                           options: Sequence[tuple[str, str]]) -> PipeSource:
    from app.modules.query.dialect.translator import detect_format_from_filename

    if reference.file_name and not reference.is_directory:
        path, pattern = tuple(reference.path_parts), reference.file_name
    else:
        path, pattern = tuple(reference.path_parts), "*"
    chosen = (file_format or "").strip().lower()
    if not chosen:
        if (pattern == "*" or "." not in pattern) and form == "stage":
            raise PipeDefinitionError(
                "Name the files with their extension, for example @stage/folder/*.csv, "
                'or use FILES("path" = "@stage/folder/*", "format" = "csv")'
            )
        if pattern == "*" or "." not in pattern:
            raise PipeDefinitionError(
                'Name the file format: add "format" = "parquet" (or csv, orc, json)'
            )
        chosen = detect_format_from_filename(pattern)
    if chosen not in FORMATS:
        raise PipeDefinitionError(
            f"File format {chosen!r} is not supported; use " + ", ".join(sorted(FORMATS))
        )
    return PipeSource(form, reference.stage_name, path, pattern, chosen, tuple(options))


def _files_source(parsed: Any, node: Any) -> PipeSource:
    from app.common.sql_guard import is_credential_property

    pairs = property_pairs(parsed, node.propertyList())
    keys = [key.strip().lower() for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise PipeDefinitionError("A FILES() property is given twice")
    found = dict(zip(keys, (value for _, value in pairs), strict=True))
    path = found.pop("path", None)
    if not path or not path.strip().startswith("@"):
        raise PipeDefinitionError(
            'A pipe reads files from a stage: FILES("path" = "@stage/folder/*.parquet", ...)'
        )
    for key in found:
        if is_credential_property(key) or key.startswith(("aws.", "azure.", "gcp.", "fs.")):
            raise PipeDefinitionError(
                f"Remove {key!r}: Nova supplies the stage's storage location and "
                "credentials at each run"
            )
    file_format = found.pop("format", None)
    reference = _stage_reference(path.strip())
    return _source_from_reference(
        reference, "files", file_format, sorted(found.items()),
    )


def source_nodes(insert: Any) -> list[Any]:
    """Every file source in the INSERT's query: FILES() or @stage relations."""
    from app.sql_frontend.antlr_utils import walk_nodes

    return [
        node for node in walk_nodes(insert.queryStatement())
        if type(node).__name__ in {"FileTableFunctionContext", "StageAtomContext"}
    ]


def decode_insert(parsed: Any, insert: Any) -> tuple[tuple[str, ...], PipeSource]:
    """Check the pipe's INSERT; return its target name parts and its file source."""
    from app.sql_frontend.antlr_utils import walk_nodes

    if insert.explainDesc() is not None:
        raise PipeDefinitionError("A pipe's statement cannot be EXPLAIN")
    if insert.OVERWRITE() is not None:
        raise PipeDefinitionError("A pipe appends: use INSERT INTO, not INSERT OVERWRITE")
    if insert.qualifiedName() is None or insert.writeBranch() is not None:
        raise PipeDefinitionError("A pipe loads into a table: INSERT INTO <table> SELECT ...")
    if any(item.LABEL() is not None for item in insert.insertLabelOrColumnAliases()):
        raise PipeDefinitionError(
            "Remove WITH LABEL: the pipe labels each batch so that it loads once"
        )
    if insert.queryStatement() is None:
        raise PipeDefinitionError("A pipe loads with INSERT INTO ... SELECT ... FROM files")
    sources = source_nodes(insert)
    stage_refs = [
        node for node in walk_nodes(insert.queryStatement())
        if type(node).__name__ == "StageReferenceContext"
    ]
    if len(sources) != 1 or len(stage_refs) > 1 or (
        stage_refs and type(sources[0]).__name__ != "StageAtomContext"
    ):
        raise PipeDefinitionError(
            "A pipe's SELECT reads exactly one file source: "
            'FILES("path" = "@stage/...") or @stage/...'
        )
    node = sources[0]
    if type(node).__name__ == "FileTableFunctionContext":
        source = _files_source(parsed, node)
    else:
        from app.sql_frontend.stages import stage_view

        references = stage_view(parsed).stage_refs
        if len(references) != 1:
            raise PipeDefinitionError("A pipe's SELECT reads exactly one stage path")
        source = _source_from_reference(references[0], "stage", None, ())
    return name_parts(insert.qualifiedName()), source


def decode_create(parsed: Any, database: str | None) -> PipeDefinition:
    root = parsed.statement_context
    or_replace = root.orReplace().REPLACE() is not None
    if_not_exists = root.ifNotExists().EXISTS() is not None
    if or_replace and if_not_exists:
        raise PipeDefinitionError("Use either OR REPLACE or IF NOT EXISTS, not both")
    pairs = property_pairs(parsed, root.properties().propertyList()) if root.properties() else []
    properties = parse_properties(pairs)
    insert = root.insertStatement()
    target, source = decode_insert(parsed, insert)
    name = name_parts(root.qualifiedName())
    split_name(name, database, "pipe")
    return PipeDefinition(
        name=name, insert_sql=_text(parsed, insert), target=target, source=source,
        properties=properties, or_replace=or_replace, if_not_exists=if_not_exists,
    )


# ── Files and batches ─────────────────────────────────────────────────────


def glob_regex(segments: Sequence[str]) -> re.Pattern[str]:
    """Match a path relative to the stage, one glob per segment (``*`` stays in a folder)."""
    parts = []
    for segment in segments:
        out, index = [], 0
        while index < len(segment):
            char = segment[index]
            if char == "*":
                out.append("[^/]*")
            elif char == "?":
                out.append("[^/]")
            elif char == "[":
                end = segment.find("]", index + 1)
                if end == -1:
                    out.append(re.escape(char))
                else:
                    body = segment[index + 1:end]
                    negate = body.startswith(("!", "^"))
                    body = re.escape(body[1:] if negate else body).replace("\\-", "-")
                    out.append(f"[{'^' if negate else ''}{body}]")
                    index = end
            else:
                out.append(re.escape(char))
            index += 1
        parts.append("".join(out))
    return re.compile("/".join(parts))


def list_prefix(segments: Sequence[str]) -> str:
    """The literal folder prefix to list before the first glob character."""
    literal = []
    for segment in segments:
        if any(char in segment for char in "*?["):
            break
        literal.append(segment)
    if len(literal) == len(segments):
        literal = literal[:-1]
    return "/".join(literal) + ("/" if literal else "")


@dataclass(frozen=True, slots=True)
class PipeFile:
    name: str
    version: str
    size: int


def batches(files: Sequence[PipeFile], *, max_files: int, max_bytes: int) -> list[list[PipeFile]]:
    """Group files in order: a batch closes at ``max_files`` or before exceeding ``max_bytes``.

    A file larger than ``max_bytes`` is a batch of its own, as in StarRocks.
    """
    result: list[list[PipeFile]] = []
    current: list[PipeFile] = []
    size = 0
    for item in files:
        if current and (len(current) >= max_files or size + item.size > max_bytes):
            result.append(current)
            current, size = [], 0
        current.append(item)
        size += item.size
    if current:
        result.append(current)
    return result


def batch_label(pipe_id: UUID | str, files: Iterable[PipeFile]) -> str:
    """The engine label of a batch: the same pipe and file versions, the same label."""
    members = sorted(f"{item.name}\0{item.version}" for item in files)
    if not members:
        raise ValueError("A batch has at least one file")
    digest = hashlib.sha256("\n".join([str(pipe_id), *members]).encode()).hexdigest()[:40]
    return f"nova_pipe_{UUID(str(pipe_id)).hex[:12]}_{digest}"


def label_position(insert: Any) -> int:
    anchor = insert.partitionNames() or insert.qualifiedName()
    return int(anchor.stop.stop) + 1


def render_batch(insert_sql: str, label: str, reads: Sequence[str]) -> str:
    """The stored INSERT with ``reads`` (one relation SQL per file) as its source."""
    from app.sql_frontend.parser import parse_statement

    if not reads:
        raise ValueError("A batch reads at least one file")
    parsed = parse_statement(insert_sql)
    insert = parsed.statement_context
    if type(insert).__name__ != "InsertStatementContext":
        raise PipeDefinitionError("The stored pipe statement is not an INSERT")
    sources = source_nodes(insert)
    if len(sources) != 1:
        raise PipeDefinitionError("The stored pipe statement reads more than one source")
    node = sources[0]
    union = " UNION ALL ".join(f"SELECT * FROM {read}" for read in reads)
    if type(node).__name__ == "FileTableFunctionContext":
        start, end = node.start.start, node.propertyList().stop.stop + 1
        replacement = f"({union})"
        if node.alias is None:
            replacement += " AS `__nova_pipe_files`"
    else:
        from app.sql_frontend.stages import stage_view

        reference = node.stageReference()
        start, end = reference.start.start, reference.stop.stop + 1
        replacement = f"({union})"
        if node.alias is None:
            # The worksheet names an unaliased @stage relation after its stage.
            stage = stage_view(parsed).stage_refs[0].stage_name
            replacement += " AS `" + stage.replace("`", "``") + "`"
    sql = parsed.normalized_sql
    position = label_position(insert)
    if position > start:
        raise PipeDefinitionError("The stored pipe statement has an unexpected shape")
    return (
        sql[:position] + f" WITH LABEL {label} " + sql[position:start]
        + replacement + sql[end:]
    )
