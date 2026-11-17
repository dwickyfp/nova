"""Pipe statements: create, change, drop and describe pipes as the caller.

A pipe belongs to the caller's active role (the owner role). It runs on the task
worker as that role's scheduled-task account: its schedule is a ``pipe.load``
task row next to the pipe, created and changed in the same metadata transaction.

Creating a pipe is checked now, as the creator: the role must be able to write
the table and read the stage (Ranger), the stage path must name one registered
stage, and the INSERT must be valid for the files already there (``EXPLAIN`` as
the creator). Every run checks the owner role again before it loads.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.common.audit import write_audit_log
from app.common.metadata_actors import metadata_scope
from app.core.metadata_db import MetadataConflict
from app.modules.access_control.security_context import SecurityContext
from app.modules.pipes.definition import (
    PipeDefinition,
    PipeFile,
    PipeProperties,
    batch_label,
    parse_properties,
    render_batch,
    split_name,
)
from app.modules.pipes.repository import PipeRepository

logger = logging.getLogger(__name__)

HANDLER = "pipe.load"
TASK_SCHEMA = "__pipes"
ADMIN_ROLES = frozenset({"ACCOUNTADMIN", "user_admin", "security_admin"})


class PipeError(ValueError):
    """A refused pipe statement; the message is safe to show."""


@dataclass(frozen=True, slots=True)
class PipeCaller:
    username: str
    role: str | None
    session_id: str | None = None
    security_context_version: int = 1

    def actor(self) -> SecurityContext:
        from app.modules.stages.service import caller_actor

        if not self.role:
            raise PipeError("Pipe statements need an active role")
        return caller_actor(self.username, self.role)

    @property
    def is_admin(self) -> bool:
        return self.role in ADMIN_ROLES


#: Runs one SQL text on the engine as the caller; returns an error message or ``None``.
EngineCheck = Callable[[str], Awaitable[str | None]]


def task_name(pipe_id: UUID | str) -> str:
    return "pipe_" + UUID(str(pipe_id)).hex


def _schedule(properties: PipeProperties, state: str) -> dict[str, Any]:
    running = state == "RUNNING" and properties.auto_ingest
    return {
        "schedule_kind": "interval" if properties.auto_ingest else "manual",
        "schedule_expr": f"{properties.poll_interval} seconds" if properties.auto_ingest
        else None,
        "lifecycle_state": "STARTED" if running else "SUSPENDED",
    }


def properties_of(row: dict[str, Any]) -> PipeProperties:
    return PipeProperties(bool(row["auto_ingest"]), int(row["poll_interval"]),
                          int(row["batch_size"]), int(row["batch_files"]))


def source_of(row: dict[str, Any]) -> dict[str, Any]:
    source = row["source"]
    return source if isinstance(source, dict) else json.loads(source)


def logical_file(row: dict[str, Any], file_name: str) -> str:
    return f"{source_of(row)['stage_label']}/{file_name}"


class PipeControl:
    # ── create ────────────────────────────────────────────────────────

    async def create(
        self, definition: PipeDefinition, caller: PipeCaller, *,
        database: str | None, schema: str | None, engine_check: EngineCheck,
    ) -> tuple[dict[str, Any], list[str]]:
        from app.modules.pipes import storage
        from app.modules.pipes.runner import table_access
        from app.modules.stages.access import StageAccessDenied
        from app.modules.task_orchestration.repository import TaskOrchestrationRepository

        actor = caller.actor()
        role = str(caller.role)
        pipe_db, pipe_name = split_name(definition.name, database, "pipe")
        target_db, target_table = split_name(definition.target, database or pipe_db, "table")
        bound = await TaskOrchestrationRepository(actor).get_role_execution_user(role)
        if not bound:
            raise PipeError(
                f"The role {role} has no scheduled-task account yet, so a pipe cannot run as "
                "it. Ask an administrator to set one up for the role."
            )
        async with metadata_scope(actor, read_only=True) as connection:
            existing = await PipeRepository(connection, actor).get_pipe(pipe_db, pipe_name)
        if existing is not None and not definition.or_replace:
            if definition.if_not_exists:
                return existing, [f"Pipe {pipe_db}.{pipe_name} already exists; nothing changed"]
            raise PipeError(f"Pipe {pipe_db}.{pipe_name} already exists")
        if existing is not None and existing["owner_role"] != role:
            raise PipeError(
                f"Pipe {pipe_db}.{pipe_name} belongs to the role {existing['owner_role']}; "
                "activate that role to replace it"
            )
        reason = await table_access(caller.username, role, target_db, target_table)
        if reason is not None:
            raise PipeError(reason.replace("can no longer insert", "cannot insert"))
        try:
            location = await storage.resolve_stage(
                caller.username, role, definition.source, database, schema,
            )
        except StageAccessDenied:
            raise PipeError(
                f"The role {role} cannot read the stage @{definition.source.stage_name}"
            ) from None
        await self._check_engine(definition, location, target_db, target_table, engine_check)

        pipe_id = uuid4()
        source = {
            **definition.source.as_json(),
            "stage_id": str(location.stage["id"]),
            "stage_label": location.label,
            "segments": list(location.segments),
            "session_database": database or pipe_db,
        }
        properties = definition.properties
        task = {
            "id": str(uuid4()), "name": task_name(pipe_id), "database_name": pipe_db,
            "schema_name": TASK_SCHEMA, "definition": "", "timezone": "UTC",
            "owner_role": role, "overlap_policy": "skip", "handler": HANDLER,
            "handler_config": {"pipe_id": str(pipe_id)},
            **_schedule(properties, "RUNNING"),
        }
        warnings: list[str] = []
        async with metadata_scope(actor) as connection:
            from app.modules.task_orchestration.metadata_repository import TaskMetadataRepository

            pipes = PipeRepository(connection, actor)
            tasks = TaskMetadataRepository(connection, actor)
            if existing is not None:
                await self._remove(pipes, tasks, existing)
                warnings.append(
                    "The pipe was replaced: its file history starts again, so files "
                    "already loaded by the old pipe load again"
                )
            created_task = await tasks.create_task(task)
            row = await pipes.create_pipe({
                "id": pipe_id, "database_name": pipe_db, "name": pipe_name,
                "target_table": f"{target_db}.{target_table}",
                "definition": definition.insert_sql, "source": source,
                "auto_ingest": properties.auto_ingest,
                "poll_interval": properties.poll_interval,
                "batch_size": properties.batch_size, "batch_files": properties.batch_files,
                "state": "RUNNING", "owner_role": role, "created_by": caller.username,
                "task_id": created_task["id"],
            })
            await self._kick(connection, actor, row, created_task, bound, "create")
        await self._audit(caller, "CREATE_PIPE", row, "SUCCESS")
        return row, warnings

    async def _check_engine(
        self, definition: PipeDefinition, location: Any, target_db: str, target_table: str,
        engine_check: EngineCheck,
    ) -> None:
        """EXPLAIN the first batch as the creator; without files, the table must exist."""
        from app.modules.pipes import storage

        config = storage.storage_config(location.stage)
        listed = await storage.list_files(config, location.segments)
        if not listed:
            name = f"`{target_db.replace('`', '``')}`.`{target_table.replace('`', '``')}`"
            error = await engine_check(f"DESC {name}")
        else:
            first = listed[0]
            csv_params = columns = None
            if definition.source.form == "stage" and definition.source.format == "csv":
                csv_params, columns = await storage.csv_layout(config, first[0])
            read = storage.file_read(config, first[0], definition.source, 0,
                                     csv_params=csv_params, columns=columns)
            label = batch_label(uuid4(), [PipeFile(first[0], first[1], first[2])])
            sql = render_batch(definition.insert_sql, label, [read])
            error = await engine_check("EXPLAIN " + sql)
        if error:
            from app.modules.pipes.runner import safe_message

            physical = f"s3://{config.bucket}/{config.base_prefix}/"
            raise PipeError(
                "The pipe's INSERT does not run: "
                + safe_message(error, {physical: location.label + "/"})
            )

    async def _kick(
        self, connection: Any, actor: SecurityContext, pipe: dict[str, Any],
        task: dict[str, Any], bound: str, reason: str,
    ) -> None:
        """Queue a run now (creation, resume, retry) as the scheduled-task account."""
        from app.modules.task_orchestration.repository import (
            TaskOrchestrationRepository,
            task_graph_id,
        )

        await PipeRepository(connection, actor).wake(pipe["id"])
        run_id = str(uuid5(NAMESPACE_URL, f"nova/pipe/{pipe['id']}/{reason}/{uuid4()}"))
        await TaskOrchestrationRepository(actor, connection=connection).create_graph_run_once({
            "id": run_id, "graph_id": task_graph_id(task), "trigger_type": "schedule",
            "state": "pending", "overlap_policy": "skip", "execution_user": bound,
            "execution_role": pipe["owner_role"],
        })

    @staticmethod
    async def _remove(pipes: PipeRepository, tasks: Any, row: dict[str, Any]) -> None:
        await pipes.delete_pipe(row["id"])
        await tasks.delete_task(row["task_id"])

    # ── lookups ───────────────────────────────────────────────────────

    async def _find(self, connection, actor, name: tuple[str, ...], database: str | None):
        pipe_db, pipe_name = split_name(name, database, "pipe")
        row = await PipeRepository(connection, actor).get_pipe(pipe_db, pipe_name)
        if row is None:
            return pipe_db, pipe_name, None
        return pipe_db, pipe_name, row

    @staticmethod
    def _require_owner(row: dict[str, Any], caller: PipeCaller, verb: str) -> None:
        if row["owner_role"] != caller.role:
            raise PipeError(
                f"Only the owner role {row['owner_role']} can {verb} this pipe; "
                "activate that role"
            )

    # ── drop ──────────────────────────────────────────────────────────

    async def drop(
        self, name: tuple[str, ...], caller: PipeCaller, *, database: str | None,
        if_exists: bool,
    ) -> dict[str, Any] | None:
        from app.modules.task_orchestration.metadata_repository import TaskMetadataRepository

        actor = caller.actor()
        async with metadata_scope(actor) as connection:
            pipe_db, pipe_name, row = await self._find(connection, actor, name, database)
            if row is None:
                if if_exists:
                    return None
                raise PipeError(f"Pipe {pipe_db}.{pipe_name} does not exist")
            await self._remove(PipeRepository(connection, actor),
                               TaskMetadataRepository(connection, actor), row)
        await self._audit(caller, "DROP_PIPE", row, "SUCCESS")
        return row

    # ── alter ─────────────────────────────────────────────────────────

    async def alter(
        self, name: tuple[str, ...], caller: PipeCaller, *, database: str | None,
        action: str, properties: tuple[tuple[str, str], ...] = (),
        file_name: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        from app.modules.task_orchestration.metadata_repository import TaskMetadataRepository
        from app.modules.task_orchestration.repository import TaskOrchestrationRepository

        actor = caller.actor()
        message = ""
        async with metadata_scope(actor) as connection:
            pipes = PipeRepository(connection, actor)
            tasks = TaskMetadataRepository(connection, actor)
            pipe_db, pipe_name, row = await self._find(connection, actor, name, database)
            if row is None:
                raise PipeError(f"Pipe {pipe_db}.{pipe_name} does not exist")
            if action == "suspend":
                if not caller.is_admin:
                    self._require_owner(row, caller, "suspend")
                if row["state"] != "SUSPENDED":
                    row = await pipes.update_pipe(row["id"], {"state": "SUSPENDED"},
                                                  expected_revision=row["revision"])
                await tasks.set_lifecycle(row["task_id"], "SUSPENDED")
                message = "Pipe suspended; files are not loaded until RESUME"
                kick = False
            else:
                self._require_owner(row, caller, {"resume": "resume", "set": "change"}.get(
                    action, "retry files of"))
                kick = True
                current = properties_of(row)
                changes: dict[str, Any] = {}
                if action == "resume":
                    await self._check_access(row, caller)
                    if row["state"] != "RUNNING":
                        changes.update(state="RUNNING", last_error=None)
                    message = "Pipe resumed; it loads new files now"
                elif action == "set":
                    updated = parse_properties(properties, current)
                    for key in ("auto_ingest", "poll_interval", "batch_size", "batch_files"):
                        if getattr(updated, key) != getattr(current, key):
                            changes[key] = getattr(updated, key)
                    if updated.auto_ingest and row["state"] == "FINISHED":
                        changes["state"] = "RUNNING"
                    current = updated
                    kick = updated.auto_ingest and bool(changes)
                    message = "Pipe properties changed"
                else:
                    count = await pipes.retry_files(row["id"], self._relative(row, file_name))
                    if count == 0:
                        raise PipeError(await self._nothing_to_retry(pipes, row, file_name))
                    if row["state"] == "FINISHED":
                        changes["state"] = "RUNNING"
                    message = f"{count} file(s) queued to load again"
                if changes:
                    row = await pipes.update_pipe(row["id"], changes,
                                                  expected_revision=row["revision"])
                await self._sync_task(tasks, row, current)
                if kick and row["state"] == "RUNNING":
                    bound = await TaskOrchestrationRepository(actor, connection=connection) \
                        .get_role_execution_user(row["owner_role"])
                    if not bound:
                        raise PipeError(
                            f"The role {row['owner_role']} has no scheduled-task account; "
                            "ask an administrator to set one up"
                        )
                    task = await tasks.get_task(row["task_id"])
                    if task is None:
                        raise PipeError("The pipe's schedule is missing; replace the pipe")
                    await self._kick(connection, actor, row, task, bound, action)
        await self._audit(caller, "ALTER_PIPE_" + action.upper(), row, "SUCCESS")
        return row, message

    async def _sync_task(self, tasks: Any, row: dict[str, Any],
                         properties: PipeProperties) -> None:
        task = await tasks.get_task(row["task_id"])
        if task is None:
            raise PipeError("The pipe's schedule is missing; replace the pipe")
        wanted = _schedule(properties, row["state"])
        lifecycle = wanted.pop("lifecycle_state")
        if any(task.get(key) != value for key, value in wanted.items()):
            await tasks.update_task(task["id"], wanted, expected_version=int(task["version"]))
        if task.get("lifecycle_state") != lifecycle:
            await tasks.set_lifecycle(task["id"], lifecycle)

    @staticmethod
    def _relative(row: dict[str, Any], file_name: str | None) -> str | None:
        """Accept the path SHOW/pipe_files print (``@db.schema.stage/dir/f``) or ``dir/f``."""
        if file_name is None:
            return None
        text = file_name.strip()
        label = source_of(row)["stage_label"] + "/"
        if text.startswith(label):
            return text[len(label):]
        if text.startswith("@"):
            short = "@" + source_of(row)["stage_name"] + "/"
            if text.startswith(short):
                return text[len(short):]
            raise PipeError(f"File {file_name} is not in this pipe's stage")
        return text.lstrip("/")

    async def _nothing_to_retry(self, pipes: PipeRepository, row: dict[str, Any],
                                file_name: str | None) -> str:
        if file_name is None:
            return "No file of this pipe failed; nothing to retry"
        states = await pipes.file_states(row["id"], self._relative(row, file_name) or "")
        if not states:
            return f"File {file_name} does not belong to pipe {row['database_name']}.{row['name']}"
        return (f"File {file_name} has not failed (it is {', '.join(sorted(set(states)))}); "
                "only failed files are retried")

    async def _check_access(self, row: dict[str, Any], caller: PipeCaller) -> None:
        from app.modules.pipes import storage
        from app.modules.pipes.runner import table_access
        from app.modules.stages.access import StageAccessDenied

        database, table = str(row["target_table"]).split(".", 1)
        role = str(caller.role)
        reason = await table_access(caller.username, role, database, table)
        if reason is not None:
            raise PipeError(reason.replace("can no longer insert", "cannot insert"))
        try:
            stage = await storage.authorize_stage(caller.username, role,
                                                  source_of(row)["stage_id"])
        except StageAccessDenied:
            raise PipeError(
                f"The role {role} cannot read the stage {source_of(row)['stage_label']}"
            ) from None
        if stage is None:
            raise PipeError(f"The stage {source_of(row)['stage_label']} no longer exists")

    # ── read ──────────────────────────────────────────────────────────

    async def visible(self, caller: PipeCaller, database: str | None = None,
                      *, files: bool = False) -> tuple[list[dict], dict[str, dict], list[dict]]:
        actor = caller.actor()
        async with metadata_scope(actor, read_only=True) as connection:
            repo = PipeRepository(connection, actor)
            rows = await repo.list_pipes(database)
            ids = [row["id"] for row in rows]
            status = await repo.load_status(ids)
            ledger = await repo.list_files(ids) if files else []
        return rows, status, ledger

    async def describe(self, name: tuple[str, ...], caller: PipeCaller,
                       database: str | None) -> dict[str, Any]:
        actor = caller.actor()
        async with metadata_scope(actor, read_only=True) as connection:
            pipe_db, pipe_name, row = await self._find(connection, actor, name, database)
        if row is None:
            raise PipeError(f"Pipe {pipe_db}.{pipe_name} does not exist")
        return row

    @staticmethod
    async def _audit(caller: PipeCaller, action: str, row: dict[str, Any], status: str) -> None:
        try:
            await write_audit_log(
                event_type="pipe", user_name=caller.username, action=action,
                object_type="pipe", object_name=f"{row['database_name']}.{row['name']}",
                status=status, database_name=row["database_name"], active_role=caller.role,
                session_id=caller.session_id,
                security_context_version=caller.security_context_version,
                decision="ALLOW",
            )
        except Exception:  # noqa: BLE001 - the change is committed; record the gap
            logger.warning("pipe audit entry could not be written for %s", action)


pipe_control = PipeControl()
__all__ = ["MetadataConflict", "PipeCaller", "PipeError", "PipeControl", "pipe_control"]
