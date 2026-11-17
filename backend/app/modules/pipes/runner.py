"""One pipe run on the task worker, as the owner role's scheduled-task account.

A run claims the pipe with a fenced lease, then makes passes until nobody asked
for another one (``wake_count`` unchanged) or the pipe stops running. A pass:

1. checks that the owner role may still write the table and read the stage
   (Ranger), and suspends the pipe with the reason when it may not;
2. resolves the stage's storage for this run only, lists the files matching
   the pipe's path and records new file versions (name + etag) as UNLOADED;
3. settles batches a lost run left LOADING by looking up their label in the
   engine (``information_schema.loads``), never by replaying them blindly;
4. groups queued files into batches (BATCH_FILES, BATCH_SIZE), records each
   batch's label and files as LOADING, then runs one ``INSERT ... WITH LABEL``.

The engine refuses a label it already committed, so a batch whose outcome was
lost can be submitted again under the same label without loading twice. A batch
that fails on its data is split so that one bad file does not hold back the
others; a file that fails alone is ERROR with the engine's message until a
``RETRY``. Access the owner role lost suspends the pipe and loads nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from app.common.metadata_actors import metadata_scope, service_actor
from app.modules.pipes.definition import (
    PipeDefinitionError,
    PipeFile,
    PipeSource,
    batch_label,
    batches,
    render_batch,
)
from app.modules.pipes.repository import WORKER, LeaseLost, PipeRepository

logger = logging.getLogger(__name__)

LEASE_SECONDS = 180
RENEW_SECONDS = 30
#: Engine states of a load that has not settled yet.
_IN_PROGRESS = frozenset({"PENDING", "QUEUEING", "LOADING", "PREPARED", "COMMITTED", "ETL"})
_ACCESS_CODES = frozenset({1044, 1045, 1142, 1143, 1227, 5203})
_CONNECTION_CODES = frozenset({2002, 2003, 2006, 2013, 2055})


def worker_actor():
    return service_actor(WORKER)


class AccessRevoked(RuntimeError):
    """The owner role may no longer load this pipe; the message says what changed."""


class OutcomeUnknown(RuntimeError):
    """A batch was submitted but its outcome was lost; its label settles it later."""


class EngineFailure(Exception):
    """An engine refusal or failure, classified: duplicate_label, access, data or unknown.

    A plain exception, not a frozen dataclass: raising sets ``__cause__`` and
    ``__traceback__``, which a frozen instance refuses.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(kind)
        self.kind = kind
        self.message = message


@dataclass(frozen=True, slots=True)
class LabelState:
    finished: bool = False
    in_progress: bool = False
    cancelled: bool = False
    rows: int | None = None
    error: str | None = None

    @property
    def found(self) -> bool:
        return self.finished or self.in_progress or self.cancelled


def safe_message(message: str, location: dict[str, str] | None = None) -> str:
    """An engine message without credentials or storage paths (stage paths instead)."""
    from app.common.sql_guard import redact_sql_credentials

    text = redact_sql_credentials(str(message or ""))
    for physical, logical in (location or {}).items():
        text = text.replace(physical, logical)
    return " ".join(text.split())[:2000] or "The load failed"


def classify(exc: BaseException) -> EngineFailure:
    from asyncmy.errors import MySQLError  # type: ignore[import-untyped]

    if isinstance(exc, MySQLError) and exc.args and isinstance(exc.args[0], int):
        code = exc.args[0]
        message = str(exc.args[1]) if len(exc.args) > 1 else ""
        lowered = message.lower()
        if "has already been used" in lowered and "label" in lowered:
            return EngineFailure("duplicate_label", message)
        if code in _ACCESS_CODES or "access denied" in lowered:
            return EngineFailure("access", message)
        if code in _CONNECTION_CODES:
            return EngineFailure("unknown", message)
        return EngineFailure("data", message)
    return EngineFailure("unknown", type(exc).__name__)


