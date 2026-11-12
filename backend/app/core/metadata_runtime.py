"""The one metadata database a process uses after cutover.

Process startup opens a `MetadataDatabase` for its workload login and installs
it here. Services take actor-bound transactions from `metadata_db()`; they never
open their own pool or fall back to `NOVA_SYSTEM` when none is installed.
"""

from __future__ import annotations

from app.core.metadata_db import MetadataDatabase, MetadataUnavailable

_database: MetadataDatabase | None = None


def install_metadata_db(database: MetadataDatabase) -> None:
    global _database
    if _database is not None and _database is not database:
        raise RuntimeError("A metadata database is already installed in this process")
    _database = database


def uninstall_metadata_db(database: MetadataDatabase) -> None:
    global _database
    if _database is database:
        _database = None


def metadata_db() -> MetadataDatabase:
    """Return the installed database; refuse rather than reach stale metadata."""
    if _database is None:
        raise MetadataUnavailable()
    return _database
