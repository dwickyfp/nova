"""Transactional PostgreSQL access for Nova's control plane.

Repositories receive a connection only inside an actor-bound transaction. This
module does not initialize schemas or retry transactions with uncertain outcomes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from math import isfinite
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolClosed, PoolTimeout, TooManyRequests

from app.core.exceptions import NovaException
from app.core.metadata_logging import logging_is_private
from app.core.metadata_schema import (
    SchemaManifestError,
    load_migrations,
    verify_applied_migrations,
)
from app.modules.access_control.security_context import SecurityContext

# Operators take the exclusive transaction lock before changing schema or
# epoch. Application transactions hold the shared lock through their last read.
METADATA_DEPLOYMENT_LOCK = 0x4E4F56414D455441
logger = logging.getLogger(__name__)


def _log_database_failure(category: str, error: BaseException) -> None:
    state = getattr(error, "sqlstate", None)
    if not isinstance(state, str) or len(state) != 5 or any(
        char not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" for char in state
    ):
        state = "unknown"
    logger.warning("Metadata transaction failed: category=%s sqlstate=%s", category, state)


class MetadataConflict(NovaException):
    def __init__(self) -> None:
        super().__init__("Metadata changed concurrently; reload and try again", status_code=409)


class MetadataUnavailable(NovaException):
    def __init__(self) -> None:
        super().__init__("Metadata service is unavailable", status_code=503)


class MetadataOutcomeUnknown(NovaException):
    def __init__(self) -> None:
        super().__init__("Metadata outcome requires reconciliation before retry", status_code=503)


class MetadataSchemaMismatch(NovaException):
    def __init__(self) -> None:
        super().__init__("Metadata schema or epoch does not match this deployment", status_code=503)


class MetadataPermissionDenied(NovaException):
    def __init__(self) -> None:
        super().__init__("Metadata operation is not authorized", status_code=403)


class MetadataTimeout(MetadataUnavailable):
    def __init__(self) -> None:
        NovaException.__init__(self, "Metadata operation exceeded its deadline", status_code=503)


class MetadataIntegrityViolation(NovaException):
    def __init__(self) -> None:
        super().__init__("Metadata operation violates a record constraint", status_code=409)


class MetadataReadOnlyMaintenance(NovaException):
    def __init__(self) -> None:
        super().__init__("Metadata writes are paused for maintenance", status_code=503)


class MetadataOperationFailed(NovaException):
    def __init__(self) -> None:
        super().__init__("Metadata operation failed", status_code=500)


@dataclass(frozen=True, slots=True)
class MetadataPoolConfig:
    dsn: str = field(repr=False)
    max_size: int = 10
    max_waiting: int = 40
    acquire_timeout: float = 5
    statement_timeout_ms: int = 15000
    lock_timeout_ms: int = 3000
    idle_transaction_timeout_ms: int = 30000

    def __post_init__(self) -> None:
        try:
            parameters = conninfo_to_dict(self.dsn)
        except psycopg.Error:
            raise ValueError("Invalid metadata connection configuration") from None
        if parameters.get("sslmode") != "verify-full":
            raise ValueError("Metadata connections require sslmode=verify-full")
        hostname = parameters.get("host")
        if not isinstance(hostname, str) or any(
            not host or host.startswith(("/", "@")) for host in hostname.split(",")
        ):
            raise ValueError("Metadata connections require an explicit TLS hostname")
        if parameters.get("dbname") != "nova_control":
            raise ValueError("Metadata connections must select nova_control")
        budgets = (
            self.max_size,
            self.max_waiting,
            self.acquire_timeout,
            self.statement_timeout_ms,
            self.lock_timeout_ms,
            self.idle_transaction_timeout_ms,
        )
        if any(not isfinite(value) or value <= 0 for value in budgets):
            raise ValueError("Metadata pool and transaction budgets must be positive and finite")


class MetadataDatabase:
    def __init__(self, config: MetadataPoolConfig) -> None:
        self._config = config
        self._pool: AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]] = (
            AsyncConnectionPool(
                config.dsn,
                open=False,
                min_size=1,
                max_size=config.max_size,
                timeout=config.acquire_timeout,
                max_waiting=config.max_waiting,
                name="nova-metadata",
                reconnect_timeout=config.acquire_timeout,
                check=AsyncConnectionPool.check_connection,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": None,
                    "row_factory": dict_row,
                    "connect_timeout": max(1, int(config.acquire_timeout)),
                    "options": (
                        f"-c statement_timeout={config.statement_timeout_ms} "
                        f"-c lock_timeout={config.lock_timeout_ms} "
                        "-c idle_in_transaction_session_timeout="
                        f"{config.idle_transaction_timeout_ms} "
                        "-c synchronous_commit=on -c timezone=UTC"
                    ),
                },
            )
        )
        self._ready = False
        self._schema_version: int | None = None
        self._metadata_epoch: str | None = None

    async def open(self, *, schema_version: int, metadata_epoch: str) -> None:
        """Admit this process only after an explicit migrator published its epoch."""
        if schema_version < 1 or not metadata_epoch:
            raise MetadataSchemaMismatch()
        self._ready = False
        try:
            migrations = load_migrations()
            if schema_version != len(migrations):
                raise MetadataSchemaMismatch()
            await self._pool.open(wait=True, timeout=self._config.acquire_timeout)
            # Transaction poolers can ignore startup timeout options. Bound the
            # entire validation, including waits on the migration ledger.
            async with (
                asyncio.timeout(self._config.acquire_timeout),
                self._pool.connection() as conn,
            ):
                if conn.info.server_version // 10000 != 17:
                    raise MetadataSchemaMismatch()
                if not await logging_is_private(conn):
                    raise MetadataSchemaMismatch()
                cursor = await conn.execute(
                    "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolreplication "
                    "FROM pg_catalog.pg_roles WHERE rolname = current_user"
                )
                privileges = await cursor.fetchone()
                if not privileges or any(privileges.values()):
                    raise MetadataSchemaMismatch()
                cursor = await conn.execute(
                    "SELECT has_database_privilege(current_user, current_database(), 'CREATE') "
                    "OR EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE "
                    "(r.rolsuper OR r.rolbypassrls OR r.rolcreatedb OR r.rolcreaterole "
                    "OR r.rolreplication OR r.rolname IN "
                    "('pg_read_all_data', 'pg_write_all_data', 'pg_read_server_files', "
                    "'pg_write_server_files', 'pg_execute_server_program', 'pg_signal_backend')) "
                    "AND pg_has_role(current_user, r.oid, 'MEMBER')) AS unsafe_membership"
                )
                membership = await cursor.fetchone()
                if not membership or membership["unsafe_membership"]:
                    raise MetadataSchemaMismatch()
                cursor = await conn.execute(
                    "SELECT pg_has_role(current_user, n.nspowner, 'MEMBER') "
                    "OR has_schema_privilege(current_user, n.oid, 'CREATE') AS owns_schema "
                    "FROM pg_catalog.pg_namespace n WHERE n.nspname = 'nova_meta'"
                )
                owner = await cursor.fetchone()
                if not owner or owner["owns_schema"]:
                    raise MetadataSchemaMismatch()
                cursor = await conn.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
                    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'nova_meta' "
                    "AND pg_has_role(current_user, c.relowner, 'MEMBER')) "
                    "OR EXISTS(SELECT 1 FROM pg_catalog.pg_database "
                    "WHERE datname = current_database() "
                    "AND pg_has_role(current_user, datdba, 'MEMBER')) AS owns_objects"
                )
                ownership = await cursor.fetchone()
                if not ownership or ownership["owns_objects"]:
                    raise MetadataSchemaMismatch()
                cursor = await conn.execute(
                    "SELECT version, name, checksum FROM nova_meta.schema_migrations "
                    "ORDER BY version"
                )
                applied = await cursor.fetchall()
                if verify_applied_migrations(migrations, applied) != schema_version:
                    raise MetadataSchemaMismatch()
                cursor = await conn.execute(
                    "SELECT schema_version, metadata_epoch FROM nova_meta.deployment_state "
                    "WHERE singleton = TRUE"
                )
                state = await cursor.fetchone()
                if (
                    not state
                    or state["schema_version"] != schema_version
                    or str(state["metadata_epoch"]) != metadata_epoch
                ):
                    raise MetadataSchemaMismatch()
            self._schema_version = schema_version
            self._metadata_epoch = metadata_epoch
            self._ready = True
        except SchemaManifestError:
            await self._pool.close()
            raise MetadataSchemaMismatch() from None
        except (TimeoutError, psycopg.errors.QueryCanceled, psycopg.errors.LockNotAvailable):
            await self._pool.close()
            raise MetadataTimeout() from None
        except (psycopg.Error, PoolTimeout):
            await self._pool.close()
            raise MetadataUnavailable() from None
        except BaseException:
            await self._pool.close()
            raise

    async def close(self) -> None:
        self._ready = False
        await self._pool.close()

    @asynccontextmanager
    async def transaction(
        self,
        actor: SecurityContext,
        *,
        read_only: bool = False,
        request_id: str = "",
    ) -> AsyncIterator[psycopg.AsyncConnection[dict[str, Any]]]:
        if not isinstance(actor, SecurityContext):
            raise ValueError("An explicit metadata actor is required")
        if not self._ready:
            raise MetadataUnavailable()
        try:
            async with self._pool.connection() as conn, conn.transaction():
                if read_only:
                    await conn.execute("SET TRANSACTION READ ONLY")
                # A transaction pooler can select a different backend after
                # readiness. Check before sending any actor or domain values.
                if not await logging_is_private(conn):
                    raise MetadataSchemaMismatch()
                await conn.execute(
                    "SELECT set_config('nova.principal', %s, true), "
                    "set_config('nova.active_role', %s, true), "
                    "set_config('nova.security_context_version', %s, true), "
                    "set_config('nova.principal_type', %s, true), "
                    "set_config('nova.request_id', %s, true), "
                    "set_config('nova.service_identity', current_user, true), "
                    "set_config('synchronous_commit', 'on', true), "
                    "set_config('TimeZone', 'UTC', true), "
                    "set_config('statement_timeout', %s, true), "
                    "set_config('lock_timeout', %s, true), "
                    "set_config('idle_in_transaction_session_timeout', %s, true)",
                    (
                        actor.principal,
                        actor.active_role,
                        str(actor.security_context_version),
                        actor.principal_type,
                        request_id,
                        str(self._config.statement_timeout_ms),
                        str(self._config.lock_timeout_ms),
                        str(self._config.idle_transaction_timeout_ms),
                    ),
                )
                await conn.execute(
                    "SELECT pg_advisory_xact_lock_shared(%s)", (METADATA_DEPLOYMENT_LOCK,)
                )
                cursor = await conn.execute(
                    "SELECT schema_version, metadata_epoch, maintenance "
                    "FROM nova_meta.deployment_state WHERE singleton = TRUE"
                )
                state = await cursor.fetchone()
                if (
                    not state
                    or state["schema_version"] != self._schema_version
                    or str(state["metadata_epoch"]) != self._metadata_epoch
                ):
                    raise MetadataSchemaMismatch()
                if not read_only and state["maintenance"]:
                    raise MetadataReadOnlyMaintenance()
                yield conn
        except (
            psycopg.errors.UniqueViolation,
            psycopg.errors.SerializationFailure,
            psycopg.errors.DeadlockDetected,
        ) as exc:
            _log_database_failure("conflict", exc)
            raise MetadataConflict() from None
        except (PoolTimeout, psycopg.errors.QueryCanceled, psycopg.errors.LockNotAvailable) as exc:
            _log_database_failure("timeout", exc)
            raise MetadataTimeout() from None
        except (TooManyRequests, PoolClosed) as exc:
            _log_database_failure("unavailable", exc)
            raise MetadataUnavailable() from None
        except psycopg.OperationalError as exc:
            _log_database_failure("outcome_unknown", exc)
            # A connection lost during COMMIT cannot be retried without a receipt.
            raise MetadataOutcomeUnknown() from None
        except psycopg.errors.InsufficientPrivilege as exc:
            _log_database_failure("permission", exc)
            raise MetadataPermissionDenied() from None
        except psycopg.errors.ReadOnlySqlTransaction as exc:
            _log_database_failure("read_only", exc)
            raise MetadataReadOnlyMaintenance() from None
        except psycopg.IntegrityError as exc:
            _log_database_failure("integrity", exc)
            raise MetadataIntegrityViolation() from None
        except psycopg.Error as exc:
            _log_database_failure("sql_error", exc)
            # Driver diagnostics can contain bind values; expose only the category.
            raise MetadataOperationFailed() from None
