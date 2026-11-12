"""News switches, journals and reader reactions on the real PG control plane."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.database import db
from app.core.metadata_migrations import migrate
from app.core.metadata_scope import acting_as
from app.core.redis import session_store
from app.modules.intelligence.contracts import Confidence, Scope, SemanticRef, Window
from app.modules.intelligence.engine_repository import intelligence_repository as repository
from app.modules.intelligence.engine_schema import ensure_engine_schema
from app.modules.intelligence.metadata_repository import intelligence_metadata, user_actor
from app.modules.intelligence.newsroom_contracts import (
    Edition,
    Narrative,
    SeriesPoint,
    Story,
    StoryProof,
)
from app.modules.intelligence.semantic_view_schema import ensure_semantic_view_schema
from app.modules.intelligence.semantic_views import semantic_view_service
from tests.conftest import redis_url, root_password
from tests.integration._stack import require_shared_stack, shared_stack_host_port
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.engine
DAY = date(2026, 10, 4)
WINDOW = Window(start=datetime(2026, 10, 4, tzinfo=UTC), end=datetime(2026, 10, 5, tzinfo=UTC))


@pytest.fixture
async def newsroom_db(request, monkeypatch, docker_services, engine_metadata):
    require_shared_stack(request)
    monkeypatch.setattr(settings, "STARROCKS_HOST", "127.0.0.1")
    monkeypatch.setattr(
        settings,
        "STARROCKS_FE_MYSQL_PORT",
        shared_stack_host_port("NOVA_TEST_FE_MYSQL_PORT", 29030),
    )
    monkeypatch.setattr(settings, "STARROCKS_ROOT_USER", "root")
    monkeypatch.setattr(settings, "STARROCKS_ROOT_PASSWORD", root_password())
    monkeypatch.setattr(
        settings,
        "REDIS_URL",
        redis_url(shared_stack_host_port("NOVA_TEST_REDIS_PORT", 26379)),
    )
    await db.init_system_pool()
    await session_store.init()
    await db.execute_system("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
    await ensure_semantic_view_schema()
    await ensure_engine_schema()
    user = session("newsroom-test-" + uuid4().hex, active="news_editor", security_context_version=1)
    try:
        async with acting_as(user_actor(user)):
            yield user
    finally:
        await session_store.close()
        await db.close_system_pool()


async def _view(user: dict) -> str:
    view_id = uuid4().hex
    async with intelligence_metadata(user_actor(user)) as repo:
        await repo.create_view(
            catalog="default_catalog",
            database="news_demo",
            schema="default",
            name="news_" + view_id[:12],
            view_id=view_id,
            definition={"version": "0.1.1", "datasets": []},
            fingerprint="f" * 64,
        )
        await repo.record_validation(view_id, 1, {"valid": True, "errors": []})
        await repo.publish(view_id, 1, expected_revision=1, expected_active=None)
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


async def test_the_schema_upgrade_is_idempotent_and_matches_the_migration(
    newsroom_db,
    engine_metadata,
    metadata_inspect,
):
    await ensure_semantic_view_schema()
    await ensure_engine_schema()
    for _ in range(2):
        await migrate(engine_metadata.role_config("nova_meta_migrator"), app_version="e" * 40)
    columns = await metadata_inspect(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='nova_meta' AND table_name='config_semantic_views'"
    )
    assert {"news_enabled", "news_config", "news_updated_by", "news_updated_at"} <= {
        row[0] for row in columns
    }


async def test_the_news_switch_roundtrips_and_lists_only_enabled_views(newsroom_db):
    owner = newsroom_db
    on, off = await _view(owner), await _view(owner)
    assert (await semantic_view_service._get(on))["news_enabled"] is False
    assert (await semantic_view_service._get(on))["news_config"] is None

    config = {"execution_role": "news_editor", "metrics": ["revenue"], "cadence_minutes": 60}
    await semantic_view_service.save_news(on, enabled=True, config=config, user=owner)
    await semantic_view_service.save_news(off, enabled=False, config=config, user=owner)

    stored = await semantic_view_service._get(on)
    assert stored["news_enabled"] is True
    assert stored["news_config"] == config
    assert stored["news_updated_by"] == owner["username"]
    assert stored["news_updated_at"]
    listed = {
        row["id"]
        for row in await semantic_view_service.news_views()
        if row["owner_name"] == owner["username"]
    }
    assert listed == {on}


async def test_the_latest_edition_is_scoped_to_its_view_and_execution_account(newsroom_db):
    scope = Scope(
        principal=newsroom_db["username"], active_role="news_editor", security_context_version=1
    )
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
    scope = Scope(
        principal=newsroom_db["username"], active_role="news_editor", security_context_version=1
    )
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
        proofs=[
            StoryProof(group_key="g" * 16, scope="slice", value="Bandung", digest="d" * 64, rows=29)
        ],
        series=[SeriesPoint(date=DAY, value=254505601.0, count=912)],
        narrative=Narrative(
            headline="Revenue fell 34.6% in Bandung",
            deck="Deck",
            what_happened="What",
            why_it_matters="Why",
            what_to_check="Check",
        ),
        dedup_key="k" * 64,
    )

    saved = await repository.save("stories", story)

    assert await repository.shared_story(saved.id, Story, actor=user_actor(newsroom_db)) == saved
    revised = await repository.save(
        "stories", saved.model_copy(update={"rank": 2}), expected_revision=1
    )
    assert await repository.edition_stories([saved.id, "0" * 64], scope, Story) == [revised]
    assert await repository.edition_stories([], scope, Story) == []
    saved = revised
    assert await repository.get("stories", saved.id, scope, Story) == saved
    assert await repository.shared_story("0" * 64, Story, actor=user_actor(newsroom_db)) is None
    stranger = scope.model_copy(update={"principal": "someone_else"})
    assert await repository.get("stories", saved.id, stranger, Story) is None
    with pytest.raises(Exception) as refused:
        await repository.save("stories", saved.model_copy(update={"scope": stranger, "rank": 2}))
    assert getattr(refused.value, "status_code", None) == 404


async def test_reactions_roundtrip_per_reader_and_the_migration_is_idempotent(newsroom_db):
    from app.modules.intelligence.newsroom_feedback import feedback_store

    reader = newsroom_db
    story = {
        "id": "s" * 64,
        "view_id": "view",
        "metric": "revenue",
        "change": -5.0,
        "slice": {"dimension": "city", "value": "Bandung"},
    }

    await feedback_store.set(reader, story, "like")
    await feedback_store.set(reader, story, "dislike")

    assert await feedback_store.rows(reader) == [
        {
            "story_id": "s" * 64,
            "reaction": "dislike",
            "features": {
                "view": "view",
                "metric": "revenue",
                "direction": "decrease",
                "dimension": "city",
                "slice": "city=Bandung",
            },
        }
    ]
    assert (
        await feedback_store.rows(
            session("someone-else-" + reader["username"], active="news_editor")
        )
        == []
    )
    await feedback_store.set(reader, story, None)
    assert await feedback_store.rows(reader) == []
