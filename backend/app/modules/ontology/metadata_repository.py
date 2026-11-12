"""Business ontology revisions in the caller's PostgreSQL transaction.

The prepared replacement for the ``NOVA_SYSTEM`` paths of
:class:`~app.modules.ontology.repository.RevisionRepository` (ontology kinds
only), switched in at the all-domain cutover. It keeps that repository's
model and method shapes:

* every change is a new immutable ``(id, revision)`` row and the newest
  revision of an id is current;
* a save names the revision it read; a concurrent save of the same record
  ends in one success and one :class:`MetadataConflict`, enforced by the
  database rather than a Redis lease;
* an id whose newest revision has two rows (imported from the source, where a
  lost lease allowed it) is reported, never resolved by picking one.

What changes: "current" reads join ``config_ontology_heads`` instead of
windowing over every row; a run's evaluation results are read through an
index on ``run_id``; and a ``save_many`` batch is one transaction, so a
conflict found mid-batch leaves nothing written. Stewardship, release and
alias rules that can be decided from stored rows are also checked by the
migration's row guard; the service still decides domain visibility through
``ontology/access.py``.
"""

from __future__ import annotations

from typing import Any, TypeVar

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from app.core.exceptions import NovaException
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext
from app.modules.intelligence.contracts import fingerprint, utc_now
from app.modules.ontology.contracts import OntologyRecord
from app.modules.ontology.schema import ONTOLOGY_TABLES

R = TypeVar("R", bound=OntologyRecord)

#: Upper bound of records of one kind read in a single request, as in the source.
MAX_CURRENT = 20000
#: Rows per INSERT when a batch is written.
BATCH_ROWS = 500
#: Ids per lookup in ``latest_many``.
LOOKUP_IDS = 500
_UNCHANGED = {"revision", "actor", "created_at", "updated_at"}
TABLES = {kind: name.lower() for kind, name in ONTOLOGY_TABLES.items()}


class OntologyReadBoundExceeded(ValueError):
    """More records than one request may read; the caller answers 422."""


class MetadataBranches(MetadataConflict):
    """The newest revision of a record has more than one row."""

    def __init__(self) -> None:
        NovaException.__init__(
            self, "Concurrent revisions require reconciliation", status_code=409,
        )


def _table(kind: str) -> str:
    try:
        return "nova_meta." + TABLES[kind]
    except KeyError:
        raise ValueError("Unknown ontology kind") from None


def _branched(rows: list[dict[str, Any]]) -> None:
    if any(row["branches"] != 1 for row in rows):
        raise MetadataBranches()


class OntologyMetadataRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Ontology metadata requires an active metadata transaction")
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

    async def _rows(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        cursor = await self.connection.execute(query, params)
        return list(await cursor.fetchall())

    # ── Reads ─────────────────────────────────────────────────────────────

    async def latest(self, kind: str, record_id: str, model: type[R]) -> R | None:
        await self._bound()
        rows = await self._rows(
            f"SELECT r.payload,h.branches FROM nova_meta.config_ontology_heads h "
            f"JOIN {_table(kind)} r ON r.id=h.id AND r.revision=h.revision "
            "WHERE h.kind=%s AND h.id=%s", (TABLES[kind], record_id),
        )
        _branched(rows)
        return model.model_validate(rows[0]["payload"]) if rows else None

    async def revision(
        self, kind: str, record_id: str, revision: int, model: type[R],
    ) -> R | None:
        """One pinned revision, for example a release member."""
        await self._bound()
        rows = await self._rows(
            f"SELECT payload FROM {_table(kind)} WHERE id=%s AND revision=%s LIMIT 2",
            (record_id, revision),
        )
        if len(rows) > 1:
            raise MetadataBranches()
        return model.model_validate(rows[0]["payload"]) if rows else None

    async def latest_many(
        self, kind: str, record_ids: list[str], model: type[R],
    ) -> dict[str, R]:
        """The newest revision of each given record, in one read per 500 ids."""
        await self._bound()
        ids = sorted(set(record_ids))
        records: dict[str, R] = {}
        for start in range(0, len(ids), LOOKUP_IDS):
            rows = await self._rows(
                f"SELECT r.payload,h.branches FROM nova_meta.config_ontology_heads h "
                f"JOIN {_table(kind)} r ON r.id=h.id AND r.revision=h.revision "
                "WHERE h.kind=%s AND h.id=ANY(%s)", (TABLES[kind], ids[start:start + LOOKUP_IDS]),
            )
            _branched(rows)
            for row in rows:
                record = model.model_validate(row["payload"])
                records[record.id] = record
        return records

    async def history(
        self, kind: str, record_id: str, model: type[R], *, limit: int = 100,
    ) -> list[R]:
        await self._bound()
        rows = await self._rows(
            f"SELECT payload FROM {_table(kind)} WHERE id=%s "
            "ORDER BY revision DESC,operation_id LIMIT %s", (record_id, limit),
        )
        return [model.model_validate(row["payload"]) for row in rows]

    async def current(self, kind: str, model: type[R]) -> list[R]:
        """The newest revision of every record of one kind."""
        await self._bound()
        rows = await self._rows(
            f"SELECT r.payload,h.branches FROM nova_meta.config_ontology_heads h "
            f"JOIN {_table(kind)} r ON r.id=h.id AND r.revision=h.revision "
            "WHERE h.kind=%s ORDER BY h.id,r.operation_id LIMIT %s",
            (TABLES[kind], MAX_CURRENT + 1),
        )
        if len(rows) > MAX_CURRENT:
            raise OntologyReadBoundExceeded("The ontology exceeds its read bound")
        _branched(rows)
        return [model.model_validate(row["payload"]) for row in rows]

    async def current_matching(
        self, kind: str, model: type[R], field: str, value: str, *, limit: int = 200,
    ) -> list[R]:
        """The newest revision of every record whose top-level ``field`` equals ``value``."""
        await self._bound()
        if not field.isidentifier():
            raise ValueError("Unsupported field")
        if kind == "eval_results" and field == "run_id":
            # The stored run_id column is indexed (config_ontology_eval_results_run).
            query = (
                "SELECT r.payload,h.branches FROM nova_meta.config_ontology_eval_results r "
                "JOIN nova_meta.config_ontology_heads h ON h.kind='config_ontology_eval_results' "
                "AND h.id=r.id AND h.revision=r.revision "
                "WHERE r.run_id=%s ORDER BY r.id,r.operation_id LIMIT %s"
            )
            params: tuple[Any, ...] = (value, limit + 1)
        else:
            query = (
                f"SELECT r.payload,h.branches FROM nova_meta.config_ontology_heads h "
                f"JOIN {_table(kind)} r ON r.id=h.id AND r.revision=h.revision "
                "WHERE h.kind=%s AND r.payload->>%s=%s ORDER BY h.id,r.operation_id LIMIT %s"
            )
            params = (TABLES[kind], field, value, limit + 1)
        rows = await self._rows(query, params)
        if len(rows) > limit:
            raise OntologyReadBoundExceeded("Too many matching records")
        _branched(rows)
        return [model.model_validate(row["payload"]) for row in rows]

    async def eval_results_for_run(self, run_id: str, model: type[R], *, limit: int) -> list[R]:
        return await self.current_matching("eval_results", model, "run_id", run_id, limit=limit)

    async def branches(self, kind: str) -> list[dict[str, Any]]:
        """Records whose newest revision has several rows, for reconciliation."""
        await self._bound()
        return await self._rows(
            "SELECT id,revision,branches FROM nova_meta.config_ontology_heads "
            "WHERE kind=%s AND branches>1 ORDER BY id", (TABLES[kind],),
        )

    # ── Writes ────────────────────────────────────────────────────────────

    @staticmethod
    def _next(old: R | None, record: R, expected: int, now: Any) -> R | None:
        """The revision to write, or ``None`` when the content is already current."""
        if type(expected) is not int or expected < 0:
            raise ValueError("A non-negative expected revision is required")
        if old is not None:
            if old.model_dump(exclude=_UNCHANGED) == record.model_dump(exclude=_UNCHANGED):
                return None
            if old.revision != expected:
                raise MetadataConflict()
            record = record.model_copy(update={"created_at": old.created_at})
        elif expected:
            raise MetadataConflict()
        return record.model_copy(update={"revision": expected + 1, "updated_at": now})

    @staticmethod
    def _values(record: OntologyRecord) -> tuple[Any, ...]:
        payload = record.model_dump(mode="json")
        return (record.id, record.revision, fingerprint(payload)[:32], Jsonb(payload))

    async def save(self, kind: str, record: R, *, expected_revision: int = 0) -> R:
        """Append ``record`` as the next revision; unchanged content is a no-op."""
        old = await self.latest(kind, record.id, type(record))
        written = self._next(old, record, expected_revision, utc_now())
        if written is None:
            assert old is not None
            return old
        await self.connection.execute(
            f"INSERT INTO {_table(kind)} (id,revision,operation_id,payload) "
            "VALUES (%s,%s,%s,%s)", self._values(written),
        )
        return written

    async def save_many(self, kind: str, records: list[tuple[R, int]]) -> int:
        """Append many records as their next revisions in this one transaction.

        Unchanged records are skipped; any revision conflict raises before the
        caller commits, so a refused batch writes nothing. Returns how many
        records were written.
        """
        if not records:
            return 0
        model = type(records[0][0])
        latest = await self.latest_many(kind, [record.id for record, _ in records], model)
        now = utc_now()
        rows = [
            self._values(written)
            for record, expected in records
            if (written := self._next(latest.get(record.id), record, expected, now)) is not None
        ]
        for start in range(0, len(rows), BATCH_ROWS):
            chunk = rows[start:start + BATCH_ROWS]
            await self.connection.execute(
                f"INSERT INTO {_table(kind)} (id,revision,operation_id,payload) VALUES "
                + ",".join(["(%s,%s,%s,%s)"] * len(chunk)),
                tuple(value for row in chunk for value in row),
            )
        return len(rows)
