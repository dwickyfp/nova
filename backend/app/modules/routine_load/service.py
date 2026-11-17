"""Routine Load from Kafka, run by the engine and governed by Nova.

The engine's Routine Load job runs in the background, long after the statement
that created it, without the caller's sign-in. Nova therefore owns three things
around it (design: ``docs/architecture/starrocks-compat/ROUTINE_LOAD.md``):

* **The connection.** ``FROM KAFKA`` names a Nova Kafka connection
  (``"kafka_connection" = "<name>"``). Nova adds the broker list and the SASL
  credential when the engine job is created, after authorizing the caller, so
  no broker credential is ever written in SQL, history or audit. A security
  admin decides which roles may load from each Kafka connection.
* **The owner.** The job is created on the caller's engine session, so the
  engine and Ranger authorize the write as the caller. Nova records the owner
  principal, the owner role and the security-context version in PostgreSQL
  before the engine call (intent first); an outcome Nova could not observe is
  recorded as ``UNKNOWN`` and verified by the reconciler, never replayed.
* **The lifetime.** Only the owner role and administrators pause, resume,
  alter or stop a job Nova manages. ``RESUME`` re-checks the owner's access.
  The reconciler (:mod:`.reconciler`) re-checks every running job and pauses
  one whose owner lost the role, the table write access or the connection.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.common.audit import write_audit_log
from app.common.sql_guard import redact_sql_credentials
from app.core.exceptions import StarRocksError
from app.core.metadata_db import MetadataConflict
from app.modules.access_control.security_context import SecurityContext
from app.modules.access_control.service import AccessControlService

from .repository import routine_load_transaction

log = logging.getLogger(__name__)

WRITE_ACCESS = frozenset({"INSERT", "ALL"})
#: Engine states of a job that is not finished.
ENGINE_LIVE = frozenset({"NEED_SCHEDULE", "RUNNING", "PAUSED", "UNSTABLE"})
ENGINE_RUNNING = frozenset({"NEED_SCHEDULE", "RUNNING", "UNSTABLE"})


class RoutineLoadError(Exception):
    """A routine-load request Nova refused; the message is safe to show."""


@dataclass(frozen=True)
class EngineReply:
    """What one engine statement returned (rows as lists, in column order)."""

    columns: list[str]
    rows: list[list[Any]]


#: Runs one statement on the caller's engine session with the caller's role.
EngineRunner = Callable[[str], Awaitable[EngineReply]]


@dataclass(frozen=True)
class CreateRequest:
    database: str
    job_name: str
    table_name: str
    connection_name: str
    topic: str
    data_format: str
    #: The statement up to the ``FROM KAFKA`` property list, as written.
    sql_before: str
    #: The caller's own ``FROM KAFKA`` properties, verbatim, without the connection.
    source_properties: tuple[str, ...]
    #: Anything after the property list, as written.
    sql_after: str


def quote_name(*parts: str) -> str:
    return ".".join("`" + part.replace("`", "``") + "`" for part in parts)


def engine_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def is_admin(role: str | None) -> bool:
    return role in AccessControlService.SECURITY_ADMIN_ROLES


def _redact(text: str, *secrets: str) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return redact_sql_credentials(text)


async def _audit(
    security: SecurityContext, action: str, target: str, status: str,
    error: str | None = None, sql: str | None = None,
) -> None:
    try:
        await write_audit_log(
            event_type="ROUTINE_LOAD",
            user_name=security.principal,
            action=action,
            object_type="ROUTINE_LOAD",
            object_name=target,
            status=status,
            sql_text=redact_sql_credentials(sql)[:4000] if sql else None,
            error_message=redact_sql_credentials(error)[:2000] if error else None,
            session_id=security.session_id,
            active_role=security.active_role,
            security_context_version=security.security_context_version,
        )
    except Exception:  # noqa: BLE001 - audit is best-effort, never the failure
        log.exception("Could not write routine-load audit entry")


# ── Access checks shared by CREATE, RESUME and the reconciler ─────────────


async def table_write_access(principal: str, role: str, database: str, table: str) -> bool:
    """Whether Ranger lets ``role`` (as ``principal``) insert into the table."""
    from app.modules.access_control.service import access_control_service

    effective = await access_control_service.effective_access(
        principal=principal, active_role=role, resource=f"{database}.{table}",
    )
    granted = {str(item).upper() for item in effective.get("object_access") or []}
    return bool(granted & WRITE_ACCESS)


async def holds_role(principal: str, role: str) -> bool:
    """Whether the engine still assigns ``role`` to ``principal``."""
    from app.core.database import db
    from app.modules.access_control.role_activation import parse_assigned_roles
    from app.modules.users.service import UserService

    try:
        result = await db.execute_system(
            f"SHOW GRANTS FOR {UserService._user_identity(principal)}"
        )
    except StarRocksError:
        return False
    return role in parse_assigned_roles(result.get("rows") or [])


async def grant_state(repo: Any, connection_name: str) -> tuple[dict[str, Any] | None, list[str]]:
    """The connection row and the roles allowed on it, read in ``repo``'s transaction."""
    from app.modules.catalog.metadata_repository import CatalogMetadataRepository

    connection = await CatalogMetadataRepository(repo.connection, repo.actor).get_connection(
        connection_name
    )
    return connection, await repo.connection_roles(connection_name)


