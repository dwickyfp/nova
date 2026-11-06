"""Real StarRocks/Redis acceptance for Business Event Hub storage and correlation reads."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.modules.intelligence.business_events import (
    EVENT_TABLES,
    BusinessIncident,
    Grouping,
    ensure_event_schema,
)
from app.modules.intelligence.contracts import Window
from app.modules.ontology.contracts import Actor
from app.modules.ontology.repository import RevisionRepository
from tests.conftest import redis_url, root_password
from tests.integration._stack import require_shared_stack, shared_stack_host_port

pytestmark = pytest.mark.engine

ACTOR = Actor(principal="events-test", active_role="news_desk", security_context_version=1)
START = datetime(2026, 11, 5, 17, tzinfo=UTC)


@pytest.fixture
async def events_db(request, monkeypatch, docker_services):
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
        settings, "REDIS_URL", redis_url(shared_stack_host_port("NOVA_TEST_REDIS_PORT", 26379))
    )
    await db.init_system_pool()
    await session_store.init()
    await db.execute_system("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
    await ensure_event_schema()
    prefix = "it_" + uuid4().hex[:16]
    try:
        yield prefix
    finally:
        for table in EVENT_TABLES.values():
            await db.execute_system(
                f"DELETE FROM NOVA_SYSTEM.{table} WHERE id LIKE %s", [prefix + "%"]
            )
        await session_store.close()
        await db.close_system_pool()


async def test_event_ddl_matches_the_migration(events_db):
    migration = Path("migrations/20261106_business_event_hub.sql").read_text()
    for statement in migration.split(";"):
        sql = "\n".join(
            line for line in statement.splitlines() if not line.strip().startswith("--")
        ).strip()
        if sql:
            await db.execute_system(sql)
    header = "-- Business Event Hub: shared signals and incidents over News sources"
    bootstrap = (
        Path("../docker/init-nova.sql")
        .read_text()
        .split(header, 1)[1]
        .split("-- End Business Event Hub schema", 1)[0]
        .strip()
    )
    assert bootstrap == migration.split("\n", 1)[1].strip()


async def test_incidents_are_found_by_population_on_the_engine(events_db):
    repository = RevisionRepository(EVENT_TABLES, "business-events")
    population = (events_db + "0" * 64)[:64]
    other = (events_db + "1" * 64)[:64]
    window = Window(start=START, end=START + timedelta(days=1))

    def incident(suffix: str, population_fp: str) -> BusinessIncident:
        signal = f"{events_db}_sig_{suffix}"
        return BusinessIncident(
            id=f"{events_db}_inc_{suffix}", actor=ACTOR, population_fingerprint=population_fp,
            window=window, signal_ids=[signal], severity="warning",
            grouping=[Grouping(signal_id=signal, rule="opened")],
        )

    first = await repository.save("incidents", incident("a", population))
    await repository.save("incidents", incident("b", other))
    grown = await repository.save(
        "incidents",
        first.model_copy(update={"signal_ids": [*first.signal_ids, f"{events_db}_sig_c"]}),
        expected_revision=1,
    )
    found = await repository.current_matching(
        "incidents", BusinessIncident, "population_fingerprint", population
    )
    assert [item.id for item in found] == [grown.id]
    assert found[0].revision == 2 and len(found[0].signal_ids) == 2
