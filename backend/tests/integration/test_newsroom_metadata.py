"""Real StarRocks acceptance for the News switch columns and edition journals."""

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.modules.intelligence.contracts import Confidence, Scope, SemanticRef, Window
from app.modules.intelligence.engine_repository import intelligence_repository as repository
from app.modules.intelligence.engine_schema import ensure_engine_schema
from app.modules.intelligence.newsroom_contracts import (
    Edition,
    Narrative,
    SeriesPoint,
    Story,
    StoryProof,
)
from app.modules.intelligence.semantic_view_schema import ensure_semantic_view_schema
from app.modules.intelligence.semantic_views import semantic_view_service
from tests.integration._stack import require_shared_stack, shared_stack_host_port

pytestmark = pytest.mark.engine
DAY = date(2026, 10, 4)
WINDOW = Window(
    start=datetime(2026, 10, 4, tzinfo=UTC), end=datetime(2026, 10, 5, tzinfo=UTC)
)


@pytest.fixture
async def newsroom_db(request, monkeypatch, docker_services):
    require_shared_stack(request)
    monkeypatch.setattr(settings, "STARROCKS_HOST", "127.0.0.1")
    monkeypatch.setattr(
        settings,
        "STARROCKS_FE_MYSQL_PORT",
        shared_stack_host_port("NOVA_TEST_FE_MYSQL_PORT", 29030),
    )
    monkeypatch.setattr(settings, "STARROCKS_ROOT_USER", "root")
    monkeypatch.setattr(settings, "STARROCKS_ROOT_PASSWORD", "")
    monkeypatch.setattr(
        settings,
        "REDIS_URL",
        f"redis://127.0.0.1:{shared_stack_host_port('NOVA_TEST_REDIS_PORT', 26379)}/0",
    )
    await db.init_system_pool()
    await session_store.init()
    await db.execute_system("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
    await ensure_semantic_view_schema()
    await ensure_engine_schema()
    marker = "newsroom-test-" + uuid4().hex
    try:
        yield marker
    finally:
        for table in ("CONFIG_INTELLIGENCE_EDITIONS", "CONFIG_INTELLIGENCE_STORIES"):
            await db.execute_system(
                f"DELETE FROM NOVA_SYSTEM.{table} WHERE principal=%s", [marker]
            )
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS WHERE owner_name=%s", [marker]
        )
        await session_store.close()
        await db.close_system_pool()


async def _view(owner: str) -> str:
    view_id = uuid4().hex
    await db.execute_system(
        "INSERT INTO NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS "
        "(catalog_name,database_name,schema_name,name,id,owner_name,visibility,"
        "active_version,status,created_at,updated_at) "
        "VALUES ('default_catalog','news_demo','',%s,%s,%s,'PUBLIC',1,'ACTIVE',NOW(),NOW())",
        ["news_" + view_id[:12], view_id, owner],
    )
    return view_id


def _edition(view_id: str, scope: Scope, day: date, story_ids=()) -> Edition:
    return Edition(
        id=uuid4().hex * 2,
        scope=scope,
        semantic=SemanticRef(view_id=view_id, version=1, fingerprint="pinned"),
        edition_date=day,
        window=WINDOW,
        nonce=uuid4().hex,
        pressed_at=datetime(2026, 10, 5, 2, tzinfo=UTC),
        config_digest="c" * 64,
        story_ids=list(story_ids),
    )


async def test_the_schema_upgrade_is_idempotent_and_matches_the_migration(newsroom_db):
    await ensure_semantic_view_schema()
    await ensure_engine_schema()
    migration = Path("migrations/20261006_newsroom.sql").read_text()
    for statement in migration.split(";"):
        sql = "\n".join(
            line for line in statement.splitlines() if not line.strip().startswith("--")
        ).strip()
        if not sql:
            continue
        try:
            await db.execute_system(sql)
        except Exception as exc:
            # The columns already exist; only that outcome is acceptable.
            assert sql.startswith("ALTER TABLE"), sql
            assert "already exists" in str(exc).lower() or "duplicate" in str(exc).lower()
    columns = await db.execute_system("SHOW COLUMNS FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS")
    assert {"news_enabled", "news_config", "news_updated_by", "news_updated_at"} <= {
        row[0] for row in columns["rows"]
    }