async def access_problem(
    job: dict[str, Any], connection: dict[str, Any] | None, roles: list[str],
) -> str | None:
    """Why the job's owner may no longer run it, or ``None``.

    ``connection`` and ``roles`` come from :func:`grant_state`; the engine and
    Ranger are read here, outside any metadata transaction.
    """
    if connection is None or connection.get("connector_type") != "kafka":
        return f"Kafka connection {job['connection_name']} no longer exists"
    owner_role = job["owner_role"]
    if not is_admin(owner_role) and owner_role not in roles:
        return f"Role {owner_role} may no longer load from connection {job['connection_name']}"
    if not await holds_role(job["owner_principal"], owner_role):
        return f"User {job['owner_principal']} no longer has role {owner_role}"
    if not await table_write_access(
        job["owner_principal"], owner_role, job["database_name"], job["table_name"]
    ):
        return (
            f"Role {owner_role} may no longer write to "
            f"{job['database_name']}.{job['table_name']}"
        )
    return None


async def engine_job(run: EngineRunner, database: str, job_name: str) -> dict[str, Any] | None:
    """The engine's newest job with this name (stopped ones included), as a dict."""
    reply = await run(f"SHOW ALL ROUTINE LOAD FOR {quote_name(database, job_name)}")
    jobs = [dict(zip(reply.columns, row, strict=False)) for row in reply.rows]
    if not jobs:
        return None
    return max(jobs, key=lambda item: int(str(item.get("Id") or 0)))