class OwnerEngine:
    """The engine as the owner: one impersonated session with the owner role active."""

    def __init__(self, connection: Any, timeout: float) -> None:
        self._connection = connection
        self._timeout = timeout

    async def insert(self, sql: str) -> int:
        """Run one labelled INSERT. Nothing from ``sql`` is logged: it holds credentials."""
        try:
            async with asyncio.timeout(self._timeout):
                async with self._connection.cursor() as cursor:
                    await cursor.execute(sql)
                    return int(cursor.rowcount or 0)
        except Exception as exc:  # noqa: BLE001 - classified, never echoed raw
            raise classify(exc) from None

    async def label_state(self, database: str, label: str) -> LabelState:
        from app.modules.task_orchestration.execution import _dict_cursor

        async with _dict_cursor(self._connection) as cursor:
            await cursor.execute(
                "SELECT STATE, SINK_ROWS, ERROR_MSG FROM information_schema.loads "
                "WHERE DB_NAME = %s AND LABEL = %s ORDER BY CREATE_TIME DESC, ID DESC",
                (database, label),
            )
            rows = list(await cursor.fetchall() or [])
        states = [str(row.get("STATE") or "").upper() for row in rows]
        for row, state in zip(rows, states, strict=True):
            if state == "FINISHED":
                return LabelState(finished=True, rows=int(row.get("SINK_ROWS") or 0))
        if any(state in _IN_PROGRESS for state in states):
            return LabelState(in_progress=True)
        if states:
            return LabelState(cancelled=True, error=str(rows[0].get("ERROR_MSG") or "Cancelled"))
        return LabelState()


