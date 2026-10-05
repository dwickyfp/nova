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


@dataclass(frozen=True)
class Table:
    """One governed table behind one Semantic View."""

    view_id: str
    view_name: str
    database: str
    columns: tuple[str, ...]
    date_column: str
    #: Metric name in the Semantic View -> column of the table.
    metrics: dict[str, str]
    dimensions: dict[str, list[str]]
    definition: dict
    fingerprint: str


RETAIL = Table(
    view_id=VIEW_ID,
    view_name="news_retail_sales",
    database="news_demo",
    columns=dataset.COLUMNS,
    date_column="sale_date",
    metrics={"revenue": "revenue", "order_count": "orders"},
    dimensions={name: dataset.values(name) for name in ("city", "channel", "category")},
    definition=DEFINITION,
    fingerprint=FINGERPRINT,
)


def domain_table(domain) -> Table:
    """The table and published definition of one of the extra desks."""
    definition = parse_ossie((MODEL_PATH.parent / domain.model_file).read_text()).model
    return Table(
        view_id=domain.view_name.replace("_", "-"),
        view_name=domain.view_name,
        database=domain.database,
        columns=domain.columns,
        date_column=domain.date_column,
        metrics={item.name: item.column for item in domain.measures},
        dimensions={name: list(values) for name, values in domain.dimensions.items()},
        definition=definition,
        fingerprint=SemanticModelIR.from_ossie(definition).fingerprint,
    )


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
    #: Databases the role is granted; ``None`` means every database.
    databases: frozenset[str] | None = None

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
#: The reader role is granted the business database only, not engineering.
_READER = frozenset({"news_demo"})
PRINCIPALS = {
    item.username: item
    for item in (
        SERVICE,
        Principal("news_manager", "news_editor"),
        Principal("news_bandung", "news_reader", {"city": {"Bandung"}}, databases=_READER),
        Principal("news_jakarta", "news_reader", {"city": {"Jakarta"}}, databases=_READER),
        Principal(
            "news_west_java", "news_reader", {"city": {"Bandung", "Jakarta"}}, databases=_READER
        ),
        Principal("news_online", "news_reader", {"channel": {"Online"}}, databases=_READER),
        Principal("news_masked", "news_reader", masked=True, databases=_READER),
        Principal("news_off", "news_editor", news_enabled=False),
        Principal("news_outsider", "finance", can_read=False),
    )
}


def may_see(
    principal: Principal, dimension: str | None, value: str | None, table: Table = RETAIL
) -> bool:
    """The access oracle: can this caller read every row behind a story?

    Computed from the grants and scopes alone, independently of how the reader
    decides. A scope on a column the table does not have restricts nothing.
    """
    if not principal.can_read or principal.masked or not principal.news_enabled:
        return False
    if principal.databases is not None and table.database not in principal.databases:
        return False
    scopes = {
        name: allowed for name, allowed in principal.scopes.items() if name in table.dimensions
    }
    for scoped, allowed in scopes.items():
        if scoped != dimension:
            if allowed != set(table.dimensions[scoped]):
                return False
        elif value not in allowed:
            return False
    return dimension is not None or not scopes


