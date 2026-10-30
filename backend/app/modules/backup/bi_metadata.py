"""Superset's metadata database in Nova backups (docs/arch-17-superset.md, Phase 5).

Superset keeps its charts, dashboards, datasets, alerts and reports in its own
PostgreSQL database (D1). A snapshot of ``NOVA_SYSTEM`` covers Nova's
metadata only, so the backup service pairs it with a ``pg_dump`` of Superset's
database stored under the same repository and label in Nova's managed object
storage (the workspace storage connection), and restoring that snapshot
restores the dump. Studio artifacts in ``NOVA_SYSTEM`` point at Superset
objects by id, so the two only make sense together.

The database password comes from the environment and is handed to the
PostgreSQL client through its environment, never its arguments, a log or a
response. Restoring replaces Superset's metadata in one transaction; restart
the Superset services afterwards so no process keeps cached objects.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from app.core.config import load_nova_app_config, settings

logger = logging.getLogger(__name__)

#: The database whose snapshots carry Superset's metadata.
NOVA_METADATA_DATABASE = "NOVA_SYSTEM"
_TIMEOUT_SECONDS = 900


class BiMetadataError(RuntimeError):
    """Superset's metadata could not be dumped or restored (message is safe to show)."""


@dataclass(frozen=True)
class BiMetadataResult:
    status: str  # saved | restored | absent | failed
    message: str
    size_bytes: int | None = None

    def as_dict(self) -> dict:
        return {"status": self.status, "message": self.message, "size_bytes": self.size_bytes}


def pairs_with(database: str) -> bool:
    return database.upper() == NOVA_METADATA_DATABASE


def _key(repository: str, label: str) -> str:
    return f"backups/bi/{repository}/{label}.dump"


def _store():
    from app.modules.ml_engine.artifacts.store import ObjectArtifactStore

    return ObjectArtifactStore(load_nova_app_config().workspace.storage_connection)


def _client_env() -> dict[str, str]:
    if not settings.SUPERSET_DB_PASSWORD:
        raise BiMetadataError("BI metadata backup is not configured (SUPERSET_DB_PASSWORD).")
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PGHOST": settings.SUPERSET_DB_HOST,
        "PGPORT": str(settings.SUPERSET_DB_PORT),
        "PGUSER": settings.SUPERSET_DB_USER,
        "PGDATABASE": settings.SUPERSET_DB_NAME,
        "PGPASSWORD": settings.SUPERSET_DB_PASSWORD,
        "PGCONNECT_TIMEOUT": "10",
    }


async def _run(arguments: list[str], stdin: bytes | None = None) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_client_env(),
        )
    except FileNotFoundError as exc:
        raise BiMetadataError(f"{arguments[0]} is not installed in this image.") from exc
    try:
        output, errors = await asyncio.wait_for(process.communicate(stdin), _TIMEOUT_SECONDS)
    except TimeoutError as exc:
        process.kill()
        raise BiMetadataError(f"{arguments[0]} did not finish in time.") from exc
    if process.returncode != 0:
        # The client's messages name objects, never the password (it is in the env).
        detail = errors.decode(errors="replace").strip().splitlines()[-1:] or ["unknown error"]
        logger.warning("%s failed: %s", arguments[0], detail[0])
        raise BiMetadataError(f"{arguments[0]} failed: {detail[0][:300]}")
    return output


async def dump(repository: str, label: str) -> BiMetadataResult:
    """Dump Superset's metadata next to snapshot ``label`` of ``repository``."""
    try:
        payload = await _run(["pg_dump", "--format=custom", "--no-owner", "--no-privileges"])
        store = _store()
        await asyncio.to_thread(store.put, _key(repository, label), payload)
    except BiMetadataError as exc:
        return BiMetadataResult("failed", str(exc))
    except Exception as exc:  # noqa: BLE001 - storage errors are reported, not raised
        logger.warning("BI metadata dump was not stored: %s", type(exc).__name__)
        return BiMetadataResult("failed", "BI metadata could not be stored.")
    return BiMetadataResult("saved", "BI metadata saved with this snapshot.", len(payload))


async def restore(repository: str, label: str) -> BiMetadataResult:
    """Restore the Superset metadata saved with snapshot ``label``, if any."""
    store = _store()
    uri = store.uri_for_key(_key(repository, label))
    try:
        if not await asyncio.to_thread(store.exists, uri):
            return BiMetadataResult("absent", "This snapshot has no BI metadata.")
        payload = await asyncio.to_thread(store.get, uri)
        await _run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                "--single-transaction",
                "--exit-on-error",
                f"--dbname={settings.SUPERSET_DB_NAME}",
            ],
            stdin=payload,
        )
    except BiMetadataError as exc:
        return BiMetadataResult("failed", str(exc))
    except Exception as exc:  # noqa: BLE001 - storage errors are reported, not raised
        logger.warning("BI metadata was not restored: %s", type(exc).__name__)
        return BiMetadataResult("failed", "BI metadata could not be read.")
    return BiMetadataResult(
        "restored",
        "BI metadata restored. Restart the Superset services to drop cached objects.",
        len(payload),
    )
