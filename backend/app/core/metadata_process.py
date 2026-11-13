"""Open, readiness-check and close the one metadata pool an application process owns.

Each process role connects to `nova_control` with its own workload login, at
the schema revision and epoch this deployment was released against. Startup
fails closed: a missing configuration, a schema or epoch mismatch, an
unpublished epoch or a privileged login stops the process. Only a transient
outage (unreachable server, connection deadline) is retried, within one
bounded startup deadline. Nothing here migrates, publishes an epoch or falls
back to `NOVA_SYSTEM`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import stat
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType

from psycopg.conninfo import make_conninfo
from pydantic import SecretStr, ValidationError

from app.core.config import MetadataRuntimeSettings
from app.core.metadata_bootstrap import WORKLOAD_ROLES
from app.core.metadata_db import (
    MetadataDatabase,
    MetadataPoolConfig,
    MetadataUnavailable,
)
from app.core.metadata_runtime import install_metadata_db, uninstall_metadata_db
from app.observability.metrics import METADATA_READY, METADATA_WRITES_PAUSED

logger = logging.getLogger(__name__)

#: Process role -> the PostgreSQL workload login it connects with. The API's
#: single-process `all` role serves the web surface and uses the web login.
#: The Autopilot worker shares the automation login (see PG_QUERY_AUTOPILOT.md).
PROCESS_WORKLOADS: Mapping[str, str] = MappingProxyType({
    "all": "nova_meta_web",
    "web": "nova_meta_web",
    "query": "nova_meta_query",
    "proxy": "nova_meta_proxy",
    "scheduler": "nova_meta_scheduler",
    "task": "nova_meta_task",
    "ingestion": "nova_meta_ingestion",
    "ml": "nova_meta_ml",
    "notebook": "nova_meta_notebook",
    "automation": "nova_meta_automation",
    "autopilot": "nova_meta_automation",
    "maintenance": "nova_meta_maintenance",
    "outbox": "nova_meta_outbox",
})
MAX_PASSWORD_DOCUMENT = 16384


class MetadataConfigurationError(RuntimeError):
    """The process cannot build a safe metadata connection; never retried."""

    def __init__(self) -> None:
        super().__init__("Metadata runtime configuration is missing or invalid")


def metadata_login(workload: str) -> str:
    login = PROCESS_WORKLOADS.get(workload)
    if login is None or login not in WORKLOAD_ROLES:
        raise MetadataConfigurationError()
    return login


def load_runtime_settings() -> MetadataRuntimeSettings:
    try:
        return MetadataRuntimeSettings()  # type: ignore[call-arg]
    except ValidationError:
        # The validation error can echo field names only; never values.
        raise MetadataConfigurationError() from None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate login")
        result[key] = value
    return result


def _read_private_file(path: Path) -> str:
    """Read the deployment secret file only when no other local user can read it."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("secret file must be a private regular file")
        if info.st_uid != os.geteuid():
            raise ValueError("secret file must belong to this process user")
        value = handle.read(MAX_PASSWORD_DOCUMENT + 1)
    return value


def resolve_password(settings: MetadataRuntimeSettings, login: str) -> SecretStr:
    """Return only this login's password from the environment or the secret file."""
    if (settings.passwords is None) == (settings.passwords_file is None):
        # Neither or both: refuse rather than guess which source is current.
        raise MetadataConfigurationError()
    try:
        if settings.passwords is not None:
            document = settings.passwords.get_secret_value()
        else:
            assert settings.passwords_file is not None
            document = _read_private_file(settings.passwords_file)
        if len(document) > MAX_PASSWORD_DOCUMENT:
            raise ValueError("secret document too large")
        parsed = json.loads(document, object_pairs_hook=_unique_object)
        if not isinstance(parsed, dict) or any(
            name not in WORKLOAD_ROLES or not isinstance(value, str)
            for name, value in parsed.items()
        ):
            # Owner, migrator, backup or readonly credentials never belong here.
            raise ValueError("unexpected login")
        value = parsed.get(login)
        if not isinstance(value, str) or not 24 <= len(value) <= 256 or "\x00" in value:
            raise ValueError("missing or invalid password")
    except (OSError, ValueError, TypeError, AssertionError):
        raise MetadataConfigurationError() from None
    return SecretStr(value)


