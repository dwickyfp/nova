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
"""

from __future__ import annotations

from app.sql_frontend.antlr_utils import walk_nodes
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import ParsedStatement

FILES_READ_REFUSAL = (
    "Reading with FILES() is not available: read files through a stage, for example "
    "SELECT * FROM @stage/folder/file.csv, or load them with COPY INTO or a pipe"
)


def has_native_file_read(parsed: ParsedStatement) -> bool:
    """True when the statement reads files through ``FILES(...)``.

    Two grammar forms reach the files: the ``FILES(...)`` relation and
    ``DESC FILES(...)``, which reads them to infer a schema.
    """
    for node in walk_nodes(parsed.statement_context):
        kind = type(node).__name__
        if kind == "FileTableFunctionContext":
            return True
        if kind == "DescTableStatementContext" and node.FILES() is not None:
            return True
    return False


def refuse_native_file_reads(parsed: ParsedStatement) -> None:
    """Raise :class:`SemanticError` when the statement reads with ``FILES(...)``."""
    if has_native_file_read(parsed):
        raise SemanticError(FILES_READ_REFUSAL)


__all__ = ["FILES_READ_REFUSAL", "has_native_file_read", "refuse_native_file_reads"]
