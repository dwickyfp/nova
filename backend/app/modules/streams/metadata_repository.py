"""Nova Stream control metadata in the caller's PostgreSQL transaction.

The prepared replacement for the ``NOVA_SYSTEM`` control paths of
:class:`ClaimRepository`, :class:`StreamRepository`,
:class:`StarRocksConsumptionJournal` and the control half of
:class:`SourceRegistry`, switched in at the all-domain cutover.

What moves here is Nova's own control state: namespace generations and their
operation facts, stream definitions, the source registry and commit index,
arbitration claims, and consumption operations and receipts. What stays in the
engine: the source-row journal and lineage tables (they commit with the
analytical data), and engine observations (table identity, partition versions,
columns, ``TargetCommitVerifier`` load/transaction evidence). No PG
transaction here claims to commit engine data.

Every write is an idempotent insert of an immutable fact followed by an exact
readback, as in the source; the database refuses a conflicting fact for an
existing identity, a receipt that overlaps or breaks the offset chain, and a
publication or decision state recorded without the winning claim. Source
status is the one updated row, under a revision CAS.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext
from app.modules.streams.schemas import StreamError, operation_id

NAMESPACE_FIELDS = (
    "catalog_name", "database_name", "schema_name", "name", "generation", "stream_id",
    "source_database", "source_name", "source_id", "owner_role", "cursor_epoch",
    "cursor_sequence", "status",
)
SCOPE_FIELDS = NAMESPACE_FIELDS[:4]
CLAIM_FIELDS = ("resource_id", "epoch", "generation", "attempt_id", "operation_digest")
SOURCE_FIELDS = (
    "source_id", "epoch", "database_name", "table_name", "table_id", "key_columns",
    "columns_json", "schema_fingerprint", "journal_table", "lineage_table",
)
COMMIT_FIELDS = (
    "commit_id", "version_before", "version_after", "row_count", "principal", "active_role",
)
OPERATION_FIELDS = (
    "consume_id", "stream_id", "generation", "source_id", "epoch", "sequence_from",
    "sequence_to", "schema_version", "operation_digest", "target_database", "target_object",
    "engine_label", "principal", "target_load_id", "target_transaction_id", "active_role",
    "security_context_version", "dispatch_attempt", "submission_digest", "cancellation_digest",
)
#: The operation facts every consumption event repeats exactly.
EVENT_FACTS = (
    "consume_id", "stream_id", "epoch", "generation", "sequence_from", "sequence_to",
    "operation_digest", "target_database", "target_object", "engine_label", "principal",
)
STATES = (
    "PREPARED", "TARGET_SUBMITTED", "TARGET_COMMITTED", "OFFSET_COMMITTED", "FAILED",
    "CANCELLED", "VERIFICATION_REQUIRED",
)
TERMINAL_STATES = ("TARGET_COMMITTED", "OFFSET_COMMITTED", "FAILED")


def claim_key(resource_id: str, epoch: int, generation: int) -> str:
    """The same key :class:`Claim` derives; the database checks it too."""
    return hashlib.sha256(f"{resource_id}:{epoch}:{generation}".encode()).hexdigest()


def event_id(consume_id: str, state: str) -> str:
    """One event per consumption and state, as the source journal derives it."""
    return operation_id(consume_id, state)


def _pick(values: Mapping[str, Any], fields: tuple[str, ...]) -> tuple[Any, ...]:
    missing = [field for field in fields if field not in values]
    if missing:
        raise ValueError(f"Missing stream metadata fields: {missing}")
    return tuple(values[field] for field in fields)


def _reason(reason: str | None) -> str | None:
    """At most 500 bytes of UTF-8, cut on a character boundary."""
    encoded = (reason or "").encode()[:500]
    return encoded.decode(errors="ignore") or None


def _blocked(message: str) -> StreamError:
    return StreamError("STREAM_CONSUMPTION_BLOCKED", message)


class StreamMetadataRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Stream metadata requires an active metadata transaction")
        cursor = await self.connection.execute(
            "SELECT current_setting('nova.principal',true)=%s "
            "AND current_setting('nova.active_role',true)=%s "
            "AND current_setting('nova.security_context_version',true)=%s "
            "AND current_setting('nova.service_identity',true)=current_user AS valid",
            (self.actor.principal, self.actor.active_role,
             str(self.actor.security_context_version)),
        )
        row = await cursor.fetchone()
        if not row or row["valid"] is not True:
            raise MetadataPermissionDenied()

    async def _one(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        cursor = await self.connection.execute(query, params)
        return await cursor.fetchone()

    async def _all(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = await self.connection.execute(query, params)
        return list(await cursor.fetchall())

    async def _insert_fact(
        self, table: str, fields: tuple[str, ...], values: tuple[Any, ...],
        identity: str, key: tuple[Any, ...], compare: tuple[str, ...],
        expected: tuple[Any, ...],
    ) -> dict[str, Any]:
        """Insert an immutable fact once and accept only an exact readback."""
        await self.connection.execute(
            f"INSERT INTO nova_meta.{table} ({','.join(fields)}) VALUES "
            f"({','.join(['%s'] * len(fields))}) ON CONFLICT DO NOTHING",
            values,
        )
        rows = await self._all(f"SELECT * FROM nova_meta.{table} WHERE {identity} LIMIT 2", key)
        if len(rows) != 1 or tuple(rows[0][field] for field in compare) != expected:
            raise MetadataConflict()
        return rows[0]

    # ── Claims ────────────────────────────────────────────────────────────

    async def acquire_claim(self, claim: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Insert the claim if its key is free; return the durable winner.

        ``True`` only when this call inserted the winner. A loser, or a retry
        whose earlier response was lost, gets the winner with ``False``: a
        recovered winner is evidence for verification, never permission to
        submit.
        """
        await self._bound()
        values = _pick(claim, CLAIM_FIELDS)
        key = claim_key(claim["resource_id"], claim["epoch"], claim["generation"])
        inserted = await self._one(
            "INSERT INTO nova_meta.audit_stream_claims (claim_key," + ",".join(CLAIM_FIELDS)
            + ") VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (claim_key) DO NOTHING "
            "RETURNING claim_key", (key, *values),
        )
        winner = await self.inspect_claim(key)
        if winner is None:
            raise MetadataConflict()
        return winner, inserted is not None and tuple(winner[f] for f in CLAIM_FIELDS) == values

    async def inspect_claim(self, key: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.audit_stream_claims WHERE claim_key=%s", (key,),
        )

    # ── Namespace generations ────────────────────────────────────────────

    async def prepare_namespace_operation(
        self, digest: str, record: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist the immutable facts a namespace claim will publish."""
        await self._bound()
        values = _pick(record, NAMESPACE_FIELDS)
        return await self._insert_fact(
            "audit_stream_namespace_operations", ("operation_digest", *NAMESPACE_FIELDS),
            (digest, *values), "operation_digest=%s", (digest,), NAMESPACE_FIELDS, values,
        )

    async def namespace_operation(self, digest: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.audit_stream_namespace_operations WHERE operation_digest=%s",
            (digest,),
        )

    async def publish_generation(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Publish the claimed generation; an exact retry returns the same row."""
        await self._bound()
        values = _pick(record, NAMESPACE_FIELDS)
        return await self._insert_fact(
            "config_streams", NAMESPACE_FIELDS, values,
            "catalog_name=%s AND database_name=%s AND schema_name=%s AND name=%s "
            "AND generation=%s", (*values[:5],), NAMESPACE_FIELDS, values,
        )

    async def current(self, scope: tuple[str, str, str, str]) -> dict[str, Any] | None:
        """The newest generation of a stream name, or ``None``."""
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_streams WHERE catalog_name=%s AND database_name=%s "
            "AND schema_name=%s AND name=%s ORDER BY generation DESC LIMIT 1", scope,
        )

    async def current_generations(
        self, *, source_id: str | None = None, statuses: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """The newest generation of every name, optionally of one source/status."""
        await self._bound()
        rows = await self._all(
            "SELECT DISTINCT ON (catalog_name,database_name,schema_name,name) * "
            "FROM nova_meta.config_streams "
            "ORDER BY catalog_name,database_name,schema_name,name,generation DESC",
        )
        return [
            row for row in rows
            if (source_id is None or row["source_id"] == source_id)
            and (statuses is None or row["status"] in statuses)
        ]

    # ── Definitions ──────────────────────────────────────────────────────

    async def create_definition(
        self, stream_id: str, mode: str, source_id: str, *,
        show_initial_rows: bool = False, initial_commit_seq: int | None = None,
    ) -> dict[str, Any]:
        await self._bound()
        values = (stream_id, mode, source_id, show_initial_rows, initial_commit_seq)
        fields = ("stream_id", "mode", "source_id", "show_initial_rows", "initial_commit_seq")
        return await self._insert_fact(
            "config_stream_definitions", fields, values, "stream_id=%s", (stream_id,),
            (*fields, "created_by"), (*values, self.actor.principal),
        )

    async def definition(self, stream_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_stream_definitions WHERE stream_id=%s", (stream_id,),
        )

    # ── Source registry ──────────────────────────────────────────────────

    async def register_source(self, source: Mapping[str, Any]) -> dict[str, Any]:
        """A new REGISTERING source; a live one for the table conflicts."""
        await self._bound()
        values = list(_pick(source, SOURCE_FIELDS))
        values[5], values[6] = Jsonb(list(values[5])), Jsonb(list(values[6]))
        row = await self._one(
            "INSERT INTO nova_meta.config_stream_sources (" + ",".join(SOURCE_FIELDS)
            + ",status) VALUES (" + ",".join(["%s"] * len(SOURCE_FIELDS))
            + ",'REGISTERING') RETURNING *", tuple(values),
        )
        assert row is not None
        return row

    async def get_source(self, source_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_stream_sources WHERE source_id=%s", (source_id,),
        )

    async def active_source_for_table(self, database: str, table: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_stream_sources WHERE database_name=%s "
            "AND table_name=%s AND status<>'DROPPED' ORDER BY epoch DESC,source_id LIMIT 1",
            (database, table),
        )

    async def list_sources(self, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_stream_sources WHERE status=ANY(%s) "
            "ORDER BY database_name,table_name,epoch", (list(statuses),),
        )

    async def set_source_status(
        self, source_id: str, status: str, reason: str | None, *, expected_revision: int,
    ) -> dict[str, Any]:
        """Move a source's status only if it is still at ``expected_revision``."""
        await self._bound()
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("A positive expected revision is required")
        row = await self._one(
            "UPDATE nova_meta.config_stream_sources SET status=%s,status_reason=%s,"
            "metadata_revision=metadata_revision+1 WHERE source_id=%s AND metadata_revision=%s "
            "RETURNING *",
            (status, _reason(reason), source_id, expected_revision),
        )
        if row is None:
            raise MetadataConflict()
        return row

    # ── Commit index ─────────────────────────────────────────────────────

    async def record_commit(
        self, source_id: str, sequence: int, commit: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Index one captured commit; a different fact for the sequence conflicts."""
        await self._bound()
        values = _pick(commit, COMMIT_FIELDS)
        return await self._insert_fact(
            "audit_stream_source_commits", ("source_id", "commit_seq", *COMMIT_FIELDS),
            (source_id, sequence, *values), "source_id=%s AND commit_seq=%s",
            (source_id, sequence), COMMIT_FIELDS, values,
        )

    async def head_commit(self, source_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.audit_stream_source_commits WHERE source_id=%s "
            "ORDER BY commit_seq DESC LIMIT 1", (source_id,),
        )

    async def commits_between(
        self, source_id: str, after: int, through: int,
    ) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.audit_stream_source_commits WHERE source_id=%s "
            "AND commit_seq>%s AND commit_seq<=%s ORDER BY commit_seq",
            (source_id, after, through),
        )

    # ── Consumption operations and receipts ──────────────────────────────

    async def prepare_consumption(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        """Persist the immutable operation facts, then its PREPARED event."""
        await self._bound()
        values = _pick(operation, OPERATION_FIELDS)
        row = await self._insert_fact(
            "audit_stream_consumption_operations", OPERATION_FIELDS, values,
            "consume_id=%s", (operation["consume_id"],), OPERATION_FIELDS, values,
        )
        await self.record_event(row, "PREPARED")
        return row

    async def operation(self, consume_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.audit_stream_consumption_operations WHERE consume_id=%s",
            (consume_id,),
        )

    async def winning_operation(self, decision_digest: str) -> dict[str, Any] | None:
        """The one operation a decision digest names; ambiguity blocks."""
        await self._bound()
        rows = await self._all(
            "SELECT * FROM nova_meta.audit_stream_consumption_operations "
            "WHERE submission_digest=%s OR cancellation_digest=%s LIMIT 2",
            (decision_digest, decision_digest),
        )
        if len(rows) > 1:
            raise _blocked("Durable operation facts are inconsistent")
        return rows[0] if rows else None

    async def record_event(
        self, operation: Mapping[str, Any], state: str,
        receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one transition; an exact retry is accepted, a different one is not."""
        await self._bound()
        if state not in STATES:
            raise ValueError("Unknown consumption state")
        facts = _pick(operation, EVENT_FACTS)
        identity = event_id(operation["consume_id"], state)
        fields = ("event_id", *EVENT_FACTS, "state", "receipt")
        row = await self._insert_fact(
            "audit_stream_consumptions", fields,
            (identity, *facts, state, None if receipt is None else Jsonb(dict(receipt))),
            "event_id=%s", (identity,), (*EVENT_FACTS, "state"), (*facts, state),
        )
        if state in TERMINAL_STATES and row["receipt"] != dict(receipt or {}):
            raise MetadataConflict()
        return row

    async def terminal_receipt(self, consume_id: str) -> dict[str, Any] | None:
        """The one verified terminal receipt of a consumption, or ``None``."""
        await self._bound()
        rows = await self._all(
            "SELECT state,receipt FROM nova_meta.audit_stream_consumptions WHERE consume_id=%s "
            "AND state=ANY(%s) LIMIT 4", (consume_id, list(TERMINAL_STATES)),
        )
        if not rows:
            return None
        if len(rows) > 3 or any(row["receipt"] != rows[0]["receipt"] for row in rows[1:]):
            raise _blocked("Conflicting terminal receipts")
        return rows[0]["receipt"]

    async def committed_cursor(self, stream_id: str, epoch: int, creation: int) -> int:
        """The committed offset, proven by one unbroken chain of offset receipts.

        Starting at the creation sequence, each OFFSET_COMMITTED receipt must
        start where the previous one ended. A gap, an overlap or two receipts
        from the same offset blocks the stream instead of taking a maximum.
        """
        await self._bound()
        rows = await self._all(
            "SELECT sequence_from,sequence_to FROM nova_meta.audit_stream_consumptions "
            "WHERE stream_id=%s AND epoch=%s AND state='OFFSET_COMMITTED'", (stream_id, epoch),
        )
        by_start: dict[int, int] = {}
        for row in rows:
            start, end = int(row["sequence_from"]), int(row["sequence_to"])
            if end < start or start in by_start:
                raise _blocked("Offset receipts conflict")
            if end <= creation and start < creation:
                continue  # history before the creation cursor
            if start < creation:
                raise _blocked("Offset receipts overlap")
            by_start[start] = end
        cursor = creation
        while cursor in by_start:
            cursor = by_start.pop(cursor)
        if by_start:
            raise _blocked("Offset receipts are not one unbroken chain")
        return cursor

    async def failed_attempts(self, stream_id: str, generation: int, start: int) -> int:
        """Proven failures from ``start``: the next attempt's arbitration slot."""
        await self._bound()
        row = await self._one(
            "SELECT count(DISTINCT consume_id) AS failures "
            "FROM nova_meta.audit_stream_consumptions WHERE stream_id=%s AND generation=%s "
            "AND sequence_from=%s AND state='FAILED'",
            (stream_id, generation, start),
        )
        return int(row["failures"]) if row else 0

    async def recent_events(self, stream_id: str, limit: int) -> list[dict[str, Any]]:
        """The newest consumption events of a stream, without receipts."""
        await self._bound()
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("A limit between 1 and 1000 is required")
        return await self._all(
            "SELECT consume_id,state,sequence_from,sequence_to,target_database,target_object,"
            "principal,created_at FROM nova_meta.audit_stream_consumptions WHERE stream_id=%s "
            "ORDER BY created_at DESC NULLS LAST,event_id LIMIT %s", (stream_id, limit),
        )
