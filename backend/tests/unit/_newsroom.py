"""Shared wiring for newsroom tests over the in-memory governed warehouse."""

from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from app.modules.intelligence import engine, newsroom
from app.modules.intelligence.engine import IntelligenceService
from app.modules.intelligence.newsroom import NewsroomService
from app.modules.intelligence.newsroom_contracts import NewsConfig, NewsSettings
from tests.benchmark.news import warehouse


def config(**overrides) -> NewsConfig:
    return NewsConfig(
        **{
            "execution_role": "news_editor",
            "metrics": ["revenue"],
            "count_metric": "order_count",
            "slice_dimensions": ["city", "channel", "category"],
            "time_dimension": "sale_date",
            "max_stories": 24,
            "narrative": "template",
            **overrides,
        }
    )


def press_time(day: date) -> datetime:
    """A morning after ``day``, when ``day`` is the last complete local day."""
    return datetime.combine(day + timedelta(days=1), time(9), ZoneInfo("Asia/Jakarta"))


class Newsroom:
    """A newsroom over the warehouse, with schedules and audit captured."""

    def __init__(self, monkeypatch, *, rows=None, bindings=None, writer=None, judge=None):
        @asynccontextmanager
        async def lock(_key, **_kwargs):
            yield

        self.audit = AsyncMock()
        self.schedules: list[dict] = []

        async def schedule(record, user, **options):
            self.schedules.append({"view_id": record.id, "by": user["username"], **options})
            return {"task_id": "task", "enabled": options["enabled"]}

        monkeypatch.setattr(newsroom, "metadata_lock", lock)
        monkeypatch.setattr(newsroom, "write_audit_log", self.audit)
        monkeypatch.setattr(engine, "write_audit_log", AsyncMock())
        monkeypatch.setattr(newsroom, "configure_schedule", schedule)
        self.warehouse = warehouse.Warehouse(rows)
        self.journal = warehouse.Journal()
        self.bindings = warehouse.Bindings(bindings)
        self.reactions = warehouse.Reactions()
        self.service = NewsroomService(
            IntelligenceService(self.journal, self.warehouse),
            self.warehouse,
            self.journal,
            self.bindings,
            writer,
            judge,
            self.reactions,
        )

    def user(self, name: str) -> dict:
        return self.warehouse.principals[name].user()

    async def enable(self, **overrides):
        return await self.service.configure(
            warehouse.VIEW_ID,
            NewsSettings(enabled=True, config=config(**overrides)),
            self.user("news_manager"),
        )

    async def press(self, day: date) -> dict:
        return await self.service.run_cycle(
            warehouse.VIEW_ID, warehouse.SERVICE.user(), now=press_time(day)
        )

    async def read(self, name: str, day: date | None = None) -> list[dict]:
        paper = await self.service.newspaper(self.user(name), day=day)
        return [story for section in paper["sections"] for story in section["stories"]]

    def stories(self, day: date | None = None) -> list:
        return sorted(
            (
                row
                for (kind, _), row in self.journal.rows.items()
                if kind == "stories" and (day is None or row.edition_date == day)
            ),
            key=lambda row: row.rank,
        )

    def editions(self) -> list:
        return [row for (kind, _), row in self.journal.rows.items() if kind == "editions"]


def subject(story) -> tuple:
    """``(dimension, value)`` of a stored or public story; total is ``(None, None)``."""
    sliced = story["slice"] if isinstance(story, dict) else story.slice
    if sliced is None:
        return (None, None)
    if isinstance(sliced, dict):
        return (sliced["dimension"], sliced["value"])
    return (sliced.dimension, sliced.value)


class Desks(Newsroom):
    """The newsroom over four desks: sales plus the three extra domains."""

    def __init__(self, monkeypatch):
        super().__init__(monkeypatch)
        from tests.benchmark.news import domains

        self.domains = {item.key: item for item in domains.DOMAINS}
        self.tables = {"sales": warehouse.RETAIL}
        houses = [self.warehouse]
        for domain in domains.DOMAINS:
            table = warehouse.domain_table(domain)
            self.tables[domain.key] = table
            houses.append(warehouse.Warehouse(domains.generate(domain), table=table))
        self.estate = warehouse.Estate(houses)
        self.service = NewsroomService(
            IntelligenceService(self.journal, self.estate),
            self.estate,
            self.journal,
            self.bindings,
            None,
            self.judge_for_desks,
            self.reactions,
        )

    @staticmethod
    async def judge_for_desks(ir, metric):
        return "better" if metric == "revenue" else "worse"

    async def enable_all(self):
        await self.enable(narrative="model")
        for key, domain in self.domains.items():
            await self.service.configure(
                self.tables[key].view_id,
                NewsSettings(
                    enabled=True,
                    config=config(
                        metrics=list(domain.watched),
                        count_metric=domain.count_metric,
                        slice_dimensions=list(domain.slices),
                        time_dimension=domain.date_column,
                        narrative="model",
                    ),
                ),
                self.user("news_manager"),
            )

    async def press_all(self, day: date) -> dict[str, dict]:
        return {
            key: await self.service.run_cycle(
                table.view_id, warehouse.SERVICE.user(), now=press_time(day)
            )
            for key, table in self.tables.items()
        }

    async def paper(self, name: str, day: date | None = None) -> dict:
        return await self.service.newspaper(self.user(name), day=day)
