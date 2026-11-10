"""The trusted side of a notebook kernel.

Nova's runtime worker calls these functions in the same Python kernel that
runs the user's cells. They are conveniences, not a security boundary: user
code can read and change anything in its own kernel. What protects data is
outside the sandbox: the network policy lets the kernel reach only Nova's
MySQL proxy, and the proxy runs every statement as the signed-in user under
their active role. Writes that need Nova itself (stage files, Postgres or
Snowflake tables) are requests the worker carries out as that user
(:mod:`nova_runtime.novaduck`).

Each call returns a :class:`Payload`, whose display bundle carries JSON under
``application/vnd.nova.payload+json``. execd forwards ``execute_result``
bundles intact (it drops ``display_data``), so this is the one reliable way
back to the worker; displays made while a cell runs are captured here and
returned by :func:`end`.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from typing import Any

import duckdb

from . import refs, tables

PAYLOAD_MIME = "application/vnd.nova.payload+json"

#: What a captured display may carry; other MIME types are dropped.
_KEPT_MIMES = (
    tables.TABLE_MIME,
    "image/png",
    "image/jpeg",
    "image/svg+xml",
    "text/html",
    "text/markdown",
    "text/plain",
)
_MAX_DISPLAY_BYTES = 10 * 1024 * 1024
#: Displays larger than this are handed over as files (see :func:`end`).
_INLINE_DISPLAY_BYTES = 1024 * 1024
DISPLAY_DIR = "/tmp/nova/displays"
_MAX_DISPLAYS = 50
_HIDDEN_NAMES = frozenset(
    {"In", "Out", "exit", "quit", "get_ipython", "db", "duckdb", "open", "display"}
)


class Payload:
    """A value whose only representation is a Nova JSON payload."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def _repr_mimebundle_(self, include=None, exclude=None):  # noqa: ARG002
        return {PAYLOAD_MIME: json.dumps(self.data, default=str), "text/plain": "<nova>"}


class _State:
    def __init__(self) -> None:
        self.con: duckdb.DuckDBPyConnection | None = None
        self.preview_rows = 100
        self.capturing = False
        self.displays: list[dict[str, Any]] = []
        self.dropped_displays = 0
        self.execution_id: str | None = None
        self.started = 0.0
        self.nova_attached = False
        self.nova_error: str | None = None
        #: Nova catalogs attached to the connection: name, alias, type, attached, error.
        self.catalogs: list[dict[str, Any]] = []
        #: How long a write request waits for Nova (the cell timeout).
        self.request_timeout = 300.0
        self.original_publish = None


_STATE = _State()


def _ipython():
    from IPython import get_ipython

    shell = get_ipython()
    if shell is None:  # pragma: no cover - always inside a kernel
        raise RuntimeError("nova_runtime runs inside an IPython kernel")
    return shell


def _exec(con: duckdb.DuckDBPyConnection, sql: str, params=None) -> list:
    """Run a statement and read its whole result.

    DuckDB 2.0 keeps a connection busy until the last result is fully read, so
    every trusted statement consumes its result before the user's next query.
    """
    return con.execute(sql, params).fetchall() if params else con.execute(sql).fetchall()


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _read_credential(path: str | None) -> dict[str, Any] | None:
    """Read and delete a one-time credential file written by the worker."""
    if not path:
        return None
    if not re.fullmatch(r"/tmp/nova/\.cred-[A-Za-z0-9_-]{8,64}\.json", path):
        raise ValueError("Unexpected credential location")
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)


def _alias(name: str) -> str:
    return "nova" if name == "default_catalog" else name


