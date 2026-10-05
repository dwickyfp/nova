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
