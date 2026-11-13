"""Requests a notebook kernel makes to Nova while a cell runs.

``novaduck``'s ``con.write``, ``con.merge_into`` and ``con.put`` need Nova to
act: load rows into a Nova, Postgres or Snowflake table, or put a file in a
stage. The sandbox reaches only the MySQL proxy, so the kernel asks the worker
that runs the cell:

1. the kernel writes ``/tmp/nova/rpc/<id>.json`` (and the data file next to
   it) and prints one marker line ``\\x1enova-rpc <id>\\x1e`` on stderr;
2. the worker, which reads the cell's output live, strips the marker, reads
   the request, does it as the runtime's user under their role, and writes
   ``/tmp/nova/rpc/<id>.result.json`` back;
3. the kernel, waiting for that file, returns or raises.

A request names no identity: the worker always acts as the user bound to the
runtime, through the same governed paths as the rest of Nova (QueryService
for Nova tables, stage authorization for stages, Ranger-checked write-back
for external catalogs). A request the user's own code forges is the user
asking for their own rights.
"""

from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.modules.access_control import data_tokens
from app.modules.notebooks.runtime.provider import ProviderError, SandboxHandle, SandboxProvider

logger = logging.getLogger(__name__)

RPC_DIR = "/tmp/nova/rpc"
MARKER = re.compile(r"\x1enova-rpc ([0-9a-f]{32})\x1e\n?")
_CUT_MARKER = re.compile(
    r"\x1e(?:nova-rpc(?: [0-9a-f]{0,32})?|n(?:o(?:v(?:a(?:-(?:r(?:p)?)?)?)?)?)?)?$"
)
#: Rows per statement when Nova tables are written through SQL.
INSERT_BATCH = 2000
MERGE_BATCH = 500
_IDENT = re.compile(r"^[^\x00`]{1,256}$")
_STAGE_PATH = re.compile(r"^@(?P<name>[A-Za-z_][\w.$-]{0,255})/(?P<path>[^\x00]{1,1024})$")


class RpcError(Exception):
    """A request Nova refused or could not do; the message is safe to show."""


@dataclass(frozen=True)
class Caller:
    username: str
    role: str
    version: int
    encrypted_password: str
    session_id: str


class MarkerReader:
    """Pulls request markers out of a cell's stderr, across chunk boundaries."""

    def __init__(self) -> None:
        self._carry = ""

    def feed(self, text: str) -> tuple[str, list[str]]:
        text = self._carry + text
        self._carry = ""
        ids = MARKER.findall(text)
        text = MARKER.sub("", text)
        cut = _CUT_MARKER.search(text)
        if cut:
            # A marker cut by the stream: hold it until the rest arrives.
            self._carry, text = text[cut.start():], text[:cut.start()]
        return text, ids