def _attach_nova(con: duckdb.DuckDBPyConnection, credential: dict[str, Any]) -> None:
    """(Re)attach every catalog the role sees through the governed proxy.

    Each catalog is its own attach (``nova`` for Nova's tables, then each
    external catalog under its Nova name), selected at login with the
    handshake database ``<catalog>.``. A catalog that fails to attach is
    reported and skipped; ``nova`` failing means Nova data is unavailable.
    """
    catalogs = credential.get("catalogs") or [{"name": "default_catalog", "type": "Internal"}]
    attached = {row[0] for row in _exec(con, "SELECT database_name FROM duckdb_databases()")}
    for previous in _STATE.catalogs:
        if previous["alias"] in attached:
            _exec(con, f'DETACH "{previous["alias"]}"')
    states: list[dict[str, Any]] = []
    for index, catalog in enumerate(catalogs):
        name = str(catalog.get("name") or "")
        alias = _alias(name)
        if not name or not re.fullmatch(r"[A-Za-z_][\w$-]{0,127}", alias):
            continue
        item = {"name": name, "alias": alias, "type": str(catalog.get("type") or ""),
                "attached": False, "error": catalog.get("error")}
        if item["error"] and name != "default_catalog":
            states.append(item)
            continue
        database = "" if name == "default_catalog" else f"{name}."
        try:
            _exec(
                con,
                f"CREATE OR REPLACE TEMPORARY SECRET __nova_secret_{index} (TYPE mysql, "
                f"HOST {_sql_literal(credential['host'])}, PORT {int(credential['port'])}, "
                f"USER {_sql_literal(credential['user'])}, "
                f"PASSWORD {_sql_literal(credential['token'])}, "
                f"DATABASE {_sql_literal(database)}, SSL_MODE 'disabled')",
            )
            _exec(con, f'ATTACH \'\' AS "{alias}" (TYPE mysql, SECRET __nova_secret_{index})')
            item["attached"] = True
        except Exception as exc:  # noqa: BLE001 - one catalog failing leaves the rest
            item["error"] = _clean_error(exc)
            if name == "default_catalog":
                states.append(item)
                _STATE.catalogs = states
                raise
        states.append(item)
    _STATE.catalogs = states
    _exec(con, "CALL mysql_clear_cache()")
    _STATE.nova_attached = True
    _STATE.nova_error = None


def _publish(data, metadata=None, source=None, *, transient=None, update=False, **kwargs):  # noqa: ARG001
    """Replacement display publisher: keep what Nova renders, in order."""
    if not _STATE.capturing:
        if _STATE.original_publish is not None:
            return _STATE.original_publish(
                data, metadata=metadata, transient=transient, update=update, **kwargs
            )
        return None
    if len(_STATE.displays) >= _MAX_DISPLAYS:
        _STATE.dropped_displays += 1
        return None
    kept = {mime: value for mime, value in (data or {}).items() if mime in _KEPT_MIMES}
    size = sum(len(str(value)) for value in kept.values())
    if size > _MAX_DISPLAY_BYTES:
        kept = {"text/plain": f"Output too large to show ({size:,} bytes)"}
    if kept:
        _STATE.displays.append({"t": time.time(), "data": kept})
    return None


def _table_formatter(value: Any):
    payload = tables.preview(value, limit=_STATE.preview_rows, origin=_origin_of(value))
    if payload is None:
        return None
    return {tables.TABLE_MIME: json.dumps(payload, default=str)}


def _origin_of(value: Any) -> str:
    return "local_duckdb" if tables.kind_of(value) == "duckdb_relation" else "python_memory"


def _install_display_hooks() -> None:
    shell = _ipython()
    if _STATE.original_publish is None:
        _STATE.original_publish = shell.display_pub.publish
        shell.display_pub.publish = _publish
    formatter = shell.display_formatter.mimebundle_formatter
    for module, name in (
        ("duckdb", "DuckDBPyRelation"),
        ("pandas", "DataFrame"),
        ("pandas", "Series"),
        ("polars", "DataFrame"),
        ("polars", "LazyFrame"),
        ("pyarrow", "Table"),
    ):
        formatter.for_type_by_name(module, name, _table_formatter)


# ── Rich output for libraries made for JupyterLab ─────────────────────────
#
# PyGWalker shows a Jupyter widget and plotly a MIME type only JupyterLab
# renders. When either is imported, it is set to produce self-contained HTML
# instead, which the notebook shows in a sandboxed frame with no network.


