"""The trusted side of a notebook kernel.

Nova's runtime worker calls these functions in the same Python kernel that
runs the user's cells. They are conveniences, not a security boundary: user
code can read and change anything in its own kernel. What protects data is
outside the sandbox: the network policy lets the kernel reach only Nova's
MySQL proxy, and the proxy runs every statement as the signed-in user under
their active role, read-only.

Each call returns a :class:`Payload`, whose display bundle carries JSON under
``application/vnd.nova.payload+json``. execd forwards ``execute_result``
bundles intact (it drops ``display_data``), so this is the one reliable way
back to the worker; displays made while a cell runs are captured here and
returned by :func:`end`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
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
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _attach_nova(con: duckdb.DuckDBPyConnection, credential: dict[str, Any]) -> None:
    """(Re)attach the ``nova`` catalog through the governed proxy, read-only."""
    _exec(
        con,
        "CREATE OR REPLACE TEMPORARY SECRET __nova_secret (TYPE mysql, "
        f"HOST {_sql_literal(credential['host'])}, PORT {int(credential['port'])}, "
        f"USER {_sql_literal(credential['user'])}, "
        f"PASSWORD {_sql_literal(credential['token'])}, SSL_MODE 'disabled')"
    )
    attached = {row[0] for row in _exec(con, "SELECT database_name FROM duckdb_databases()")}
    if "nova" in attached:
        _exec(con, "DETACH nova")
    _exec(con, "ATTACH '' AS nova (TYPE mysql, SECRET __nova_secret, READ_ONLY)")
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


def bootstrap(config_json: str) -> Payload:
    """Prepare the kernel: DuckDB as ``db`` and the default, ``nova`` attached.

    ``config_json`` holds limits and, when Nova data is available, the path of
    a one-time credential file the worker wrote for this call.
    """
    config = json.loads(config_json)
    _STATE.preview_rows = int(config.get("preview_rows", 100))
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

    shell = _ipython()
    shell.user_ns["db"] = con
    shell.user_ns["duckdb"] = duckdb
    _install_display_hooks()

    credential = _read_credential(config.get("credential_path"))
    nova: dict[str, Any] = {"attached": False}
    if credential:
        try:
            _attach_nova(con, credential)
            schemas = _exec(
                con,
                "SELECT count(*) FROM information_schema.schemata WHERE catalog_name = 'nova'",
            )[0][0]
            nova = {"attached": True, "schemas": int(schemas)}
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
            "nova": nova,
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
        return Payload({"rotated": True})
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
    return Payload({"started": True})


def end() -> Payload:
    """Stop capturing; return the displays and the variables now defined."""
    _STATE.capturing = False
    displays, _STATE.displays = _STATE.displays, []
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
        return Payload({"error": {"name": "RuntimeNotReady", "message": "The runtime is not ready"}})
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
                raise RuntimeError(_STATE.nova_error or "Nova data is not available in this runtime")
            text = source.rstrip().rstrip(";")
            relation = con.sql("SELECT * FROM mysql_query('nova', ?)", params=[text])
            head = con.sql(
                "SELECT * FROM mysql_query('nova', ?)",
                params=[f"SELECT * FROM ({text}) AS nova_preview LIMIT {limit + 1}"],
            )
            result = _named_result(con, shell, relation, head, name=name, limit=limit, origin="nova")
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
            return Payload(
                _named_result(con, shell, relation, head, name=name, limit=limit,
                              origin="local_duckdb")
            )
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


def _named_result(con, shell, relation, head, *, name, limit, origin) -> dict[str, Any]:
    table = head.to_arrow_table()
    schema = [{"name": n, "type": str(t)} for n, t in zip(relation.columns, relation.types)]
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
    command = [
        sys.executable, "-m", "pip", "install", "--user", "--disable-pip-version-check",
        "--no-input", "--progress-bar", "off", *requirements,
    ]
    process = subprocess.Popen(  # noqa: S603 - fixed argv, validated requirements
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
    code = process.wait()
    if code != 0:
        return Payload({"ok": False, "exit_code": code, "error": "pip could not install the request"})
    import importlib
    import site

    user_site = site.getusersitepackages()
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