async def handle(
    provider: SandboxProvider, sandbox: SandboxHandle, session_id: str, request_id: str
) -> None:
    """Do one kernel request and write its result back for the kernel."""
    result: dict[str, Any]
    try:
        raw = await provider.read_file(sandbox, f"{RPC_DIR}/{request_id}.json")
        request = json.loads(raw)
        caller = await _caller(session_id)
        result = {"ok": True, **await _dispatch(provider, sandbox, request, caller)}
    except RpcError as exc:
        result = {"ok": False, "error": str(exc)}
    except ProviderError as exc:
        result = {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - the kernel gets a safe message
        from app.modules.catalog.errors import CatalogError
        from app.modules.stages.access import StageAccessDenied

        if isinstance(exc, (CatalogError, StageAccessDenied)):
            result = {"ok": False, "error": str(exc)}
        else:
            logger.exception("Notebook request %s failed", request_id)
            result = {"ok": False, "error": "Nova could not complete this request"}
    body = json.dumps(result, default=str).encode()
    await provider.write_file(sandbox, f"{RPC_DIR}/{request_id}.result.json", body)


async def _caller(session_id: str) -> Caller:
    try:
        session = await data_tokens.resolve_session(session_id)
    except data_tokens.BiCredentialError:
        raise RpcError("Sign in to Nova again to write from this notebook") from None
    return Caller(
        username=session.username,
        role=session.active_role,
        version=session.security_context_version,
        encrypted_password=session.encrypted_password,
        session_id=session.session_id,
    )


async def _dispatch(
    provider: SandboxProvider, sandbox: SandboxHandle, request: dict[str, Any], caller: Caller
) -> dict[str, Any]:
    op = request.get("op")
    data_name = str(request.get("data") or "")
    if not re.fullmatch(r"[0-9a-f]{32}\.(parquet|csv|json|bin)", data_name):
        raise RpcError("The request has no data file")
    data = await provider.read_file(sandbox, f"{RPC_DIR}/{data_name}")
    if len(data) > settings.NOTEBOOK_WRITE_MAX_BYTES:
        raise RpcError(
            f"One write takes at most {settings.NOTEBOOK_WRITE_MAX_BYTES // 1024**2} MB"
        )
    if op == "put":
        return await _put(str(request.get("target") or ""), data, caller)
    if op in {"write", "merge"}:
        import pyarrow.parquet as pq

        table = pq.read_table(io.BytesIO(data))
        if table.num_rows > settings.NOTEBOOK_WRITE_MAX_ROWS:
            raise RpcError(f"One write takes at most {settings.NOTEBOOK_WRITE_MAX_ROWS:,} rows")
        target = request.get("target") or {}
        catalog, schema, name = (str(target.get(key) or "") for key in ("catalog", "schema",
                                                                         "table"))
        if not all(_IDENT.match(part) for part in (catalog, schema, name)):
            raise RpcError("Name the table as catalog.schema.table")
        mode = "merge" if op == "merge" else str(request.get("mode") or "append")
        keys = tuple(str(key) for key in request.get("on") or ())
        when_matched = str(request.get("when_matched") or "update")
        when_not_matched = str(request.get("when_not_matched") or "insert")
        columns = tuple(table.column_names)
        rows = [tuple(row.values()) for row in table.to_pylist()]
        if catalog == "default_catalog":
            return await _write_nova(
                caller, (catalog, schema, name), columns, rows, mode=mode, keys=keys,
                when_matched=when_matched, when_not_matched=when_not_matched,
            )
        from app.modules.catalog import writeback

        written = await writeback.write(
            username=caller.username,
            active_role=caller.role,
            target=f"{catalog}.{schema}.{name}",
            columns=columns,
            rows=rows,
            mode=mode,
            keys=keys,
            when_matched=when_matched,
            when_not_matched=when_not_matched,
            session_id=caller.session_id,
        )
        return {
            "inserted": written.inserted,
            "updated": written.updated,
            "deleted": written.deleted,
            "rows": len(rows),
        }
    raise RpcError("Unknown request")


# ── Nova tables ───────────────────────────────────────────────────────────


def _quote(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


async def _run(caller: Caller, sql: str) -> Any:
    from app.modules.query.service import query_service

    results = await query_service.execute_statements(
        sql=sql,
        username=caller.username,
        encrypted_password=caller.encrypted_password,
        role=caller.role,
        security_context_version=caller.version,
        session_id=caller.session_id,
        confirm_destructive=True,
        source="notebook",
    )
    last = results[-1] if results else None
    if last is None or last.error:
        raise RpcError(str(getattr(last, "error", None) or "Nova refused the statement"))
    return last


async def _write_nova(
    caller: Caller,
    target: tuple[str, str, str],
    columns: tuple[str, ...],
    rows: list[tuple[Any, ...]],
    *,
    mode: str,
    keys: tuple[str, ...],
    when_matched: str,
    when_not_matched: str,
) -> dict[str, Any]:
    """Write rows to a Nova table as the user, in batches of literal SQL.

    Values are rendered by the proxy's ``sql_literal`` (escaped, typed), the
    same rendering the proxy uses to bind prepared-statement parameters.
    """
    from app.proxy.session import sql_literal

    name = ".".join(_quote(part) for part in target)
    names = ", ".join(_quote(column) for column in columns)
    if mode not in {"append", "overwrite", "merge"}:
        raise RpcError("mode must be append, overwrite or merge")
    if mode == "merge":
        return await _merge_nova(caller, name, columns, rows, keys, when_matched,
                                 when_not_matched)
    if mode == "overwrite" and not rows:
        await _run(caller, f"TRUNCATE TABLE {name}")
        return {"inserted": 0, "rows": 0}
    for start in range(0, len(rows), INSERT_BATCH):
        values = ", ".join(
            "(" + ", ".join(sql_literal(value) for value in row) + ")"
            for row in rows[start:start + INSERT_BATCH]
        )
        verb = "INSERT OVERWRITE" if mode == "overwrite" and start == 0 else "INSERT INTO"
        await _run(caller, f"{verb} {name} ({names}) VALUES {values}")
    return {"inserted": len(rows), "rows": len(rows)}


async def _merge_nova(
    caller: Caller,
    name: str,
    columns: tuple[str, ...],
    rows: list[tuple[Any, ...]],
    keys: tuple[str, ...],
    when_matched: str,
    when_not_matched: str,
) -> dict[str, Any]:
    from app.proxy.session import sql_literal

    if not keys:
        raise RpcError("merge_into needs on=[...] key columns")
    lowered = [column.lower() for column in columns]
    try:
        positions = [lowered.index(key.lower()) for key in keys]
    except ValueError:
        raise RpcError("Key columns must be columns of the data") from None
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        key = tuple(row[position] for position in positions)
        if key in seen:
            raise RpcError("The data has more than one row for the same key")
        seen.add(key)
    key_names = [columns[position] for position in positions]
    on = " AND ".join(f"t.{_quote(key)} = s.{_quote(key)}" for key in key_names)
    clauses = []
    values = [column for column in columns if column not in key_names]
    if when_matched == "update" and values:
        clauses.append(
            "WHEN MATCHED THEN UPDATE SET "
            + ", ".join(f"{_quote(column)} = s.{_quote(column)}" for column in values)
        )
    elif when_matched == "delete":
        clauses.append("WHEN MATCHED THEN DELETE")
    elif when_matched not in {"update", "ignore"}:
        raise RpcError("when_matched must be update, delete or ignore")
    if when_not_matched == "insert":
        clauses.append(
            "WHEN NOT MATCHED THEN INSERT ("
            + ", ".join(_quote(column) for column in columns)
            + ") VALUES ("
            + ", ".join(f"s.{_quote(column)}" for column in columns)
            + ")"
        )
    elif when_not_matched != "ignore":
        raise RpcError("when_not_matched must be insert or ignore")
    if not clauses or not rows:
        return {"rows": 0}
    for start in range(0, len(rows), MERGE_BATCH):
        source = " UNION ALL ".join(
            "SELECT " + ", ".join(
                f"{sql_literal(value)} AS {_quote(column)}"
                for column, value in zip(columns, row, strict=True)
            )
            for row in rows[start:start + MERGE_BATCH]
        )
        await _run(caller, f"MERGE INTO {name} t USING ({source}) s ON {on} {' '.join(clauses)}")
    return {"rows": len(rows)}


# ── Stages ────────────────────────────────────────────────────────────────


async def _put(target: str, content: bytes, caller: Caller) -> dict[str, Any]:
    from app.common.audit import write_audit_log
    from app.modules.stages.service import caller_actor, discover_stages, stage_service

    match = _STAGE_PATH.match(target.strip())
    if not match:
        raise RpcError("Name the file as @stage/path/file.parquet")
    path = match.group("path").strip("/")
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise RpcError("The stage path is not valid")
    parts = match.group("name").split(".")
    if len(parts) > 3:
        raise RpcError("Name the stage as @stage, @schema.stage or @database.schema.stage")
    database, schema = (None, None)
    if len(parts) == 3:
        database, schema = parts[0], parts[1]
    elif len(parts) == 2:
        schema = parts[0]
    stage_name = parts[-1]
    actor = caller_actor(
        caller.username, caller.role, session_id=caller.session_id, version=caller.version
    )
    stages = await discover_stages(actor, name=stage_name, database=database, schema=schema)
    if len(stages) != 1:
        raise RpcError(
            f"Stage @{match.group('name')} is "
            + ("ambiguous; qualify it as @database.schema.stage" if stages else "not found")
        )
    stage = stages[0]
    status, error = "SUCCESS", None
    try:
        # Ranger authorizes the write before the registration's storage is read.
        registration = await stage_service.get_stage(stage["id"], actor, action="write")
        if registration is None:
            raise RpcError(f"Stage @{match.group('name')} is not found")
        await stage_service.upload_file(registration, path, content)
    except Exception as exc:
        status, error = "DENIED" if type(exc).__name__ == "StageAccessDenied" else "ERROR", str(exc)
        raise
    finally:
        try:
            await write_audit_log(
                event_type="notebook_put",
                user_name=caller.username,
                action="PUT_STAGE_FILE",
                object_type="STAGE",
                object_name=f"@{stage['name']}/{path}",
                status=status,
                error_message=error,
                session_id=caller.session_id,
                active_role=caller.role,
            )
        except Exception:  # noqa: BLE001
            logger.warning("Could not audit a notebook stage write")
    return {"path": f"@{match.group('name')}/{path}", "bytes": len(content)}