def _patch_pygwalker(module: Any) -> None:
    with contextlib.suppress(Exception):
        _patch_pygwalker_parser()
    with contextlib.suppress(Exception):
        # Usage events could not leave the sandbox anyway; do not try.
        from pygwalker.services.global_var import GlobalVarManager

        GlobalVarManager.set_privacy("offline")
    to_html = getattr(module, "to_html", None)
    if to_html is None or getattr(module.walk, "__nova__", False):
        return
    keep = {"spec", "theme_key", "appearance", "default_tab", "field_specs"}

    def walk(dataset: Any, *args: Any, **kwargs: Any) -> None:
        from IPython.display import HTML, display

        options = {k: v for k, v in kwargs.items() if k in keep}
        display(HTML(_pygwalker_page(dataset, options) or to_html(dataset, **options)))

    walk.__nova__ = True  # type: ignore[attr-defined]
    walk.__doc__ = "Explore a DataFrame interactively (shown in the notebook)."
    module.walk = walk


def _pygwalker_page(dataset: Any, options: dict[str, Any]) -> str | None:
    """PyGWalker's explorer as one page, without the inner frame ``to_html`` adds.

    The notebook already shows it in a sandboxed frame; a second frame inside
    would not get the storage the page needs (see the output frame's shim).
    Built as ``pygwalker.to_html`` builds it: data in the page, computed in the
    browser, no cloud or save tools.
    """
    try:
        from pygwalker.api.pygwalker import PygWalker
        from pygwalker.utils.randoms import generate_hash_code

        walker = PygWalker(
            gid=generate_hash_code(),
            dataset=dataset,
            field_specs=options.get("field_specs") or [],
            spec=options.get("spec", ""),
            source_invoke_code="",
            theme_key=options.get("theme_key", "g2"),
            appearance=options.get("appearance", "media"),
            show_cloud_tool=False,
            use_preview=False,
            kernel_computation=False,
            use_save_tool=False,
            gw_mode="explore",
            is_export_dataframe=False,
            kanaries_api_key="",
            default_tab=options.get("default_tab", "vis"),
            cloud_computation=False,
        )
        return str(walker.to_html_without_iframe())
    except Exception:  # noqa: BLE001 - fall back to the library's own HTML
        return None


def _patch_pygwalker_parser() -> None:
    """PyGWalker reads one row with ``fetchone()``, which DuckDB 2.0 relations lack."""
    import functools

    from pygwalker.data_parsers import base

    parser = base.BaseDataFrameDataParser

    def field_metas(self: Any) -> list[dict[str, str]]:
        duckdb.register("pygwalker_mid_table", self._duckdb_df)
        result = duckdb.sql("SELECT * FROM pygwalker_mid_table LIMIT 1")
        rows = result.fetchall()
        if not rows:
            return []
        return base.get_data_meta_type(dict(zip(result.columns, rows[0], strict=False)))

    parser.field_metas = property(functools.lru_cache(maxsize=None)(field_metas))


def _patch_plotly_io(module: Any) -> None:
    from plotly.io._base_renderers import MimetypeRenderer

    class NovaRenderer(MimetypeRenderer):
        def to_mimebundle(self, fig_dict):  # noqa: ANN001, ANN201
            return {
                "text/html": module.to_html(
                    fig_dict, include_plotlyjs=True, full_html=False, validate=False
                )
            }

    module.renderers["nova"] = NovaRenderer()
    module.renderers.default = "nova"


_PATCHES = {"pygwalker": _patch_pygwalker, "plotly.io": _patch_plotly_io}


class _RenderHook:
    """Patches a library right after it is first imported (a meta path finder)."""

    def find_spec(self, name: str, path: Any, target: Any = None) -> Any:
        patch = _PATCHES.get(name)
        if patch is None:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(name, path, target)
            if spec is not None:
                break
        else:
            return None
        loader = spec.loader
        original = getattr(loader, "exec_module", None)
        if original is None:
            return spec

        def exec_module(module: Any) -> None:
            original(module)
            with contextlib.suppress(Exception):
                patch(module)

        loader.exec_module = exec_module
        return spec


