"""Revision-checked persistence for shared, deployment-wide records.

The write protocol is the Intelligence one (a metadata lease, a re-read of the
newest revision, and a compare on the expected revision), but records are
shared across the deployment rather than owned by one principal. The ontology
and the business event hub each use it with their own tables.
"""

from __future__ import annotations

import json
from typing import TypeVar

from fastapi import HTTPException

from app.core.database import db
from app.modules.intelligence.contracts import fingerprint, utc_now
from app.modules.intelligence.engine_repository import metadata_lock
from app.modules.ontology.contracts import OntologyRecord
from app.modules.ontology.schema import ONTOLOGY_TABLES

R = TypeVar("R", bound=OntologyRecord)

#: Upper bound of records of one kind read in a single request.
MAX_CURRENT = 20000
_UNCHANGED = {"revision", "actor", "created_at", "updated_at"}


def _decode(value):
    return json.loads(value) if isinstance(value, str) else value


def _check_fork(rows: list) -> None:
    if len(rows) > 1 and rows[0][1] == rows[1][1]:
        raise HTTPException(status_code=409, detail="Concurrent revisions require reconciliation")


class RevisionRepository:
    def __init__(self, tables: dict[str, str], namespace: str) -> None:
        self.tables = tables
        self.namespace = namespace

    def _table(self, kind: str) -> str:
        return "NOVA_SYSTEM." + self.tables[kind]

    async def latest(self, kind: str, record_id: str, model: type[R]) -> R | None:
        result = await db.execute_system(
            f"SELECT payload,revision FROM {self._table(kind)} WHERE id=%s "
            "ORDER BY revision DESC LIMIT 2",
            [record_id],
        )
        _check_fork(result["rows"])
        return model.model_validate(_decode(result["rows"][0][0])) if result["rows"] else None

    async def revision(self, kind: str, record_id: str, revision: int, model: type[R]) -> R | None:
        result = await db.execute_system(
            f"SELECT payload,revision FROM {self._table(kind)} WHERE id=%s AND revision=%s "
            "LIMIT 2",
            [record_id, revision],
        )
        _check_fork(result["rows"])
        return model.model_validate(_decode(result["rows"][0][0])) if result["rows"] else None

    async def latest_many(self, kind: str, record_ids: list[str], model: type[R]) -> dict[str, R]:
        """The newest revision of each given record, in one read."""
        ids = sorted(set(record_ids))[:500]
        if not ids:
            return {}
        marks = ",".join(["%s"] * len(ids))
        result = await db.execute_system(
            "SELECT payload,branches FROM (SELECT id,payload,"
            "COUNT(*) OVER (PARTITION BY id,revision) AS branches,"
            "ROW_NUMBER() OVER (PARTITION BY id ORDER BY revision DESC) AS rn "
            f"FROM {self._table(kind)} WHERE id IN ({marks})) latest WHERE rn=1",
            ids,
        )
        if any(row[1] != 1 for row in result["rows"]):
            raise HTTPException(
                status_code=409, detail="Concurrent revisions require reconciliation"
            )
        records = [model.model_validate(_decode(row[0])) for row in result["rows"]]
        return {record.id: record for record in records}

    async def history(self, kind: str, record_id: str, model: type[R]) -> list[R]:
        result = await db.execute_system(
            f"SELECT payload,revision FROM {self._table(kind)} WHERE id=%s "
            "ORDER BY revision DESC LIMIT 100",
            [record_id],
        )
        return [model.model_validate(_decode(row[0])) for row in result["rows"]]

    async def current(self, kind: str, model: type[R]) -> list[R]:
        """The newest revision of every record of one kind."""
        result = await db.execute_system(
            "SELECT payload,branches FROM (SELECT id,payload,"
            "COUNT(*) OVER (PARTITION BY id,revision) AS branches,"
            "ROW_NUMBER() OVER (PARTITION BY id ORDER BY revision DESC) AS rn "
            f"FROM {self._table(kind)}) latest WHERE rn=1 ORDER BY id LIMIT %s",
            [MAX_CURRENT + 1],
        )
        if len(result["rows"]) > MAX_CURRENT:
            raise HTTPException(status_code=422, detail="The ontology exceeds its read bound")
        if any(row[1] != 1 for row in result["rows"]):
            raise HTTPException(
                status_code=409, detail="Concurrent revisions require reconciliation"
            )
        return [model.model_validate(_decode(row[0])) for row in result["rows"]]

    async def current_matching(
        self, kind: str, model: type[R], field: str, value: str, *, limit: int = 200
    ) -> list[R]:
        """The newest revision of every record whose top-level ``field`` equals ``value``."""
        if not field.isidentifier():
            raise ValueError("Unsupported field")
        result = await db.execute_system(
            "SELECT payload,branches FROM (SELECT id,payload,"
            "COUNT(*) OVER (PARTITION BY id,revision) AS branches,"
            "ROW_NUMBER() OVER (PARTITION BY id ORDER BY revision DESC) AS rn "
            f"FROM {self._table(kind)}) latest WHERE rn=1 "
            f"AND get_json_string(CAST(payload AS VARCHAR),'$.{field}')=%s "
            "ORDER BY id LIMIT %s",
            [value, limit + 1],
        )
        if len(result["rows"]) > limit:
            raise HTTPException(status_code=422, detail="Too many matching records")
        if any(row[1] != 1 for row in result["rows"]):
            raise HTTPException(
                status_code=409, detail="Concurrent revisions require reconciliation"
            )
        return [model.model_validate(_decode(row[0])) for row in result["rows"]]

    async def save(self, kind: str, record: R, *, expected_revision: int = 0) -> R:
        """Append ``record`` as the next revision.

        Saving the content that is already current is a no-op, so an
        interrupted multi-record write can be replayed safely.
        """
        async with metadata_lock(f"{self.namespace}:{kind}:{record.id}") as lock:
            result = await db.execute_system(
                f"SELECT payload,revision FROM {self._table(kind)} WHERE id=%s "
                "ORDER BY revision DESC LIMIT 2",
                [record.id],
            )
            _check_fork(result["rows"])
            if result["rows"]:
                old = type(record).model_validate(_decode(result["rows"][0][0]))
                if old.model_dump(exclude=_UNCHANGED) == record.model_dump(exclude=_UNCHANGED):
                    return old
                if old.revision != expected_revision:
                    raise HTTPException(
                        status_code=409, detail="Record changed; reload before editing"
                    )
                record = record.model_copy(update={"created_at": old.created_at})
            elif expected_revision:
                raise HTTPException(
                    status_code=409, detail="Record no longer matches this revision"
                )
            if not await lock.renew():
                raise HTTPException(status_code=409, detail="Metadata lease expired; retry")
            record = record.model_copy(
                update={"revision": expected_revision + 1, "updated_at": utc_now()}
            )
            await db.execute_system(
                f"INSERT INTO {self._table(kind)} "
                "(id,revision,operation_id,actor,active_role,security_context_version,"
                "payload,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())",
                [
                    record.id,
                    record.revision,
                    fingerprint(record.model_dump(mode="json"))[:32],
                    record.actor.principal,
                    record.actor.active_role,
                    record.actor.security_context_version,
                    record.model_dump_json(),
                ],
            )
            return record


    async def save_many(self, kind: str, records: list[tuple[R, int]]) -> int:
        """Append many records as their next revisions under one lease.

        For writers that own a whole kind, such as the derived-layer compiler.
        Unchanged records are skipped; any revision conflict aborts the batch
        before anything is written. Returns how many records were written.
        """
        if not records:
            return 0
        model = type(records[0][0])
        async with metadata_lock(f"{self.namespace}:{kind}:batch", timeout_seconds=60) as lock:
            ids = [record.id for record, _ in records]
            latest: dict[str, R] = {}
            for start in range(0, len(ids), 500):
                latest.update(await self.latest_many(kind, ids[start:start + 500], model))
            rows = []
            now = utc_now()
            for record, expected in records:
                old = latest.get(record.id)
                if old is not None:
                    if old.model_dump(exclude=_UNCHANGED) == record.model_dump(exclude=_UNCHANGED):
                        continue
                    if old.revision != expected:
                        raise HTTPException(
                            status_code=409, detail="Record changed; reload before editing"
                        )
                    record = record.model_copy(update={"created_at": old.created_at})
                elif expected:
                    raise HTTPException(
                        status_code=409, detail="Record no longer matches this revision"
                    )
                record = record.model_copy(update={"revision": expected + 1, "updated_at": now})
                rows.append([
                    record.id,
                    record.revision,
                    fingerprint(record.model_dump(mode="json"))[:32],
                    record.actor.principal,
                    record.actor.active_role,
                    record.actor.security_context_version,
                    record.model_dump_json(),
                ])
            for start in range(0, len(rows), 50):
                if not await lock.renew():
                    raise HTTPException(status_code=409, detail="Metadata lease expired; retry")
                chunk = rows[start:start + 50]
                await db.execute_system(
                    f"INSERT INTO {self._table(kind)} "
                    "(id,revision,operation_id,actor,active_role,security_context_version,"
                    "payload,created_at) VALUES "
                    + ",".join(["(%s,%s,%s,%s,%s,%s,%s,NOW())"] * len(chunk)),
                    [value for row in chunk for value in row],
                )
            return len(rows)


class OntologyRepository(RevisionRepository):
    def __init__(self) -> None:
        super().__init__(ONTOLOGY_TABLES, "ontology")


ontology_repository = OntologyRepository()
