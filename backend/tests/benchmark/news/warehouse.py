"""An in-memory governed warehouse for News tests and the offline benchmark.

It stands in for the Semantic View service and the engine underneath it. Row
scopes play the part of Ranger row filters: they are attached to the caller, and
every plan is evaluated only over the rows that caller may read. The newsroom
code under test receives no hint about scopes; it can only compare results.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from tests.benchmark.news import dataset

VIEW_ID = "news-retail-sales"
MODEL_PATH = (
    Path(__file__).resolve().parents[4] / "workspace" / "news_demo" / "retail_sales.ossie.yaml"
)
DEFINITION = parse_ossie(MODEL_PATH.read_text()).model
FINGERPRINT = SemanticModelIR.from_ossie(DEFINITION).fingerprint
_INDEX = {name: position for position, name in enumerate(dataset.COLUMNS)}


@dataclass
class Principal:
    """One caller: an active role, row scopes, and whether a measure is masked."""

    username: str
    role: str
    #: ``{dimension: allowed values}``; an absent dimension is unrestricted.
    scopes: dict[str, set[str]] = field(default_factory=dict)
    can_read: bool = True
    masked: bool = False
    news_enabled: bool = True

    def user(self) -> dict:
        return {
            "username": self.username,
            "active_role": self.role,
            "roles": [self.role],
            "assigned_roles": [self.role],
            "security_context_version": 1,
            "session_id": f"session-{self.username}",
            "encrypted_password": "test",
        }


SERVICE = Principal("nova_task_service_news", "news_editor")
PRINCIPALS = {
    item.username: item
    for item in (
        SERVICE,
        Principal("news_manager", "news_editor"),
        Principal("news_bandung", "news_reader", {"city": {"Bandung"}}),
        Principal("news_jakarta", "news_reader", {"city": {"Jakarta"}}),
        Principal("news_west_java", "news_reader", {"city": {"Bandung", "Jakarta"}}),
        Principal("news_online", "news_reader", {"channel": {"Online"}}),
        Principal("news_masked", "news_reader", masked=True),
        Principal("news_off", "news_editor", news_enabled=False),
        Principal("news_outsider", "finance", can_read=False),
    )
}


def may_see(principal: Principal, dimension: str | None, value: str | None) -> bool:
    """The access oracle: can this caller read every row behind a story?

    Computed from the scopes alone, independently of how the reader decides.
    """
    if not principal.can_read or principal.masked or not principal.news_enabled:
        return False
    for scoped, allowed in principal.scopes.items():
        if scoped != dimension:
            if allowed != set(dataset.values(scoped)):
                return False
        elif value not in allowed:
            return False
    return dimension is not None or not principal.scopes


class Warehouse:
    def __init__(self, rows: list[tuple] | None = None, principals=None):
        self.rows = rows if rows is not None else dataset.generate()
        self.principals = deepcopy(dict(principals or PRINCIPALS))
        self.view = {
            "id": VIEW_ID,
            "name": "news_retail_sales",
            "owner_name": "news_manager",
            "database_name": "news_demo",
            "visibility": "PUBLIC",
            "active_version": 1,
            "status": "ACTIVE",
            "news_enabled": False,
            "news_config": None,
        }
        self.queries: dict[str, int] = defaultdict(int)

    # ── Semantic View service surface used by the newsroom ──────────────────

    async def _get(self, view_id):
        return deepcopy(self.view) if view_id == VIEW_ID else None

    async def _version(self, view_id, version):
        if view_id != VIEW_ID or version != 1:
            return None
        return {"view_id": VIEW_ID, "version": 1, "definition": DEFINITION,
                "fingerprint": FINGERPRINT, "status": "ACTIVE"}

    async def _owned(self, view_id, user):
        view = await self._get(view_id)
        if not view or (
            view["owner_name"] != user["username"] and user["active_role"] != "ACCOUNTADMIN"
        ):
            raise HTTPException(status_code=404, detail="Semantic View not found")
        return view

    async def describe(self, view_id, user):
        return await self._get(view_id)

    async def news_views(self):
        return [deepcopy(self.view)] if self.view["news_enabled"] else []

    async def save_news(self, view_id, *, enabled, config, username):
        self.view.update(news_enabled=enabled, news_config=config, news_updated_by=username)

    async def _readable_version(self, view_id, version, user):
        principal = self.principals.get(user["username"])
        if (
            view_id != VIEW_ID
            or principal is None
            or not principal.can_read
            or principal.role != user.get("active_role")
        ):
            raise HTTPException(status_code=404, detail="Semantic version not found")
        return await self._get(view_id), await self._version(view_id, version)

    async def execute_plan(self, view_id, version, plan, user):
        await self._readable_version(view_id, version, user)
        principal = self.principals[user["username"]]
        self.queries[principal.username] += 1
        start, end = (
            datetime.fromisoformat(item.value).date()
            for item in plan.filters
            if item.field == "sale_date"
        )
        dimension = plan.dimensions[0] if plan.dimensions else None
        totals: dict[tuple, list] = {}
        for row in self.rows:
            day = row[_INDEX["sale_date"]]
            if not start <= day < end or any(
                row[_INDEX[name]] not in allowed for name, allowed in principal.scopes.items()
            ):
                continue
            key = (day, row[_INDEX[dimension]]) if dimension else (day,)
            bucket = totals.setdefault(key, [0, 0])
            bucket[0] += row[_INDEX["revenue"]]
            bucket[1] += row[_INDEX["orders"]]
        columns = ["sale_date", *([dimension] if dimension else []), "revenue", "order_count"]
        rows = [
            [*key, 0 if principal.masked else revenue, orders]
            for key, (revenue, orders) in sorted(totals.items(), key=str)
        ]
        return {
            "columns": columns,
            "rows": rows[: plan.limit or 1000],
            "truncated": len(rows) > (plan.limit or 1000),
            "model_fingerprint": FINGERPRINT,
            "query_id": "governed-query",
        }


class Journal:
    """Scoped record store with the repository's revision rules."""

    def __init__(self):
        self.rows: dict[tuple[str, str], object] = {}
        self.saves = 0

    async def get(self, kind, record_id, scope, model, **_kwargs):
        row = self.rows.get((kind, record_id))
        if row and (row.scope.principal, row.scope.active_role) == (
            scope.principal, scope.active_role
        ):
            return deepcopy(row)
        return None

    async def save(self, kind, record, *, expected_revision=0):
        old = self.rows.get((kind, record.id))
        ignored = {"created_at", "updated_at", "revision"}
        if old and old.model_dump(exclude=ignored) == record.model_dump(exclude=ignored):
            return deepcopy(old)
        if old and old.revision != expected_revision:
            raise HTTPException(status_code=409, detail="stale revision")
        record = record.model_copy(update={"revision": expected_revision + 1})
        self.rows[(kind, record.id)] = deepcopy(record)
        self.saves += 1
        return record

    async def latest_edition(self, view_id, scope, model):
        editions = [
            row for (kind, _), row in self.rows.items()
            if kind == "editions" and row.semantic.view_id == view_id
            and (row.scope.principal, row.scope.active_role)
            == (scope.principal, scope.active_role)
        ]
        return deepcopy(max(editions, key=lambda row: row.edition_date)) if editions else None

    async def shared_story(self, record_id, model):
        return deepcopy(self.rows.get(("stories", record_id)))


class Bindings:
    def __init__(self, bindings=None):
        self.bindings = bindings if bindings is not None else {"news_editor": SERVICE.username}

    async def get_role_execution_user(self, role):
        return self.bindings.get(role)