class RoutineLoadService:
    """The statement side: create and control jobs as the caller."""

    async def create(
        self, request: CreateRequest, security: SecurityContext, run: EngineRunner,
    ) -> str:
        """Authorize, record intent, create the engine job as the caller."""
        from app.modules.catalog.connections import connection_service, endpoint_of
        from app.modules.catalog.sources import SOURCES, Credentials

        target = f"{request.database}.{request.job_name}"
        role, principal = security.active_role, security.principal
        try:
            async with routine_load_transaction(security, read_only=True) as repo:
                connection, roles = await grant_state(repo, request.connection_name)
                existing = await repo.live_job(request.database, request.job_name)
            if connection is None or connection.get("connector_type") != "kafka":
                raise RoutineLoadError(
                    f"Kafka connection '{request.connection_name}' does not exist"
                )
            if not is_admin(role) and role not in roles:
                raise RoutineLoadError(
                    f"Role {role} may not load from Kafka connection "
                    f"'{request.connection_name}'; a security admin allows roles on the "
                    "Connections page"
                )
            if existing is not None:
                raise RoutineLoadError(f"Routine load {target} already exists")
            if not await table_write_access(
                principal, role, request.database, request.table_name
            ):
                raise RoutineLoadError(
                    f"Role {role} may not write to {request.database}.{request.table_name}"
                )
        except RoutineLoadError as exc:
            await _audit(security, "CREATE_ROUTINE_LOAD", target, "DENIED", str(exc))
            raise

        # Intent before the engine call: a row in CREATING names the job.
        job_id = str(uuid.uuid4())
        try:
            async with routine_load_transaction(security) as repo:
                job = await repo.create_job({
                    "id": job_id,
                    "database_name": request.database,
                    "job_name": request.job_name,
                    "table_name": request.table_name,
                    "connection_name": request.connection_name,
                    "topic": request.topic,
                    "data_format": request.data_format,
                    "owner_principal": principal,
                    "owner_role": role,
                    "security_context_version": security.security_context_version,
                })
        except MetadataConflict:  # another create took the live name first
            raise RoutineLoadError(f"Routine load {target} already exists") from None

        # The credential is resolved only now, after authorization and intent.
        source = SOURCES["kafka"]
        endpoint = endpoint_of(connection)
        credentials: Credentials | None = None
        if source.requires_credentials(endpoint):
            from app.modules.catalog.metadata_repository import CatalogMetadataRepository

            async with routine_load_transaction(security, read_only=True) as repo:
                stored = await connection_service.stored_credentials(
                    connection, CatalogMetadataRepository(repo.connection, repo.actor)
                )
            credentials = await connection_service.credentials_for(connection, stored)
        properties = source.routine_load_properties(endpoint, credentials)  # type: ignore[attr-defined]
        secret = credentials.password if credentials else ""
        rendered = [f"{engine_literal(key)} = {engine_literal(value)}"
                    for key, value in properties.items()]
        sql = (
            request.sql_before + "(" + ", ".join([*rendered, *request.source_properties]) + ")"
            + request.sql_after
        )
        redacted = _redact(sql, secret)
        try:
            await run(sql)
        except StarRocksError as exc:
            message = _redact(str(exc), secret)
            await self._finish(security, job, "FAILED")
            await _audit(security, "CREATE_ROUTINE_LOAD", target, "ERROR", message, redacted)
            raise RoutineLoadError(message) from None
        except (asyncio.CancelledError, Exception) as exc:
            # The engine may or may not have the job: the reconciler verifies.
            await asyncio.shield(self._finish(security, job, "UNKNOWN"))
            await _audit(security, "CREATE_ROUTINE_LOAD", target, "ERROR",
                         "Outcome unknown; Nova will verify", redacted)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RoutineLoadError(
                "The engine did not confirm the routine load; Nova will check whether it "
                "was created. Run SHOW ROUTINE LOAD in a minute."
            ) from None
        engine_id = None
        try:
            found = await engine_job(run, request.database, request.job_name)
            engine_id = str(found["Id"]) if found else None
        except Exception:  # noqa: BLE001 - the reconciler fills it in
            log.warning("Could not read the new routine load's id", exc_info=True)
        await self._finish(security, job, "ACTIVE", engine_id)
        await _audit(security, "CREATE_ROUTINE_LOAD", target, "SUCCESS", None, redacted)
        return redacted

    async def _finish(
        self, security: SecurityContext, job: dict[str, Any], state: str,
        engine_id: str | None = None,
    ) -> None:
        changes: dict[str, Any] = {"state": state}
        if engine_id:
            changes["engine_job_id"] = engine_id
        async with routine_load_transaction(security) as repo:
            await repo.update_job(job["id"], changes, expected_revision=job["revision"])

    async def control(
        self,
        operation: str,
        database: str,
        job_name: str,
        sql: str,
        security: SecurityContext,
        run: EngineRunner,
    ) -> None:
        """``PAUSE``, ``RESUME``, ``STOP`` or ``ALTER`` a job, as the caller.

        A job Nova manages is changed only by its owner role or an
        administrator. ``RESUME`` and ``ALTER`` need a job Nova manages;
        ``PAUSE`` and ``STOP`` of a job made outside Nova are left to the
        engine, which authorizes them as the caller.
        """
        target = f"{database}.{job_name}"
        action = f"{operation.upper()}_ROUTINE_LOAD"
        role = security.active_role
        try:
            async with routine_load_transaction(security, read_only=True) as repo:
                job = await repo.live_job(database, job_name)
                ended = (
                    await repo.latest_job(database, job_name)
                    if job is None and operation in {"resume", "alter"} else None
                )
                grants = (
                    await grant_state(repo, job["connection_name"])
                    if job is not None and operation == "resume" else None
                )
            if ended is not None:
                raise RoutineLoadError(
                    f"Routine load {target} is {ended['state'].lower()}; a stopped job cannot "
                    "be resumed or changed. Create a new one."
                )
            if job is None and operation in {"resume", "alter"}:
                raise RoutineLoadError(
                    f"Routine load {target} was not created through Nova; create it again "
                    "with a Kafka connection (\"kafka_connection\" = ...)"
                )
            if job is not None and job["owner_role"] != role and not is_admin(role):
                raise RoutineLoadError(
                    f"Routine load {target} belongs to role {job['owner_role']}; only that "
                    "role or an administrator can change it"
                )
            if job is not None and job["state"] in {"CREATING", "UNKNOWN"}:
                raise RoutineLoadError(
                    f"Routine load {target} is still being created; try again in a minute"
                )
            problem = await access_problem(job, *grants) if job and grants else None
            if problem:
                raise RoutineLoadError(f"Routine load {target} cannot resume: {problem}")
        except RoutineLoadError as exc:
            await _audit(security, action, target, "DENIED", str(exc), sql)
            raise
        try:
            await run(sql)
        except StarRocksError as exc:
            message = _redact(str(exc))
            await _audit(security, action, target, "ERROR", message, sql)
            raise RoutineLoadError(message) from None
        if job is not None:
            changes: dict[str, Any] = {}
            if operation == "stop":
                changes["state"] = "STOPPED"
            elif operation == "resume":
                changes["state"] = "ACTIVE"
                if job["owner_principal"] == security.principal and job["owner_role"] == role:
                    changes["security_context_version"] = security.security_context_version
            if changes and (changes.get("state") != job["state"] or len(changes) > 1):
                async with routine_load_transaction(security) as repo:
                    await repo.update_job(job["id"], changes, expected_revision=job["revision"])
        await _audit(security, action, target, "SUCCESS", None, sql)


routine_load_service = RoutineLoadService()