def _install_render_hook() -> None:
    if not any(isinstance(finder, _RenderHook) for finder in sys.meta_path):
        sys.meta_path.insert(0, _RenderHook())
    for name, patch in _PATCHES.items():
        if name in sys.modules:
            with contextlib.suppress(Exception):
                patch(sys.modules[name])


def bootstrap(config_json: str) -> Payload:
    """Prepare the kernel: DuckDB as ``db`` and the default, ``nova`` attached.

    ``config_json`` holds limits and, when Nova data is available, the path of
    a one-time credential file the worker wrote for this call.
    """
    config = json.loads(config_json)
    _STATE.preview_rows = int(config.get("preview_rows", 100))
    _STATE.request_timeout = float(config.get("request_timeout", 300))
    os.makedirs("/tmp/nova/duckdb", exist_ok=True)
    workspace = config.get("workspace", "/workspace")
    os.makedirs(workspace, exist_ok=True)
    os.chdir(workspace)
    for path in (os.path.join(workspace, "src"), workspace):
        if path not in sys.path:
            sys.path.insert(0, path)

    con = duckdb.connect(
        ":memory:",
        config={
            "extension_directory": os.environ.get(
                "NOVA_DUCKDB_EXTENSION_DIR", "/opt/nova/duckdb-extensions"
            ),
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
            "allow_community_extensions": False,
            "memory_limit": str(config.get("memory_limit", "512MB")),
            "threads": int(config.get("threads", 2)),
            "temp_directory": "/tmp/nova/duckdb",
        },
    )
    for extension in ("parquet", "json", "mysql"):
        con.load_extension(extension)
    duckdb.set_default_connection(con)
    _STATE.con = con

    # Load the dataframe stack now, before the runtime reports Ready, so the
    # first cell that shows a table or a chart does not pay for it.
    import matplotlib  # noqa: F401
    import numpy  # noqa: F401
    import pandas  # noqa: F401
    import pyarrow  # noqa: F401

    from . import novaduck

    shell = _ipython()
    shell.user_ns["db"] = novaduck.connect()
    shell.user_ns["duckdb"] = duckdb
    _install_display_hooks()
    _install_render_hook()

    credential = _read_credential(config.get("credential_path"))
    nova: dict[str, Any] = {"attached": False}
    if credential:
        try:
            _attach_nova(con, credential)
            # Ask Nova alone: listing schemas here would read every catalog.
            _exec(con, "SELECT * FROM mysql_query('nova', 'SELECT 1')")
            nova = {"attached": True}
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            _STATE.nova_attached = False
            _STATE.nova_error = _clean_error(exc)
            nova = {"attached": False, "error": _STATE.nova_error}
    # Settings stay as configured: extensions cannot be switched back on.
    _exec(con, "SET lock_configuration = true")
    ready = _exec(con, "SELECT 1")[0][0] == 1
    return Payload(
        {
            "ready": bool(ready),
            "python": sys.version.split()[0],
            "duckdb": duckdb.__version__,
            "nova": {**nova, "catalogs": _STATE.catalogs},
        }
    )


def rotate(config_json: str) -> Payload:
    """Re-attach ``nova`` with a fresh credential (renewal or role change)."""
    config = json.loads(config_json)
    credential = _read_credential(config.get("credential_path"))
    if _STATE.con is None or credential is None:
        return Payload({"rotated": False})
    try:
        _attach_nova(_STATE.con, credential)
        return Payload({"rotated": True, "catalogs": _STATE.catalogs})
    except Exception as exc:  # noqa: BLE001
        _STATE.nova_attached = False
        _STATE.nova_error = _clean_error(exc)
        return Payload({"rotated": False, "error": _STATE.nova_error})