@asynccontextmanager
async def owner_engine(executor: Any, owner: str, role: str, database: str) -> AsyncIterator[Any]:
    from app.core.config import settings
    from app.modules.task_orchestration.credentials import CredentialUnavailable
    from app.modules.task_orchestration.execution import NodeExecutionError, TaskSpec, _dict_cursor

    try:
        opened = executor.owner_connection(owner)
        connection = await opened.__aenter__()
    except CredentialUnavailable as exc:
        raise AccessRevoked(f"The scheduled-task account cannot sign in: {exc}") from None
    try:
        try:
            async with _dict_cursor(connection) as cursor:
                await executor._prepare_task_session(
                    cursor, TaskSpec(name="pipe", body="", database=database, active_role=role),
                )
        except NodeExecutionError as exc:
            raise AccessRevoked(str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - USE refused: the database is not reachable
            failure = classify(exc)
            if failure.kind == "access":
                raise AccessRevoked(safe_message(failure.message)) from None
            raise
        yield OwnerEngine(connection, settings.WORKER_TASK_POLL_TIMEOUT_SECONDS)
    finally:
        await opened.__aexit__(None, None, None)


async def table_access(owner: str, role: str, database: str, table: str) -> str | None:
    """Why ``role`` may not insert into the table (Ranger), or ``None`` when it may."""
    from app.modules.access_control.service import access_control_service

    effective = await access_control_service.effective_access(
        principal=owner, active_role=role, resource=f"{database}.{table}",
    )
    allowed = {str(item).upper() for item in effective.get("object_access", [])}
    if allowed & {"INSERT", "ALL"}:
        return None
    return f"The role {role} can no longer insert into {database}.{table}"


@dataclass
class PipeRuntime:
    """Everything a run touches outside PostgreSQL; replaced in tests."""

    executor: Any = None
    table_access: Callable[[str, str, str, str], Awaitable[str | None]] = table_access
    authorize_stage: Callable[..., Awaitable[Any]] | None = None
    storage_config: Callable[[dict[str, Any]], Any] | None = None
    list_files: Callable[..., Awaitable[list[Any]]] | None = None
    csv_layout: Callable[..., Awaitable[tuple[dict[str, str], list[str] | None]]] | None = None
    engine: Callable[..., Any] | None = None

    def __post_init__(self) -> None:
        from app.modules.pipes import storage

        self.authorize_stage = self.authorize_stage or storage.authorize_stage
        self.storage_config = self.storage_config or storage.storage_config
        self.list_files = self.list_files or storage.list_files
        self.csv_layout = self.csv_layout or storage.csv_layout
        if self.engine is None:
            executor = self.executor
            self.engine = lambda owner, role, database: owner_engine(
                executor, owner, role, database
            )


@dataclass
class PassResult:
    staged: int = 0
    loaded_files: int = 0
    batches: int = 0
    failed_files: int = 0
    pending: int = 0
    last_error: str | None = None
    notes: list[str] = field(default_factory=list)


@asynccontextmanager
async def _ledger() -> AsyncIterator[PipeRepository]:
    actor = worker_actor()
    async with metadata_scope(actor) as connection:
        yield PipeRepository(connection, actor)


class PipeRun:
    def __init__(
        self, pipe_id: UUID | str, owner: str, role: str, runtime: PipeRuntime,
        *, heartbeat: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.pipe_id = UUID(str(pipe_id))
        self.owner = owner
        self.role = role
        self.runtime = runtime
        self.heartbeat = heartbeat
        self.token = uuid4()
        self.generation = 0
        self.lost = False
        self.location: dict[str, str] = {}

    # ── lease ──────────────────────────────────────────────────────────

    async def _keep(self) -> None:
        while True:
            await asyncio.sleep(RENEW_SECONDS)
            try:
                async with _ledger() as repo:
                    await repo.renew(self.pipe_id, self.token, self.generation,
                                     seconds=LEASE_SECONDS)
            except LeaseLost:
                self.lost = True
                return
            if self.heartbeat is not None:
                await self.heartbeat()

    async def run(self) -> str:
        async with _ledger() as repo:
            pipe = await repo.claim(self.pipe_id, self.token, seconds=LEASE_SECONDS)
        if pipe is None:
            return "Another run is loading this pipe"
        self.generation = int(pipe["claim_generation"])
        keeper = asyncio.create_task(self._keep())
        summaries: list[PassResult] = []
        try:
            while True:
                if pipe["state"] != "RUNNING":
                    break
                if pipe["owner_role"] != self.role:
                    raise AccessRevoked("The pipe's owner role changed")
                wakes = pipe["wake_count"]
                summaries.append(await self._pass(pipe))
                if self.lost:
                    break
                async with _ledger() as repo:
                    pipe = await repo.fence(self.pipe_id, self.token, self.generation)
                if pipe["wake_count"] == wakes:
                    break
        except LeaseLost:
            self.lost = True
        finally:
            keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await keeper
            if not self.lost:
                with contextlib.suppress(Exception):
                    async with _ledger() as repo:
                        await repo.release(self.pipe_id, self.token, self.generation)
        if not summaries:
            return f"Pipe is {pipe['state']}; nothing loaded"
        total = PassResult()
        for item in summaries:
            total.staged += item.staged
            total.loaded_files += item.loaded_files
            total.batches += item.batches
            total.failed_files += item.failed_files
            total.pending = item.pending
            total.notes.extend(item.notes)
        text = (
            f"{total.staged} new file(s) found; {total.loaded_files} file(s) loaded in "
            f"{total.batches} batch(es); {total.failed_files} file(s) failed"
        )
        if total.pending:
            text += f"; {total.pending} file(s) still loading"
        return "; ".join([text, *total.notes])

    # ── one pass ───────────────────────────────────────────────────────

    async def _suspend(self, pipe: dict[str, Any], reason: str) -> None:
        async with _ledger() as repo:
            await repo.set_state(self.pipe_id, self.token, self.generation,
                                 state="SUSPENDED", last_error=reason[:4096])
            await repo.suspend_schedule(self.pipe_id, self.token, self.generation,
                                        pipe["task_id"])
        logger.info("pipe %s suspended: owner access changed", self.pipe_id)

    async def _pass(self, pipe: dict[str, Any]) -> PassResult:
        from app.modules.stages.access import StageAccessDenied

        result = PassResult()
        source_json = pipe["source"] if isinstance(pipe["source"], dict) else json.loads(
            pipe["source"])
        source = PipeSource(
            source_json["form"], source_json["stage_name"], tuple(source_json["path"]),
            source_json["pattern"], source_json["format"],
            tuple(tuple(item) for item in source_json.get("options", [])),  # type: ignore[misc]
        )
        database, table = str(pipe["target_table"]).split(".", 1)
        runtime = self.runtime
        assert runtime.authorize_stage and runtime.storage_config and runtime.list_files
        assert runtime.engine is not None
        reason = await runtime.table_access(self.owner, self.role, database, table)
        stage = None
        if reason is None:
            try:
                stage = await runtime.authorize_stage(self.owner, self.role,
                                                      source_json["stage_id"])
            except StageAccessDenied:
                reason = (f"The role {self.role} can no longer read the stage "
                          f"{source_json['stage_label']}")
            if reason is None and stage is None:
                reason = f"The stage {source_json['stage_label']} no longer exists"
        if reason is not None:
            await self._suspend(pipe, reason)
            result.notes.append("suspended: " + reason)
            return result
        assert stage is not None
        config = runtime.storage_config(stage)
        physical = f"s3://{config.bucket}/{config.base_prefix}/"
        self.location = {physical: source_json["stage_label"] + "/"}
        segments = tuple(source_json["segments"])
        listed = await runtime.list_files(config, segments)
        present = {(name, version) for name, version, _size, _modified in listed}
        if pipe["auto_ingest"] or pipe["last_polled_at"] is None:
            async with _ledger() as repo:
                result.staged = await repo.stage_files(
                    self.pipe_id, self.token, self.generation,
                    [(name, version, size, modified) for name, version, size, modified in listed],
                )
                await repo.mark_polled(self.pipe_id, self.token, self.generation)
        try:
            async with runtime.engine(self.owner, self.role, database) as engine:
                await self._settle(pipe, engine, source, config, result, present)
                async with _ledger() as repo:
                    queued = await repo.files_in(self.pipe_id, "UNLOADED")
                files = [PipeFile(row["file_name"], row["file_version"], int(row["file_size"]))
                         for row in queued]
                # A queued version that is no longer in the stage (replaced or
                # removed) cannot be read any more: reading the path would load
                # the newer content under the old version.
                for stale in [item for item in files if (item.name, item.version) not in present]:
                    await self._mark_gone(stale, result)
                files = [item for item in files if (item.name, item.version) in present]
                for batch in batches(files, max_files=int(pipe["batch_files"]),
                                     max_bytes=int(pipe["batch_size"])):
                    if self.lost:
                        break
                    await self._load(pipe, engine, source, config, batch, result, split=True)
        except AccessRevoked as exc:
            await self._suspend(pipe, str(exc))
            result.notes.append("suspended: " + str(exc))
            return result
        except OutcomeUnknown as exc:
            result.notes.append(str(exc))
        await self._finish_pass(pipe, result)
        return result

    async def _finish_pass(self, pipe: dict[str, Any], result: PassResult) -> None:
        async with _ledger() as repo:
            pending = len(await repo.files_in(self.pipe_id, "UNLOADED")) + len(
                await repo.files_in(self.pipe_id, "LOADING"))
            result.pending = len(await repo.files_in(self.pipe_id, "LOADING"))
            current = await repo.fence(self.pipe_id, self.token, self.generation)
            state = current["state"]
            if not current["auto_ingest"] and pending == 0 and state == "RUNNING":
                state = "FINISHED"
            error = result.last_error if result.last_error is not None else current["last_error"]
            if state != current["state"] or error != current["last_error"]:
                await repo.set_state(self.pipe_id, self.token, self.generation,
                                     state=state, last_error=error)

    async def _mark_gone(self, item: PipeFile, result: PassResult) -> None:
        label = batch_label(self.pipe_id, [item])
        message = ("This version of the file is no longer in the stage (it was replaced or "
                   "removed); a newer version is loaded as its own file")
        async with _ledger() as repo:
            await repo.begin_batch(self.pipe_id, self.token, self.generation, label,
                                   [(item.name, item.version)], item.size)
            await repo.fail_batch(self.pipe_id, self.token, self.generation, label,
                                  error=message, file_state="ERROR")
        result.failed_files += 1

    async def _settle(self, pipe, engine, source, config, result: PassResult,
                      present: set[tuple[str, str]]) -> None:
        """Batches a lost run left LOADING: the engine's label record decides."""
        async with _ledger() as repo:
            loading = await repo.files_in(self.pipe_id, "LOADING")
        groups: dict[str, list[PipeFile]] = {}
        for row in loading:
            groups.setdefault(row["batch_label"], []).append(
                PipeFile(row["file_name"], row["file_version"], int(row["file_size"]))
            )
        database = str(pipe["target_table"]).split(".", 1)[0]
        for label, files in groups.items():
            state = await engine.label_state(database, label)
            if state.finished:
                async with _ledger() as repo:
                    await repo.finish_batch(self.pipe_id, self.token, self.generation, label,
                                            self._rows(files, state.rows))
                result.loaded_files += len(files)
                result.batches += 1
            elif state.in_progress:
                result.notes.append(f"batch {label} is still loading in the engine")
            elif state.cancelled:
                # The engine rejected the batch: one file is ERROR with the engine's
                # reason; several go back to the queue and split on the next load.
                async with _ledger() as repo:
                    await repo.fail_batch(
                        self.pipe_id, self.token, self.generation, label,
                        error=safe_message(state.error or "Cancelled", self.location),
                        file_state="ERROR" if len(files) == 1 else "UNLOADED",
                    )
                if len(files) == 1:
                    result.failed_files += 1
            elif any((item.name, item.version) not in present for item in files):
                async with _ledger() as repo:
                    await repo.fail_batch(
                        self.pipe_id, self.token, self.generation, label,
                        error="This version of the file is no longer in the stage (it was "
                              "replaced or removed) and was never loaded",
                        file_state="ERROR",
                    )
                result.failed_files += len(files)
            else:
                # Never submitted, or submitted and lost before the engine took it:
                # the same label again cannot load the batch twice.
                await self._submit(pipe, engine, source, config, files, label, result,
                                   split=len(files) > 1)

    @staticmethod
    def _rows(files: list[PipeFile], rows: int | None) -> dict[tuple[str, str], int | None]:
        if len(files) == 1:
            return {(files[0].name, files[0].version): rows}
        return {(item.name, item.version): None for item in files}

    async def _load(self, pipe, engine, source, config, files: list[PipeFile],
                    result: PassResult, *, split: bool) -> None:
        label = batch_label(self.pipe_id, files)
        async with _ledger() as repo:
            await repo.begin_batch(self.pipe_id, self.token, self.generation, label,
                                   [(item.name, item.version) for item in files],
                                   sum(item.size for item in files))
        await self._submit(pipe, engine, source, config, files, label, result, split=split)

    async def _submit(self, pipe, engine, source, config, files: list[PipeFile], label: str,
                      result: PassResult, *, split: bool) -> None:
        from app.modules.pipes.storage import file_read

        assert self.runtime.csv_layout is not None
        csv_params: dict[str, str] | None = None
        columns: list[str] | None = None
        if source.form == "stage" and source.format == "csv":
            csv_params, columns = await self.runtime.csv_layout(config, files[0].name)
        reads = [
            file_read(config, item.name, source, index, csv_params=csv_params, columns=columns)
            for index, item in enumerate(files)
        ]
        try:
            sql = render_batch(pipe["definition"], label, reads)
        except PipeDefinitionError as exc:
            async with _ledger() as repo:
                await repo.fail_batch(self.pipe_id, self.token, self.generation, label,
                                      error=str(exc), file_state="ERROR")
            result.failed_files += len(files)
            result.last_error = str(exc)
            return
        database = str(pipe["target_table"]).split(".", 1)[0]
        try:
            rows = await engine.insert(sql)
        except EngineFailure as failure:
            message = safe_message(failure.message, self.location)
            if failure.kind == "duplicate_label":
                state = await engine.label_state(database, label)
                if state.finished:
                    async with _ledger() as repo:
                        await repo.finish_batch(self.pipe_id, self.token, self.generation,
                                                label, self._rows(files, state.rows))
                    result.loaded_files += len(files)
                    result.batches += 1
                else:
                    result.notes.append(f"batch {label} is already in the engine; "
                                        "its label settles it on a later run")
                return
            if failure.kind == "access":
                async with _ledger() as repo:
                    await repo.fail_batch(self.pipe_id, self.token, self.generation, label,
                                          error=message, file_state="UNLOADED")
                raise AccessRevoked(f"The engine refused the load for role {self.role}: "
                                    f"{message}") from None
            if failure.kind == "unknown":
                raise OutcomeUnknown(
                    f"The outcome of batch {label} was lost ({message}); "
                    "the next run settles it by its label"
                ) from None
            async with _ledger() as repo:
                await repo.fail_batch(self.pipe_id, self.token, self.generation, label,
                                      error=message,
                                      file_state="UNLOADED" if split else "ERROR")
            if split:
                for item in files:
                    await self._load(pipe, engine, source, config, [item], result, split=False)
                return
            result.failed_files += len(files)
            result.last_error = f"{files[0].name}: {message}"
            return
        async with _ledger() as repo:
            await repo.finish_batch(self.pipe_id, self.token, self.generation, label,
                                    self._rows(files, rows))
        result.loaded_files += len(files)
        result.batches += 1
        if self.heartbeat is not None:
            await self.heartbeat()


async def run_pipe_task(
    task: dict[str, Any], owner: str, role: str, executor: Any,
    *, heartbeat: Callable[[], Awaitable[None]] | None = None,
) -> str:
    """The worker entry point for a ``pipe.load`` task node."""
    raw = task.get("handler_config")
    config = json.loads(raw) if isinstance(raw, str) else (raw or {})
    pipe_id = config.get("pipe_id")
    if not pipe_id:
        raise ValueError("The pipe task names no pipe")
    run = PipeRun(pipe_id, owner, role, PipeRuntime(executor=executor), heartbeat=heartbeat)
    return await run.run()
