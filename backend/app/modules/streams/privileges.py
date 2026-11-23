"""Privilege proofs for CREATE STREAM, decided by the engine as the caller.

Registering a table as a stream source changes how every writer of that table
behaves: its writes go through capture (row and lock limits), ALTER TABLE and
INSERT OVERWRITE are refused, and loads that bypass Nova block the source. That
is an owner's decision, so the first stream on a table needs ALTER on it, the
privilege the engine already requires for changing a table's definition.
Later streams on a registered source change nothing for its writers and need
only SELECT. A stream is a new name in its database, so it needs CREATE TABLE
there, as a table of that name would.

StarRocks has no privilege-test function, and Ranger decides privileges inside
the engine. Each proof therefore runs, on the caller's own session with the
active role, a statement that the engine authorizes before it fails for a
reason that cannot change anything:

* ALTER: ``ALTER TABLE <source> DROP ROLLUP <random name>``: the engine
  checks ALTER on the table, then reports that no such rollup exists.
* CREATE TABLE: ``CREATE TABLE <db>.<random name> … replication_num = 0``: the
  engine checks CREATE TABLE on the database, then refuses zero replicas.

An access-denied error is a refusal. Any other outcome than the expected
error (another error, a lost connection, an unexpected success) is not proof,
so the stream is refused as unverified. The random names use the stream
journal prefix, which Ranger denies to every user and Nova SQL refuses.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from app.modules.streams.codec import quote_identifier
from app.modules.streams.schemas import StreamError

logger = logging.getLogger(__name__)

#: Prefix of the names that only exist inside a privilege proof.
CHECK_PREFIX = "__nova_stream_check_"

_ACCESS_DENIED = re.compile(r"access denied", re.IGNORECASE)
_NO_ROLLUP = re.compile(r"does not exist", re.IGNORECASE)
_NO_REPLICAS = re.compile(r"replication num should larger than 0", re.IGNORECASE)


def _check_name() -> str:
    return CHECK_PREFIX + uuid.uuid4().hex[:16]


def _unverified(what: str) -> StreamError:
    return StreamError(
        "STREAM_ACCESS_DENIED",
        f"Your {what} privilege could not be verified, so the stream was not created; "
        "try again, or ask an administrator to check the engine",
    )


async def _probe(caller: Any, sql: str) -> str | None:
    if caller.engine is None:
        raise StreamError(
            "STREAM_ACCESS_DENIED",
            "CREATE STREAM needs your own engine session to verify your privileges",
        )
    return await caller.engine(sql)


async def require_owner_privilege(caller: Any, database: str, table: str) -> None:
    """Refuse unless the caller's active role has ALTER on ``database.table``."""
    error = await _probe(
        caller,
        f"ALTER TABLE {quote_identifier(database)}.{quote_identifier(table)} "
        f"DROP ROLLUP {quote_identifier(_check_name())}",
    )
    if error is not None and _ACCESS_DENIED.search(error):
        raise StreamError(
            "STREAM_ACCESS_DENIED",
            f"Only a role with ALTER on {database}.{table} can start capturing its changes: "
            "the first stream on a table changes how every writer of that table works. "
            "Ask its owner to create the first stream; later streams need only SELECT",
        )
    if error is None or not _NO_ROLLUP.search(error):
        logger.warning("stream ALTER proof on %s.%s was inconclusive", database, table)
        raise _unverified(f"ALTER on {database}.{table}")


async def require_create_privilege(caller: Any, database: str) -> None:
    """Refuse unless the caller's active role has CREATE TABLE in ``database``."""
    name = _check_name()
    qualified = f"{quote_identifier(database)}.{quote_identifier(name)}"
    error = await _probe(
        caller,
        f"CREATE TABLE {qualified} (k INT) DUPLICATE KEY(k) DISTRIBUTED BY HASH(k) "
        "BUCKETS 1 PROPERTIES ('replication_num' = '0')",
    )
    if error is not None and _ACCESS_DENIED.search(error):
        raise StreamError(
            "STREAM_ACCESS_DENIED",
            f"Creating a stream in {database} needs the CREATE TABLE privilege there",
        )
    if error is None:
        # The engine accepted zero replicas: remove what it created.
        from app.core.database import db

        await db.execute_system(f"DROP TABLE IF EXISTS {qualified} FORCE")
        logger.warning("stream CREATE proof in %s created a table; it was dropped", database)
        raise _unverified(f"CREATE TABLE in {database}")
    if not _NO_REPLICAS.search(error):
        logger.warning("stream CREATE proof in %s was inconclusive", database)
        raise _unverified(f"CREATE TABLE in {database}")


__all__ = ["CHECK_PREFIX", "require_create_privilege", "require_owner_privilege"]