def begin(meta_json: str) -> Payload:
    """Start capturing displays for one execution."""
    meta = json.loads(meta_json)
    _STATE.execution_id = str(meta.get("execution_id") or "")
    _STATE.displays = []
    _STATE.dropped_displays = 0
    _STATE.started = time.time()
    _STATE.capturing = True
    # Files of the previous execution's large displays were read by now.
    with contextlib.suppress(FileNotFoundError):
        for name in os.listdir(DISPLAY_DIR):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(DISPLAY_DIR, name))
    return Payload({"started": True})


def end() -> Payload:
    """Stop capturing; return the displays and the variables now defined."""
    # Output the cell printed last may still sit in the stream buffers.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()
    _STATE.capturing = False
    displays, _STATE.displays = _STATE.displays, []
    # A display past ~1 MB travels as a file: execd drops a result event of
    # several MB, and interactive charts (plotly) are that large.
    os.makedirs(DISPLAY_DIR, exist_ok=True)
    for index, display in enumerate(displays):
        body = json.dumps(display["data"], default=str)
        if len(body) > _INLINE_DISPLAY_BYTES:
            path = f"{DISPLAY_DIR}/{uuid.uuid4().hex}.json"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            displays[index] = {"t": display["t"], "file": path, "bytes": len(body)}
    return Payload(
        {
            "execution_id": _STATE.execution_id,
            "displays": displays,
            "dropped_displays": _STATE.dropped_displays,
            "variables": variables(),
        }
    )


def variables() -> list[dict[str, Any]]:
    """The user's variables, described without running or copying anything."""
    shell = _ipython()
    hidden = set(getattr(shell, "user_ns_hidden", {}) or {})
    out = []
    for name, value in list(shell.user_ns.items()):
        if name.startswith("_") or name in _HIDDEN_NAMES or name in hidden:
            continue
        if type(value).__name__ in {"module", "function", "type", "builtin_function_or_method"}:
            continue
        try:
            summary = tables.describe(value)
        except Exception:  # noqa: BLE001
            summary = {"type": type(value).__name__, "kind": None, "shape": None, "preview": None}
        out.append({"name": name, **summary})
        if len(out) >= 200:
            break
    return sorted(out, key=lambda item: item["name"])


