"""In-memory doubles for ontology service tests."""

from __future__ import annotations

import copy
from itertools import count

from fastapi import HTTPException

from app.modules.intelligence.contracts import fingerprint
from app.modules.ontology.contracts import OntologyRecord
from app.modules.ontology.repository import _UNCHANGED
from app.modules.ontology.representations import PublishedView
from tests.benchmark.news import warehouse

_session = count(1)


def user(name: str, role: str, *, version: int = 1) -> dict:
    return {
        "username": name,
        "session_id": f"session-{next(_session)}",
        "roles": [role],
        "assigned_roles": [role],
        "active_role": role,
        "security_context_version": version,
    }


ADMIN = user("nova_admin", "ACCOUNTADMIN")
FINANCE_STEWARD = user("fiona", "finance_steward")
SALES_STEWARD = user("sam", "sales_steward")
ANALYST = user("ana", "finance_analyst")
OUTSIDER = user("oscar", "marketing")


class FakeRepository:
    """Same contract as ``OntologyRepository``, held in memory."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, list[OntologyRecord]]] = {}
        self.fail_after: int | None = None
        self.saves = 0

    def _records(self, kind: str, record_id: str) -> list[OntologyRecord]:
        return self.rows.setdefault(kind, {}).setdefault(record_id, [])

    async def latest(self, kind, record_id, model, *, actor=None):
        records = self._records(kind, record_id)
        return copy.deepcopy(records[-1]) if records else None

    async def revision(self, kind, record_id, revision, model, *, actor=None):
        for record in self._records(kind, record_id):
            if record.revision == revision:
                return copy.deepcopy(record)
        return None

    async def history(self, kind, record_id, model, *, actor=None):
        return [copy.deepcopy(item) for item in reversed(self._records(kind, record_id))]

    async def current(self, kind, model, *, actor=None):
        return [
            copy.deepcopy(records[-1])
            for _, records in sorted(self.rows.get(kind, {}).items())
            if records
        ]

    async def latest_many(self, kind, record_ids, model, *, actor=None):
        found = {}
        for record_id in record_ids:
            record = await self.latest(kind, record_id, model)
            if record is not None:
                found[record_id] = record
        return found

    async def current_matching(self, kind, model, field, value, *, limit=200, actor=None):
        return [
            item for item in await self.current(kind, model)
            if str(getattr(item, field)) == value
        ][:limit]

    async def save(self, kind, record, *, expected_revision=0, actor=None):
        records = self._records(kind, record.id)
        if records:
            old = records[-1]
            if old.model_dump(exclude=_UNCHANGED) == record.model_dump(exclude=_UNCHANGED):
                return copy.deepcopy(old)
            if old.revision != expected_revision:
                raise HTTPException(status_code=409, detail="Record changed; reload before editing")
            record = record.model_copy(update={"created_at": old.created_at})
        elif expected_revision:
            raise HTTPException(status_code=409, detail="Record no longer matches this revision")
        if self.fail_after is not None and self.saves >= self.fail_after:
            raise RuntimeError("simulated crash")
        self.saves += 1
        record = record.model_copy(update={"revision": expected_revision + 1})
        records.append(record)
        return copy.deepcopy(record)


    async def save_many(self, kind, records):
        written = 0
        for record, expected in records:
            old = self._records(kind, record.id)
            before = old[-1].revision if old else 0
            saved = await self.save(kind, record, expected_revision=expected)
            written += saved.revision != before
        return written


def definition(*, revenue_expression: str = "SUM(sales.revenue)", drop_revenue: bool = False):
    value = copy.deepcopy(warehouse.DEFINITION)
    metrics = []
    for metric in value["metrics"]:
        if metric["name"] == "revenue":
            if drop_revenue:
                continue
            metric["expression"] = revenue_expression
            metric["synonyms"] = ["net sales"]
        metrics.append(metric)
    value["metrics"] = metrics
    return value


class FakeCatalog:
    def __init__(self) -> None:
        self.views: dict[str, PublishedView] = {}
        self.history: dict[tuple[str, int], PublishedView] = {}
        self.hidden: dict[str, set[str]] = {}
        self.publish("sales_sv", definition())

    def publish(self, view_id: str, value: dict) -> PublishedView:
        prior = self.views.get(view_id)
        view = PublishedView(
            view_id=view_id,
            name=value["name"],
            version=(prior.version + 1) if prior else 1,
            fingerprint=fingerprint(value),
            definition=value,
        )
        self.views[view_id] = view
        self.history[(view_id, view.version)] = view
        return view

    def unpublish(self, view_id: str) -> None:
        self.views.pop(view_id, None)

    async def published(self, view_id):
        return self.views.get(view_id)

    async def visible(self, view_id, user):
        return view_id in self.views and user["username"] not in self.hidden.get(view_id, set())

    async def version(self, view_id, version):
        return self.history.get((view_id, version))

    async def published_ids(self, user):
        return [view_id for view_id in sorted(self.views) if await self.visible(view_id, user)]
