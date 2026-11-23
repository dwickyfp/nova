"""Native ``FILES()`` reads: files are read through a stage, never an address.

The engine's ``FILES()`` table function (and ``DESC FILES(...)``) reads
whatever location its properties name: a storage URL, an ``hdfs://`` host, an
``aws.s3.endpoint`` on the internal network, the backend's own instance
credentials. A statement
that names one makes the engine connect there on the caller's behalf, outside
stage governance, so a user could reach internal hosts (SSRF) or move data to
storage Nova does not know.

Nova reads files through a stage instead: ``SELECT ... FROM @stage/path``,
``COPY INTO``, Broker Load or a pipe. The stage supplies the location and the
credentials, and the stage runtime authorizes the active role before any
secret is resolved. Writing ``FILES()`` in SQL is therefore refused at AST
building, for every statement and consumer, before planning or any I/O.

Pipes keep their own ``FILES("path" = "@stage/...")`` form: the pipe
validator accepts only a stage path there and the pipes service renders the
physical call. ``INSERT INTO FILES(...)`` is an unload and is validated by
:mod:`app.sql_frontend.unload`, which also allows only a stage path.

One Nova-generated statement still reads ``FILES()``: a database migration
imports the rows it exported to its own transfer folder. The migration service
wraps that import in :func:`internal_file_read`, naming the configured
transfer location, the exact folder it built for the run and the properties it
injected. In that context only, a ``FILES()`` relation reading a file name or
one ``*`` pattern directly inside that folder, with exactly those properties,
is accepted; every other ``FILES()`` read, and every statement outside the
context, is still refused. The import still runs as the caller, so Ranger
governs the target write.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import ParsedStatement

FILES_READ_REFUSAL = (
    "Reading with FILES() is not available: read files through a stage, for example "
    "SELECT * FROM @stage/folder/file.csv, or load them with COPY INTO or a pipe"
)


@dataclass(frozen=True, slots=True)
class _FileReadGrant:
    folder: str
    properties: tuple[tuple[str, str], ...]


#: The one folder, and the exact properties, Nova-generated SQL may read with
#: ``FILES()`` in this context. Only server code sets it, through
#: :func:`internal_file_read`; nothing in a request, SQL text, session
#: variable or proxy connection reaches it.
_INTERNAL_READ: ContextVar[_FileReadGrant | None] = ContextVar(
    "nova_internal_file_read", default=None
)

#: A file name, or one ``*`` pattern, directly inside the granted folder.
_FILE_NAME = re.compile(r"[A-Za-z0-9_.-]*\*?[A-Za-z0-9_.-]*")
#: ``scheme://bucket/...``: one scheme, a bucket, and no empty, ``.`` or ``..``
#: segment, so comparing the text compares the normalized location.
_FOLDER = re.compile(r"[a-z][a-z0-9+.-]*://[A-Za-z0-9_.-]+(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*/")


def _folder(text: str) -> str:
    text = text if text.endswith("/") else text + "/"
    if not _FOLDER.fullmatch(text) or "/./" in text or "/../" in text:
        raise ValueError(f"Not a storage folder: {text!r}")
    return text


@contextmanager
def internal_file_read(
    folder: str, *, root: str, properties: Mapping[str, str]
) -> Iterator[None]:
    """Accept one Nova-generated ``FILES()`` read in this context.

    ``root`` is the configured transfer location (scheme and bucket included)
    and ``folder`` the per-run folder under it that Nova built, both from
    server configuration, never from user input. Inside the context a
    ``FILES()`` relation is accepted only when its ``path`` is a file name or
    one ``*`` pattern directly inside ``folder`` and its other properties are
    exactly ``properties`` (the format and the storage settings Nova
    injected). The grant is removed when the context exits, and as a context
    variable it never reaches another request.
    """
    folder, root = _folder(folder), _folder(root)
    if not folder.startswith(root) or folder == root:
        raise ValueError("The folder is not inside the transfer location")
    expected = tuple(sorted((key.strip().lower(), value) for key, value in properties.items()))
    if any(key == "path" for key, _ in expected) or len({k for k, _ in expected}) != len(expected):
        raise ValueError("Grant properties must be unique and must not include path")
    token = _INTERNAL_READ.set(_FileReadGrant(folder, expected))
    try:
        yield
    finally:
        _INTERNAL_READ.reset(token)


def _string_value(text: str) -> str | None:
    if len(text) < 2 or text[0] not in "'\"" or text[-1] != text[0] or "\\" in text:
        return None
    return text[1:-1].replace(text[0] * 2, text[0])


def _files_properties(parsed: ParsedStatement, node: Any) -> list[tuple[str | None, str | None]]:
    sql = parsed.normalized_sql
    pairs: list[tuple[str | None, str | None]] = []
    for item in node.propertyList().property_():
        key = _string_value(sql[item.key.start.start : item.key.stop.stop + 1])
        value = _string_value(sql[item.value.start.start : item.value.stop.stop + 1])
        pairs.append((key.strip().lower() if key is not None else None, value))
    return pairs


def _granted(parsed: ParsedStatement, node: Any) -> bool:
    grant = _INTERNAL_READ.get()
    if grant is None:
        return False
    try:
        pairs = _files_properties(parsed, node)
    except Exception:  # noqa: BLE001 - an unreadable property list is not granted
        return False
    if any(key is None or value is None for key, value in pairs):
        return False
    paths = [value for key, value in pairs if key == "path"]
    others = sorted((key, value) for key, value in pairs if key != "path")
    if len(paths) != 1 or tuple(others) != grant.properties:
        return False
    path = paths[0] or ""
    name = path[len(grant.folder) :]
    return path.startswith(grant.folder) and bool(name) and bool(_FILE_NAME.fullmatch(name)) \
        and name not in {".", ".."}


def _native_file_reads(parsed: ParsedStatement) -> Iterator[Any]:
    for node in walk_nodes(parsed.statement_context):
        kind = type(node).__name__
        if kind == "FileTableFunctionContext" or (
            kind == "DescTableStatementContext" and node.FILES() is not None
        ):
            yield node


def has_native_file_read(parsed: ParsedStatement) -> bool:
    """True when the statement reads files through ``FILES(...)``.

    Two grammar forms reach the files: the ``FILES(...)`` relation and
    ``DESC FILES(...)``, which reads them to infer a schema.
    """
    return next(_native_file_reads(parsed), None) is not None


def refuse_native_file_reads(parsed: ParsedStatement) -> None:
    """Raise :class:`SemanticError` when the statement reads with ``FILES(...)``.

    Inside :func:`internal_file_read`, a ``FILES()`` relation reading only the
    granted folder is accepted; ``DESC FILES(...)`` never is.
    """
    for node in _native_file_reads(parsed):
        if type(node).__name__ == "FileTableFunctionContext" and _granted(parsed, node):
            continue
        raise SemanticError(FILES_READ_REFUSAL)


__all__ = [
    "FILES_READ_REFUSAL",
    "has_native_file_read",
    "internal_file_read",
    "refuse_native_file_reads",
]