def _clean_error(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    # Never echo a credential: the secret statement is the only one carrying it.
    text = re.sub(r"PASSWORD\s+'(?:[^']|'')*'", "PASSWORD '***'", text, flags=re.I)
    return text[:2000]


def _statement_types(sql: str) -> list[str]:
    return [str(statement.type).split(".")[-1] for statement in duckdb.extract_statements(sql)]


def run_sql(payload_json: str) -> Payload:
    """Run one SQL cell in this kernel's DuckDB connection.

    ``engine`` ``duckdb`` runs the cell in DuckDB (``nova.*`` tables are read
    through the governed proxy with filter pushdown; ``{{name}}`` references
    resolve to kernel values). ``engine`` ``nova`` sends the whole statement to
    Nova (full pushdown, Nova SQL such as ``@stage``), read-only, as the user.

    A read names its result: the cell name becomes a lazy relation in Python
    and a view for later SQL. Writes run once and report affected rows.
    """
    payload = json.loads(payload_json)
    con = _STATE.con
    if con is None:
        return Payload(
            {"error": {"name": "RuntimeNotReady", "message": "The runtime is not ready"}}
        )
    source = str(payload.get("source") or "").strip()
    name = payload.get("name") or None
    engine = payload.get("engine") or "duckdb"
    limit = int(payload.get("preview_rows") or _STATE.preview_rows)
    if not source:
        return Payload({"kind": "empty"})
    shell = _ipython()
    try:
        if engine == "nova":
            if not _STATE.nova_attached:
                raise RuntimeError(
                    _STATE.nova_error or "Nova data is not available in this runtime"
                )
            text = source.rstrip().rstrip(";")
            if not (_READ_START.match(text) or _METADATA_START.match(text)):
                # mysql_query returns rows; a change has to go another way.
                raise ValueError(
                    "A Nova SQL cell runs a query (SELECT, WITH, SHOW or DESCRIBE). To "
                    "change data, write SQL on nova.<schema>.<table> in a SQL cell, or "
                    "use con.write(...) / con.merge_into(...)."
                )
            relation = con.sql("SELECT * FROM mysql_query('nova', ?)", params=[text])
            head = con.sql(
                "SELECT * FROM mysql_query('nova', ?)", params=[_nova_preview(text, limit)]
            )
            result = _named_result(
                con, shell, relation, head, name=name, limit=limit, origin="nova"
            )
            return Payload(result)

        resolved = refs.resolve(source, shell.user_ns)
        for registered, value in resolved.relations.items():
            if tables.kind_of(value) == "duckdb_relation":
                value.create_view(registered, replace=True)
            else:
                con.register(registered, value)
        types = _statement_types(resolved.sql)
        if not types:
            return Payload({"kind": "empty"})
        statements = duckdb.extract_statements(resolved.sql)
        params = list(resolved.params)
        if params and len(statements) > 1:
            raise refs.ReferenceError_(
                "A cell that references a single value holds one statement"
            )
        # Run every statement but the last once, in order.
        for statement in statements[:-1]:
            _exec(con, statement.query)
        last = statements[-1]
        if types[-1] == "SELECT":
            relation = con.sql(last.query, params=params or None)
            head = relation.limit(limit + 1)
            result = _named_result(con, shell, relation, head, name=name, limit=limit,
                                   origin="local_duckdb")
            if len(statements) == 1 and not resolved.relations and not params:
                candidate = nova_sql_candidate(last.query)
                if candidate:
                    result["nova_candidate"] = candidate
            return Payload(result)
        affected = None
        try:
            fetched = _exec(con, last.query, params or None)
            if fetched and len(fetched[0]) == 1 and isinstance(fetched[0][0], int):
                affected = fetched[0][0]
        except duckdb.InvalidInputException:
            affected = None
        return Payload({"kind": "status", "statement": types[-1], "affected_rows": affected})
    except refs.ReferenceError_ as exc:
        return Payload({"error": {"name": "ReferenceError", "message": str(exc)}})
    except Exception as exc:  # noqa: BLE001 - reported as the cell's error
        return Payload({"error": {"name": type(exc).__name__, "message": _clean_error(exc)}})


_TRAILING_LIMIT = re.compile(r"\bLIMIT\s+\d+(?:\s*(?:,|OFFSET)\s*\d+)?\s*$", re.I)
_METADATA_START = re.compile(
    r"^\s*(?:(?:--[^\n]*\n|/\*.*?\*/)\s*)*(?:SHOW|DESCRIBE|DESC)\b", re.I | re.S
)
_READ_START = re.compile(
    r"^\s*(?:(?:--[^\n]*\n|/\*.*?\*/)\s*)*\(*\s*(?:SELECT|WITH)\b", re.I | re.S
)


def nova_sql_candidate(sql: str) -> str | None:
    """This query as Nova SQL, when it reads only Nova catalogs, else ``None``.

    Dataviz re-runs a result in Nova, so a SQL cell qualifies only when every
    table it reads is in an attached Nova catalog. The catalog names are
    already Nova's except ``nova`` (Nova's own tables), which becomes
    ``default_catalog``; strings and comments are left alone. The worker then
    checks the statement in Nova before anything relies on it.
    """
    aliases = {item["alias"]: item["name"] for item in _STATE.catalogs if item.get("attached")}
    try:
        names = duckdb.get_table_names(sql, qualified=True)
        tokens = duckdb.tokenize(sql)
    except Exception:  # noqa: BLE001 - local files, Python values: not a Nova query
        return None
    if not names:
        return None
    for qualified in names:
        parts = [part.strip('"') for part in qualified.split(" AS ")[0].split(".")]
        if len(parts) != 3 or parts[0] not in aliases:
            return None
    pieces: list[str] = []
    position = 0
    bounds = [start for start, _ in tokens] + [len(sql)]
    for index, (start, kind) in enumerate(tokens):
        if str(kind).endswith("identifier"):
            raw = sql[start:bounds[index + 1]]
            word = raw.strip()
            following = sql[start + len(word):bounds[index + 1] + 1].lstrip()
            name = word.strip('"')
            if name in aliases and aliases[name] != name and following.startswith("."):
                pieces.append(sql[position:start])
                pieces.append(aliases[name])
                position = start + len(word)
    pieces.append(sql[position:])
    return "".join(pieces)


def _nova_preview(text: str, limit: int) -> str:
    """The bounded query a Nova SQL cell previews with, keeping its order.

    Nova (like MySQL) ignores ORDER BY inside a derived table, so a query is
    limited in place when it has no LIMIT of its own; one that has a LIMIT, or
    that is not a plain query, is wrapped. ``SHOW`` and ``DESCRIBE`` cannot sit
    in a derived table; they run as written (their answers are small).
    """
    if _METADATA_START.match(text):
        return text
    if _READ_START.match(text) and not _TRAILING_LIMIT.search(text):
        return f"{text}\nLIMIT {limit + 1}"
    return f"SELECT * FROM ({text}) AS nova_preview LIMIT {limit + 1}"


def _named_result(con, shell, relation, head, *, name, limit, origin) -> dict[str, Any]:
    table = head.to_arrow_table()
    schema = [
        {"name": n, "type": str(t)}
        for n, t in zip(relation.columns, relation.types, strict=False)
    ]
    truncated = table.num_rows > limit
    preview_table = table.slice(0, limit)
    columns = [preview_table.column(i).to_pylist() for i in range(preview_table.num_columns)]
    from .jsonsafe import cell_value

    rows = [[cell_value(col[r]) for col in columns] for r in range(preview_table.num_rows)]
    if name:
        relation.create_view(name, replace=True)
        shell.user_ns[name] = con.view(name)
    return {
        "kind": "table",
        "name": name,
        "table": {
            "kind": "duckdb_relation",
            "schema": schema,
            "rows": rows,
            "limit": limit,
            "total_rows": None if truncated else len(rows),
            "truncated": truncated,
            "origin": origin,
        },
    }


def list_outputs(config_json: str = "{}") -> Payload:
    """Files under ``/workspace/outputs`` to copy back to the Workspace."""
    config = json.loads(config_json)
    root = config.get("root", "/workspace/outputs")
    max_files = int(config.get("max_files", 100))
    max_bytes = int(config.get("max_bytes", 50 * 1024 * 1024))
    files: list[dict[str, Any]] = []
    skipped = 0
    if os.path.isdir(root):
        for directory, _dirs, names in os.walk(root, followlinks=False):
            for name in sorted(names):
                path = os.path.join(directory, name)
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                size = os.path.getsize(path)
                if size > max_bytes or len(files) >= max_files:
                    skipped += 1
                    continue
                files.append({"path": os.path.relpath(path, root), "size": size})
    return Payload({"root": root, "files": files, "skipped": skipped})


_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}(?:\[[A-Za-z0-9,._-]{1,100}\])?$")
_SPECIFIER = re.compile(r"^(?:(?:==|>=|<=|~=|!=|<|>)\s*[0-9][0-9A-Za-z.*+!_-]*\s*,?\s*){0,4}$")


