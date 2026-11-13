"""novaduck: this notebook's DuckDB, connected to Nova.

``con = novaduck.connect()`` is the only Nova call. It returns the kernel's
DuckDB 2.0 connection (the same one SQL cells and ``duckdb.sql`` use) with
every catalog your role sees attached under its Nova name: ``nova`` for Nova's
own tables, then ``pg_shop``, ``sf_sales`` and so on. From there everything is
ordinary DuckDB: ``con.sql("SELECT * FROM nova.sales.orders")``,
``INSERT``/``UPDATE``/``DELETE`` on ``nova.*`` tables, and DuckDB's
``mysql_query('nova', ...)`` / ``mysql_execute('nova', ...)`` for Nova SQL.
Reads and writes run as you, under your active role: Ranger decides what you
may see and change, and every write is audited.

For notebooks written before this, the connection still accepts the older
calls ``write``, ``merge_into``, ``put``, ``nova`` and ``catalogs``. They are
the only route for writes to Postgres or Snowflake (which refuse SQL writes
through this connection) and for saving files into a Nova stage.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import time
import uuid
from typing import Any

import duckdb

RPC_DIR = "/tmp/nova/rpc"
_FORMATS = {".parquet": "parquet", ".csv": "csv", ".json": "json"}


class NovaError(Exception):
    """Nova refused or could not complete a request from this notebook."""


class NovaConnection:
    """A DuckDB connection with Nova's catalogs and write calls.

    Attribute access falls through to the DuckDB connection, so ``con.sql``,
    ``con.execute``, ``con.read_parquet`` and the rest behave as in DuckDB.
    """

    def __init__(self, con: duckdb.DuckDBPyConnection, state: Any) -> None:
        object.__setattr__(self, "_con", con)
        object.__setattr__(self, "_state", state)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._con, name)

    def __repr__(self) -> str:
        names = [item["alias"] for item in self._state.catalogs if item.get("attached")]
        return f"<novaduck connection: {', '.join(names) or 'no Nova catalogs'}>"

    @property
    def duckdb(self) -> duckdb.DuckDBPyConnection:
        """The underlying DuckDB connection."""
        return self._con

    # ── Reading ──────────────────────────────────────────────────────

    def catalogs(self) -> list[dict[str, Any]]:
        """The Nova catalogs attached to this connection, and any that failed."""
        return [dict(item) for item in self._state.catalogs]

    def nova(self, sql: str) -> duckdb.DuckDBPyRelation:
        """Run Nova SQL in Nova (full pushdown) and return the result as a relation."""
        if not self._state.nova_attached:
            raise NovaError(self._state.nova_error or "Nova data is not available here")
        return self._con.sql("SELECT * FROM mysql_query('nova', ?)", params=[sql])

    # ── Writing ──────────────────────────────────────────────────────

    def write(self, data: Any, target: str, *, mode: str = "append") -> dict[str, Any]:
        """Append ``data`` to a table, or replace its rows (``mode="overwrite"``)."""
        if mode not in {"append", "overwrite"}:
            raise ValueError('mode must be "append" or "overwrite"')
        return self._send("write", data, ".parquet", target=self._table(target), mode=mode)

    def merge_into(
        self,
        target: str,
        data: Any,
        *,
        on: list[str] | tuple[str, ...] | str,
        when_matched: str = "update",
        when_not_matched: str = "insert",
    ) -> dict[str, Any]:
        """Upsert ``data`` into a table by the ``on`` key columns.

        ``when_matched``: ``update`` (default), ``delete`` or ``ignore``.
        ``when_not_matched``: ``insert`` (default) or ``ignore``.
        """
        keys = [on] if isinstance(on, str) else list(on)
        if not keys:
            raise ValueError("on= names the key columns")
        return self._send(
            "merge", data, ".parquet", target=self._table(target), on=keys,
            when_matched=when_matched, when_not_matched=when_not_matched,
        )

    def put(self, data: Any, path: str) -> dict[str, Any]:
        """Save data, or a local file, into a Nova stage: ``@stage/dir/name.parquet``."""
        if not isinstance(path, str) or not path.startswith("@") or "/" not in path:
            raise ValueError('Name the file as "@stage/path/name.parquet"')
        extension = os.path.splitext(path)[1].lower()
        if isinstance(data, (str, os.PathLike)) and os.path.isfile(data):
            return self._send("put", data, None, target=path)
        if extension not in _FORMATS:
            raise ValueError("Data is saved as .parquet, .csv or .json; name the file so")
        return self._send("put", data, extension, target=path)

    # ── Internals ────────────────────────────────────────────────────

    def _table(self, target: str) -> dict[str, str]:
        parts = [part.strip().strip('"`') for part in str(target).split(".")]
        if len(parts) == 2:
            parts = ["nova", *parts]
        if len(parts) != 3 or not all(parts):
            raise ValueError('Name the table as "catalog.schema.table", e.g. "nova.sales.orders"')
        alias, schema, table = parts
        catalog = next(
            (item["name"] for item in self._state.catalogs
             if alias in {item["alias"], item["name"]}),
            None,
        )
        if catalog is None:
            raise NovaError(f"{alias} is not a catalog your role sees here")
        return {"catalog": catalog, "schema": schema, "table": table}

    def _send(self, op: str, data: Any, extension: str | None, **fields: Any) -> dict[str, Any]:
        os.makedirs(RPC_DIR, exist_ok=True)
        _remove_stale_results()
        request_id = uuid.uuid4().hex
        if extension is None:
            data_name = f"{request_id}.bin"
            shutil.copyfile(os.fspath(data), os.path.join(RPC_DIR, data_name))
        else:
            data_name = f"{request_id}{extension}"
            _export(self._con, data, os.path.join(RPC_DIR, data_name), _FORMATS[extension])
        paths = [os.path.join(RPC_DIR, name) for name in (data_name, f"{request_id}.json")]
        result_path = os.path.join(RPC_DIR, f"{request_id}.result.json")
        try:
            with open(paths[1], "w", encoding="utf-8") as handle:
                json.dump({"op": op, "data": data_name, **fields}, handle)
            sys.stderr.write(f"\x1enova-rpc {request_id}\x1e\n")
            sys.stderr.flush()
            result = _wait(result_path, self._state.request_timeout)
        finally:
            # The result file stays for now: the upload that wrote it may still
            # be setting its permissions when its content is already readable,
            # and removing it then fails that upload. A later request removes it.
            for path in paths:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(path)
        if not result.get("ok"):
            raise NovaError(str(result.get("error") or "Nova could not complete this request"))
        result.pop("ok", None)
        return result


def _relation(con: duckdb.DuckDBPyConnection, data: Any) -> duckdb.DuckDBPyRelation:
    if isinstance(data, duckdb.DuckDBPyRelation):
        return data
    if isinstance(data, NovaConnection):
        raise TypeError("Pass the data to write, not the connection")
    if isinstance(data, (str, os.PathLike)):
        text = os.fspath(data)
        if os.path.isfile(text):
            extension = os.path.splitext(text)[1].lower()
            reader = {".parquet": con.read_parquet, ".csv": con.read_csv,
                      ".json": con.read_json}.get(extension)
            if reader is None:
                raise ValueError("Read .parquet, .csv or .json files, or pass a DataFrame")
            return reader(text)
        return con.sql(text)
    try:
        return con.from_arrow(data) if hasattr(data, "to_batches") else con.from_df(data)
    except Exception:  # noqa: BLE001 - polars and others go through Arrow
        to_arrow = getattr(data, "to_arrow", None)
        if to_arrow is None:
            raise TypeError(
                "Write a DuckDB relation, SQL text, a pandas/polars/Arrow frame or a file path"
            ) from None
        return con.from_arrow(to_arrow())


def _export(con: duckdb.DuckDBPyConnection, data: Any, path: str, fmt: str) -> None:
    relation = _relation(con, data)
    view = f"__nova_export_{uuid.uuid4().hex[:12]}"
    relation.create_view(view, replace=True)
    try:
        options = {"parquet": "FORMAT parquet", "csv": "FORMAT csv, HEADER true",
                   "json": "FORMAT json"}[fmt]
        literal = "'" + path.replace("'", "''") + "'"
        con.execute(f'COPY (SELECT * FROM "{view}") TO {literal} ({options})').fetchall()
    finally:
        con.execute(f'DROP VIEW IF EXISTS "{view}"').fetchall()


#: A result file older than this belongs to a request that has finished.
_RESULT_STALE_SECONDS = 30.0


def _remove_stale_results() -> None:
    now = time.time()
    with contextlib.suppress(FileNotFoundError):
        for name in os.listdir(RPC_DIR):
            if not name.endswith(".result.json"):
                continue
            path = os.path.join(RPC_DIR, name)
            with contextlib.suppress(OSError):
                if now - os.path.getmtime(path) > _RESULT_STALE_SECONDS:
                    os.remove(path)


def _wait(path: str, timeout: float) -> dict[str, Any]:
    """The result Nova writes for a request, once it is complete."""
    deadline = time.monotonic() + timeout
    delay = 0.02
    while time.monotonic() < deadline:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    return json.load(handle)
            except ValueError:
                pass  # still being written
        time.sleep(delay)
        delay = min(delay * 1.5, 0.25)
    raise NovaError("Nova did not answer in time; the write may still finish")


_CONNECTION: NovaConnection | None = None


def connect() -> NovaConnection:
    """This notebook's DuckDB connection, with Nova's catalogs attached."""
    from .kernel import _STATE

    global _CONNECTION
    if _STATE.con is None:
        raise NovaError("The runtime is not ready yet")
    if _CONNECTION is None or _CONNECTION._con is not _STATE.con:
        _CONNECTION = NovaConnection(_STATE.con, _STATE)
    return _CONNECTION