async def test_the_news_switch_roundtrips_and_lists_only_enabled_views(newsroom_db):
    owner = newsroom_db
    on, off = await _view(owner), await _view(owner)
    assert (await semantic_view_service._get(on))["news_enabled"] is False
    assert (await semantic_view_service._get(on))["news_config"] is None

    config = {"execution_role": "news_editor", "metrics": ["revenue"], "cadence_minutes": 60}
    await semantic_view_service.save_news(on, enabled=True, config=config, username=owner)
    await semantic_view_service.save_news(off, enabled=False, config=config, username=owner)

    stored = await semantic_view_service._get(on)
    assert stored["news_enabled"] is True
    assert stored["news_config"] == config
    assert stored["news_updated_by"] == owner
    assert stored["news_updated_at"]
    listed = {
        row["id"] for row in await semantic_view_service.news_views() if row["owner_name"] == owner
    }
    assert listed == {on}


async def test_the_latest_edition_is_scoped_to_its_view_and_execution_account(newsroom_db):
    scope = Scope(principal=newsroom_db, active_role="news_editor", security_context_version=1)
    view_id, other_view = uuid4().hex, uuid4().hex
    older = await repository.save("editions", _edition(view_id, scope, date(2026, 10, 3)))
    newest = await repository.save("editions", _edition(view_id, scope, DAY))
    await repository.save("editions", _edition(other_view, scope, date(2026, 10, 9)))
    revised = await repository.save(
        "editions",
        newest.model_copy(update={"warnings": ["revenue by city: coverage note"]}),
        expected_revision=1,
    )

    latest = await repository.latest_edition(view_id, scope, Edition)

    assert (latest.id, latest.revision, latest.edition_date) == (revised.id, 2, DAY)
    assert latest.warnings == ["revenue by city: coverage note"]
    assert older.edition_date < latest.edition_date
    stranger = scope.model_copy(update={"principal": "nova_task_service_other"})
    assert await repository.latest_edition(view_id, stranger, Edition) is None
    assert await repository.latest_edition(uuid4().hex, scope, Edition) is None


async def test_a_story_roundtrips_with_exact_figures_and_is_found_by_id_only(newsroom_db):
    scope = Scope(principal=newsroom_db, active_role="news_editor", security_context_version=1)
    view_id = uuid4().hex
    story = Story(
        id=uuid4().hex * 2,
        scope=scope,
        edition_id="e" * 64,
        semantic=SemanticRef(view_id=view_id, version=1, fingerprint="pinned"),
        metric="revenue",
        metric_label="Revenue",
        unit="IDR",
        slice={"dimension": "city", "value": "Bandung"},
        edition_date=DAY,
        window=WINDOW,
        baseline_dates=[date(2026, 9, 27), date(2026, 9, 20)],
        before=389127213.0,
        after=254505601.0,
        change=-134621612.0,
        relative_change=-0.3459,
        severity="critical",
        rank=1,
        confidence=Confidence(dimension="detection", method="matched-weekday-median-mad-v1"),
        proofs=[StoryProof(group_key="g" * 16, scope="slice", value="Bandung",
                           digest="d" * 64, rows=29)],
        series=[SeriesPoint(date=DAY, value=254505601.0, count=912)],
        narrative=Narrative(
            headline="Revenue fell 34.6% in Bandung", deck="Deck", what_happened="What",
            why_it_matters="Why", what_to_check="Check",
        ),
        dedup_key="k" * 64,
    )

    saved = await repository.save("stories", story)

    assert await repository.shared_story(saved.id, Story) == saved
    assert await repository.get("stories", saved.id, scope, Story) == saved
    assert await repository.shared_story("0" * 64, Story) is None
    stranger = scope.model_copy(update={"principal": "someone_else"})
    assert await repository.get("stories", saved.id, stranger, Story) is None
    with pytest.raises(Exception) as refused:
        await repository.save("stories", saved.model_copy(update={"scope": stranger, "rank": 2}))
    assert getattr(refused.value, "status_code", None) == 404