class Warehouse:
    def __init__(self, rows: list[tuple] | None = None, principals=None, table: Table = RETAIL):
        self.table = table
        self.index = {name: position for position, name in enumerate(table.columns)}
        self.rows = rows if rows is not None else dataset.generate()
        self.principals = deepcopy(dict(principals or PRINCIPALS))
        self.view = {
            "id": table.view_id,
            "name": table.view_name,
            "owner_name": "news_manager",
            "database_name": table.database,
            "visibility": "PUBLIC",
            "active_version": 1,
            "status": "ACTIVE",
            "news_enabled": False,
            "news_config": None,
        }
        self.queries: dict[str, int] = defaultdict(int)

    # ── Semantic View service surface used by the newsroom ──────────────────

    async def _get(self, view_id):
        return deepcopy(self.view) if view_id == self.table.view_id else None

    async def _version(self, view_id, version):
        if view_id != self.table.view_id or version != 1:
            return None
        return {"view_id": view_id, "version": 1, "definition": self.table.definition,
                "fingerprint": self.table.fingerprint, "status": "ACTIVE"}

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
            view_id != self.table.view_id
            or principal is None
            or not principal.can_read
            or principal.role != user.get("active_role")
            or (
                principal.databases is not None
                and self.table.database not in principal.databases
            )
        ):
            raise HTTPException(status_code=404, detail="Semantic version not found")
        return await self._get(view_id), await self._version(view_id, version)

    async def execute_plan(self, view_id, version, plan, user):
        await self._readable_version(view_id, version, user)
        principal = self.principals[user["username"]]
        self.queries[principal.username] += 1
        table, index = self.table, self.index
        start, end = (
            datetime.fromisoformat(item.value).date()
            for item in plan.filters
            if item.field == table.date_column
        )
        dimension = plan.dimensions[0] if plan.dimensions else None
        scopes = {
            name: allowed for name, allowed in principal.scopes.items() if name in index
        }
        measures = [index[table.metrics[name]] for name in plan.metrics]
        totals: dict[tuple, list] = {}
        for row in self.rows:
            day = row[index[table.date_column]]
            if not start <= day < end or any(
                row[index[name]] not in allowed for name, allowed in scopes.items()
            ):
                continue
            key = (day, row[index[dimension]]) if dimension else (day,)
            bucket = totals.setdefault(key, [0] * len(measures))
            for position, column in enumerate(measures):
                bucket[position] += row[column]
        columns = [table.date_column, *([dimension] if dimension else []), *plan.metrics]
        rows = [
            # The first metric is the watched measure; a mask hides it.
            [*key, 0 if principal.masked else values[0], *values[1:]]
            for key, values in sorted(totals.items(), key=str)
        ]
        return {
            "columns": columns,
            "rows": rows[: plan.limit or 1000],
            "truncated": len(rows) > (plan.limit or 1000),
            "model_fingerprint": table.fingerprint,
            "query_id": "governed-query",
        }


class Estate:
    """Several governed tables behind several Semantic Views, one set of callers."""

    def __init__(self, houses: list[Warehouse]):
        self.houses = {house.table.view_id: house for house in houses}
        self.principals = houses[0].principals
        for house in houses:
            house.principals = self.principals

    def _house(self, view_id) -> Warehouse | None:
        return self.houses.get(view_id)

    async def _get(self, view_id):
        house = self._house(view_id)
        return await house._get(view_id) if house else None

    async def _version(self, view_id, version):
        house = self._house(view_id)
        return await house._version(view_id, version) if house else None

    async def _owned(self, view_id, user):
        house = self._house(view_id)
        if house is None:
            raise HTTPException(status_code=404, detail="Semantic View not found")
        return await house._owned(view_id, user)

    async def describe(self, view_id, user):
        return await self._get(view_id)

    async def news_views(self):
        return [
            deepcopy(house.view)
            for house in sorted(self.houses.values(), key=lambda item: item.view["name"])
            if house.view["news_enabled"]
        ]

    async def save_news(self, view_id, **settings):
        await self.houses[view_id].save_news(view_id, **settings)

    async def _readable_version(self, view_id, version, user):
        house = self._house(view_id)
        if house is None:
            raise HTTPException(status_code=404, detail="Semantic version not found")
        return await house._readable_version(view_id, version, user)

    async def execute_plan(self, view_id, version, plan, user):
        return await self.houses[view_id].execute_plan(view_id, version, plan, user)


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

    async def edition_stories(self, story_ids, scope, model):
        return [
            deepcopy(row)
            for story_id in story_ids
            if (row := await self.get("stories", story_id, scope, model))
        ]

    async def shared_story(self, record_id, model):
        return deepcopy(self.rows.get(("stories", record_id)))


class Bindings:
    def __init__(self, bindings=None):
        self.bindings = bindings if bindings is not None else {"news_editor": SERVICE.username}

    async def get_role_execution_user(self, role):
        return self.bindings.get(role)


class Reactions:
    """Per-reader reactions kept in memory, with the store's interface."""

    def __init__(self):
        self.saved: dict[tuple[str, str], dict] = {}

    async def set(self, user_name, story, reaction):
        from app.modules.intelligence.newsroom_feedback import features

        if reaction is None:
            self.saved.pop((user_name, story["id"]), None)
            return
        self.saved[(user_name, story["id"])] = {
            "story_id": story["id"], "reaction": reaction, "features": features(story),
        }

    async def rows(self, user_name):
        return [row for (owner, _), row in self.saved.items() if owner == user_name]