def pool_config(settings: MetadataRuntimeSettings, workload: str) -> MetadataPoolConfig:
    login = metadata_login(workload)
    host = settings.host.strip()
    if not host or host != settings.host or "," in host or host.startswith(("/", "@")):
        raise MetadataConfigurationError()
    if not settings.sslrootcert.is_file():
        raise MetadataConfigurationError()
    password = resolve_password(settings, login)
    parameters: dict[str, str | int] = {
        "host": host,
        "port": settings.port,
        "dbname": "nova_control",
        "user": login,
        "password": password.get_secret_value(),
        "sslmode": "verify-full",
        "sslrootcert": str(settings.sslrootcert),
        "application_name": f"nova-{workload}",
    }
    if settings.hostaddr:
        parameters["hostaddr"] = settings.hostaddr
    try:
        return MetadataPoolConfig(
            make_conninfo(**parameters),  # type: ignore[arg-type]
            max_size=settings.pool_max_size,
            max_waiting=settings.pool_max_waiting,
            acquire_timeout=settings.acquire_timeout_seconds,
            statement_timeout_ms=settings.statement_timeout_ms,
            lock_timeout_ms=settings.lock_timeout_ms,
            idle_transaction_timeout_ms=settings.idle_transaction_timeout_ms,
        )
    except (ValueError, TypeError):
        raise MetadataConfigurationError() from None


async def open_process_metadata(
    workload: str,
    *,
    settings: MetadataRuntimeSettings | None = None,
    stop: asyncio.Event | None = None,
    factory: Callable[[MetadataPoolConfig], MetadataDatabase] = MetadataDatabase,
    clock: Callable[[], float] = time.monotonic,
) -> MetadataDatabase | None:
    """Admit this process to its published epoch and install the pool.

    Returns ``None`` only when ``stop`` is set while waiting for PostgreSQL.
    Mismatch, permission and configuration errors propagate immediately.
    """
    settings = settings or load_runtime_settings()
    config = pool_config(settings, workload)
    deadline = settings.startup_deadline_seconds
    started, delay = clock(), 1.0
    while True:
        # A psycopg pool cannot reopen after a failed start; build a fresh one.
        database = factory(config)
        try:
            await database.open(
                schema_version=settings.schema_version, metadata_epoch=str(settings.epoch),
            )
        except MetadataUnavailable as error:
            if clock() - started + delay > deadline:
                logger.error("Metadata for %s stayed unavailable at startup", workload)
                raise
            logger.warning(
                "Metadata for %s unavailable (%s); retry in %.0fs",
                workload, type(error).__name__, delay,
            )
        else:
            try:
                install_metadata_db(database)
            except BaseException:
                await database.close()
                raise
            logger.info("Metadata ready for %s at schema %d", workload, settings.schema_version)
            return database
        if stop is None:
            await asyncio.sleep(delay)
        else:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), delay)
            if stop.is_set():
                return None
        delay = min(delay * 2, 30.0)


async def close_process_metadata(database: MetadataDatabase | None, service: str) -> None:
    METADATA_READY.labels(service=service).set(0)
    if database is None:
        return
    uninstall_metadata_db(database)
    await database.close()


async def check_metadata(database: MetadataDatabase, service: str) -> str:
    """One readiness observation: `read_write` or `read_only`; raises when unavailable."""
    try:
        paused = await database.probe()
    except Exception:
        METADATA_READY.labels(service=service).set(0)
        raise
    METADATA_READY.labels(service=service).set(1)
    METADATA_WRITES_PAUSED.labels(service=service).set(1 if paused else 0)
    return "read_only" if paused else "read_write"


async def watch_metadata(
    database: MetadataDatabase, service: str, stop: asyncio.Event, *, interval: float,
) -> None:
    """Keep the readiness gauge current until shutdown; an outage is not a crash."""
    while not stop.is_set():
        try:
            await check_metadata(database, service)
        except Exception as error:
            logger.warning("Metadata readiness for %s failed: %s", service, type(error).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), interval)