def install(spec_json: str) -> Payload:
    """Install approved packages into this kernel's own interpreter.

    The worker validates the request against the allowlist first; this checks
    the shape again and runs pip with a fixed argument vector (no shell), then
    imports each package to prove it works. Already-imported modules keep
    their old code until the kernel restarts.
    """
    spec = json.loads(spec_json)
    requirements: list[str] = []
    imports: list[str] = []
    for item in spec.get("packages", []):
        name, specifier = str(item.get("name", "")), str(item.get("specifier") or "")
        if not _PACKAGE.match(name) or not _SPECIFIER.match(specifier):
            return Payload({"ok": False, "error": f"Invalid package request: {name}"})
        requirements.append(f"{name}{specifier}")
        imports.append(str(item.get("import_name") or name.split("[")[0].replace("-", "_")))
    if not requirements:
        return Payload({"ok": False, "error": "No packages requested"})
    already = sorted(module for module in imports if module in sys.modules)
    import site

    user_site = site.getusersitepackages()
    code = 0
    for requirement in requirements:
        code = _pip_install(requirement, user_site)
        if code != 0:
            return Payload(
                {
                    "ok": False,
                    "exit_code": code,
                    "error": (
                        "pip could not install the request. A package that needs a different "
                        "version of the runtime's own libraries (DuckDB, pandas, numpy, "
                        "pyarrow and others) cannot be added."
                    ),
                }
            )
    import importlib

    if user_site not in sys.path:
        sys.path.append(user_site)
    importlib.invalidate_caches()
    failed = []
    for module in imports:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001
            failed.append({"module": module, "error": _clean_error(exc)})
    versions = _installed_versions([r.split("[")[0] for r in requirements])
    return Payload(
        {
            "ok": not failed,
            "exit_code": code,
            "failed_imports": failed,
            "versions": versions,
            "restart_recommended": already,
        }
    )


#: Pins of every library the image ships; installs may not change them.
RUNTIME_CONSTRAINTS = "/opt/nova/runtime-constraints.txt"
#: Packages whose declared pins are stricter than what they need here. They
#: are installed without those pins and patched where the runtime's newer
#: library differs (see ``_patch_pygwalker``):
#: ``skip`` requirements the runtime already provides in a newer version;
#: ``no_deps`` dependencies installed without their own (stale) pins.
#: Every other requirement still installs under the runtime pins.
_RELAXED_REQUIREMENTS: dict[str, dict[str, frozenset[str]]] = {
    "pygwalker": {
        "skip": frozenset({"duckdb", "python-dateutil"}),
        "no_deps": frozenset({"kanaries-track", "segment-analytics-python"}),
    },
}


def _pip(*args: str) -> int:
    command = [
        sys.executable, "-m", "pip", "install", "--user", "--disable-pip-version-check",
        "--no-input", "--progress-bar", "off",
    ]
    if os.path.exists(RUNTIME_CONSTRAINTS):
        command += ["--constraint", RUNTIME_CONSTRAINTS]
    process = subprocess.Popen(  # noqa: S603 - fixed argv, validated requirements
        [*command, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
    return process.wait()


def _canonical(name: str) -> str:
    return re.split(r"[\[<>=!~ ;]", name, maxsplit=1)[0].strip().lower().replace("_", "-")


def _pip_install(requirement: str, user_site: str) -> int:
    """Install one requirement under the runtime pins (see ``_RELAXED_REQUIREMENTS``)."""
    rules = _RELAXED_REQUIREMENTS.get(_canonical(requirement))
    if rules is None:
        return _pip(requirement)
    import importlib
    from importlib import metadata

    from packaging.requirements import Requirement

    if user_site not in sys.path:
        sys.path.append(user_site)
    pending, done, needed = [requirement], set(), []
    while pending:
        current = pending.pop()
        code = _pip("--no-deps", current)
        if code != 0:
            return code
        done.add(_canonical(current))
        importlib.invalidate_caches()
        for raw in metadata.requires(_canonical(current)) or []:
            dependency = Requirement(raw)
            name = _canonical(dependency.name)
            if dependency.marker is not None and not dependency.marker.evaluate({"extra": ""}):
                continue
            if name in rules["skip"] or name in done:
                continue
            if name in rules["no_deps"]:
                pending.append(str(dependency).split(";")[0].strip())
            else:
                needed.append(str(dependency).split(";")[0].strip())
    return _pip(*needed) if needed else 0


def _installed_versions(names: list[str]) -> dict[str, str]:
    from importlib import metadata

    out = {}
    for name in names:
        bare = re.split(r"[<>=!~]", name)[0]
        try:
            out[bare] = metadata.version(bare)
        except metadata.PackageNotFoundError:
            continue
    return out
