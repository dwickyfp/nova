"""The reader's access proof is reused briefly, refreshed in the background, never shared."""

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.modules.intelligence import newsroom_cache, newsroom_refresher
from app.modules.intelligence.newsroom_cache import ProofCache
from tests.benchmark.news import dataset, warehouse
from tests.unit._newsroom import Newsroom, subject

TODAY = dataset.LAST_DAY
BANDUNG = ("city", "Bandung")


class Redis:
    """The few Redis commands the cache uses, over dictionaries."""

    def __init__(self, clock):
        self.values: dict[str, tuple[str, float | None]] = {}
        self.sets: dict[str, dict[str, float]] = {}
        self.clock = clock
        self.down = False

    def _check(self):
        if self.down:
            raise ConnectionError("redis unavailable")

    async def get(self, key):
        self._check()
        value, expires = self.values.get(key, (None, None))
        if expires is not None and expires <= self.clock():
            self.values.pop(key, None)
            return None
        return value

    async def set(self, key, value, ex=None):
        self._check()
        self.values[key] = (value, self.clock() + ex if ex else None)

    async def incr(self, key):
        self._check()
        value = int((await self.get(key)) or 0) + 1
        self.values[key] = (str(value), None)
        return value

    async def zadd(self, key, mapping):
        self._check()
        self.sets.setdefault(key, {}).update(mapping)

    async def zremrangebyscore(self, key, _low, high):
        members = self.sets.get(key, {})
        for member in [name for name, score in members.items() if score <= high]:
            del members[member]

    async def zrevrange(self, key, start, stop):
        ranked = sorted(self.sets.get(key, {}).items(), key=lambda item: -item[1])
        return [member for member, _score in ranked[start : stop + 1]]

    async def zrem(self, key, member):
        self.sets.get(key, {}).pop(member, None)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
async def room(monkeypatch):
    clock = Clock()
    redis = Redis(clock)
    newsroom = Newsroom(monkeypatch)
    newsroom.clock, newsroom.redis = clock, redis
    newsroom.cache = ProofCache(redis, clock)
    newsroom.service.cache = newsroom.cache
    await newsroom.enable()
    await newsroom.press(TODAY)
    return newsroom


def queries(room, name):
    return room.warehouse.queries[name]


async def subjects(room, name, day=TODAY):
    return [subject(story) for story in await room.read(name, day)]


async def test_a_second_read_runs_no_query_and_shows_the_same_stories(room):
    first = await subjects(room, "news_bandung")
    spent = queries(room, "news_bandung")

    second = await subjects(room, "news_bandung")

    assert first == second == [BANDUNG]
    assert spent == 4
    assert queries(room, "news_bandung") == spent


async def test_opening_and_reacting_reuse_the_same_proof(room):
    [story] = await room.read("news_bandung", TODAY)
    spent = queries(room, "news_bandung")

    await room.service.story(story["id"], room.user("news_bandung"))
    await room.service.react(story["id"], "like", room.user("news_bandung"))

    assert queries(room, "news_bandung") == spent


async def test_a_proof_is_never_reused_by_another_reader(room):
    assert await subjects(room, "news_bandung") == [BANDUNG]

    assert await subjects(room, "news_jakarta") == []
    assert queries(room, "news_jakarta") == 4
    [listed] = await room.read("news_manager", TODAY)
    with pytest.raises(HTTPException) as hidden:
        await room.service.story(listed["id"], room.user("news_jakarta"))
    assert hidden.value.status_code == 404


async def test_a_new_session_or_role_switch_proves_again(room):
    await subjects(room, "news_bandung")
    user = room.user("news_bandung")

    again = await room.service.newspaper(user | {"session_id": "another-session"}, day=TODAY)
    switched = await room.service.newspaper(user | {"security_context_version": 2}, day=TODAY)

    assert queries(room, "news_bandung") == 12
    assert [subject(row) for row in again["sections"][0]["stories"]] == [BANDUNG]
    assert [subject(row) for row in switched["sections"][0]["stories"]] == [BANDUNG]


async def test_an_access_change_through_nova_retires_every_proof_at_once(room):
    assert await subjects(room, "news_manager") == [BANDUNG]
    room.warehouse.principals["news_manager"].scopes = {"city": {"Jakarta"}}
    # Without the signal the recent proof would still be used.
    assert await subjects(room, "news_manager") == [BANDUNG]

    await room.cache.bump()

    assert await subjects(room, "news_manager") == []


async def test_no_proof_is_kept_while_an_access_change_is_still_reaching_the_engine(room):
    await room.cache.bump()
    spent = queries(room, "news_manager")
    # The engine has not applied the new filter yet, so this proof shows the old access.
    assert await subjects(room, "news_manager") == [BANDUNG]
    room.warehouse.principals["news_manager"].scopes = {"city": {"Jakarta"}}

    assert await subjects(room, "news_manager") == []
    assert queries(room, "news_manager") == spent + 8

    room.clock.advance(2 * newsroom_cache.settings.RANGER_POLICY_PROPAGATION_SECONDS + 1)
    await subjects(room, "news_manager")
    await subjects(room, "news_manager")
    assert queries(room, "news_manager") == spent + 12


async def test_a_change_outside_nova_is_bounded_by_the_cache_lifetime(room, monkeypatch):
    assert await subjects(room, "news_manager") == [BANDUNG]
    room.warehouse.principals["news_manager"].scopes = {"city": {"Jakarta"}}

    room.clock.advance(newsroom_cache.settings.NEWS_PROOF_CACHE_SECONDS - 1)
    assert await subjects(room, "news_manager") == [BANDUNG]
    room.clock.advance(2)

    assert await subjects(room, "news_manager") == []


async def test_a_repressed_edition_is_proven_again(room):
    await subjects(room, "news_manager")
    spent = queries(room, "news_manager")
    room.warehouse.rows.append((TODAY, "Bandung", "Store", "Grocery", Decimal(5_000_000), 40))
    await room.press(TODAY)

    assert await subjects(room, "news_manager") == [BANDUNG]
    assert queries(room, "news_manager") == spent + 4


async def test_a_view_the_reader_cannot_read_is_remembered_as_unreadable(room):
    paper = await room.service.newspaper(room.user("news_outsider"), day=TODAY)
    room.warehouse.principals["news_outsider"].can_read = True
    room.warehouse.principals["news_outsider"].role = "finance"

    assert paper["sections"] == []
    assert (await room.service.newspaper(room.user("news_outsider"), day=TODAY))["sections"] == []
    await room.cache.bump()
    assert (await room.service.newspaper(room.user("news_outsider"), day=TODAY))["sections"]


async def test_every_reader_still_matches_the_access_oracle_with_the_cache_on(room):
    await room.press(TODAY - timedelta(days=1))
    leaks = []
    for _round in range(2):
        for day in (TODAY - timedelta(days=1), TODAY):
            pressed = {subject(row) for row in room.stories(day)}
            for name, principal in room.warehouse.principals.items():
                if not principal.news_enabled:
                    continue
                shown = set(await subjects(room, name, day))
                allowed = {item for item in pressed if warehouse.may_see(principal, *item)}
                if shown != allowed:
                    leaks.append((day.isoformat(), name))

    assert leaks == []


async def test_zero_lifetime_turns_the_cache_off(room, monkeypatch):
    monkeypatch.setattr(newsroom_cache.settings, "NEWS_PROOF_CACHE_SECONDS", 0)

    await subjects(room, "news_bandung")
    await subjects(room, "news_bandung")

    assert queries(room, "news_bandung") == 8
    assert room.redis.values == {}


async def test_an_unavailable_cache_falls_back_to_proving(room):
    room.redis.down = True

    assert await subjects(room, "news_bandung") == [BANDUNG]
    assert await subjects(room, "news_bandung") == [BANDUNG]
    assert queries(room, "news_bandung") == 8


async def test_the_cache_holds_digests_and_counts_only(room):
    await subjects(room, "news_manager")

    stored = " ".join(value for value, _expires in room.redis.values.values())
    assert "254505601" not in stored and "Revenue" not in stored
    assert "news_manager" not in " ".join(room.redis.values)


@pytest.fixture
def sessions(room, monkeypatch):
    """Live sessions for the refresher, and who has News enabled."""
    live = {f"session-{name}": room.user(name) for name in ("news_manager", "news_bandung")}

    async def get(session_id):
        return dict(live[session_id]) if session_id in live else None

    async def enabled(username):
        return room.warehouse.principals[username].news_enabled

    monkeypatch.setattr(newsroom_refresher.session_store, "get", get)
    monkeypatch.setattr(newsroom_refresher, "decrypt_password", lambda _value: "secret")
    monkeypatch.setattr(newsroom_refresher, "is_news_enabled", enabled)
    return live


async def test_the_refresher_renews_a_stale_proof_so_reads_stay_free(room, sessions):
    await subjects(room, "news_bandung")
    spent = queries(room, "news_bandung")
    # Signing in queues a reader before they open News.
    await room.cache.touch("session-news_manager")

    assert await newsroom_refresher.refresh_once(room.service, room.cache) == 2
    # A proof that is still fresh is left alone; the manager had none yet.
    assert queries(room, "news_bandung") == spent
    assert queries(room, "news_manager") == 4

    room.clock.advance(newsroom_cache.settings.NEWS_PROOF_REFRESH_SECONDS + 1)
    await newsroom_refresher.refresh_once(room.service, room.cache)

    assert queries(room, "news_bandung") == spent + 4
    await subjects(room, "news_bandung")
    assert queries(room, "news_bandung") == spent + 4


async def test_the_refresher_picks_up_an_access_change_made_outside_nova(room, sessions):
    assert await subjects(room, "news_manager") == [BANDUNG]
    room.warehouse.principals["news_manager"].scopes = {"city": {"Jakarta"}}
    room.clock.advance(newsroom_cache.settings.NEWS_PROOF_REFRESH_SECONDS + 1)

    await newsroom_refresher.refresh_once(room.service, room.cache)

    assert await subjects(room, "news_manager") == []


async def test_the_refresher_drops_expired_idle_and_unentitled_sessions(room, sessions):
    await subjects(room, "news_bandung")
    await subjects(room, "news_manager")
    del sessions["session-news_manager"]

    assert await newsroom_refresher.refresh_once(room.service, room.cache) == 1
    assert await room.cache.readers(20) == ["session-news_bandung"]

    room.warehouse.principals["news_bandung"].news_enabled = False
    assert await newsroom_refresher.refresh_once(room.service, room.cache) == 0
    assert await room.cache.readers(20) == []

    await room.cache.touch("session-news_bandung")
    room.clock.advance(newsroom_cache.settings.NEWS_PROOF_READER_IDLE_SECONDS + 1)
    assert await room.cache.readers(20) == []


async def test_one_failing_reader_does_not_stop_the_pass(room, sessions, monkeypatch):
    await subjects(room, "news_bandung")
    await subjects(room, "news_manager")
    original = room.service.refresh_reader

    async def flaky(user):
        if user["username"] == "news_manager":
            raise RuntimeError("engine unavailable")
        return await original(user)

    monkeypatch.setattr(room.service, "refresh_reader", flaky)

    assert await newsroom_refresher.refresh_once(room.service, room.cache) == 1


# ── City RBAC with the cache on ─────────────────────────────────────────────


async def test_same_role_readers_of_different_cities_never_share_a_proof(room):
    bandung, jakarta = room.user("news_bandung"), room.user("news_jakarta")
    assert bandung["active_role"] == jakarta["active_role"] == "news_reader"
    await room.press(TODAY - timedelta(days=1))
    national = TODAY - timedelta(days=1)

    # Read twice each, interleaved, so every read after the first is cached.
    seen = {"news_bandung": [], "news_jakarta": []}
    for _round in range(3):
        for name in seen:
            seen[name].append(await subjects(room, name, national))

    assert seen["news_bandung"] == [[BANDUNG]] * 3
    assert seen["news_jakarta"] == [[("city", "Jakarta")]] * 3
    assert queries(room, "news_bandung") == queries(room, "news_jakarta") == 4


async def test_a_reader_moved_to_another_city_loses_the_old_city_at_once(room):
    assert await subjects(room, "news_bandung") == [BANDUNG]

    # An administrator changes the reader's city scope through Nova.
    room.warehouse.principals["news_bandung"].scopes = {"city": {"Jakarta"}}
    await room.cache.bump()

    assert await subjects(room, "news_bandung") == []
    [listed] = await room.read("news_manager", TODAY)
    with pytest.raises(HTTPException) as hidden:
        await room.service.story(listed["id"], room.user("news_bandung"))
    assert hidden.value.status_code == 404


async def test_a_reader_whose_role_is_revoked_loses_the_section_at_once(room):
    assert await subjects(room, "news_bandung") == [BANDUNG]

    room.warehouse.principals["news_bandung"].can_read = False
    await room.cache.bump()

    paper = await room.service.newspaper(room.user("news_bandung"), day=TODAY)
    assert paper["sections"] == []


async def test_a_cached_proof_cannot_be_used_under_another_active_role(room):
    assert await subjects(room, "news_manager") == [BANDUNG]
    switched = room.user("news_manager") | {
        "active_role": "finance",
        "roles": ["finance"],
        "assigned_roles": ["finance"],
        "security_context_version": 2,
    }

    assert (await room.service.newspaper(switched, day=TODAY))["sections"] == []


async def test_a_like_cannot_be_recorded_from_a_cache_the_reader_does_not_own(room):
    [listed] = await room.read("news_manager", TODAY)
    await room.read("news_manager", TODAY)

    with pytest.raises(HTTPException) as refused:
        await room.service.react(listed["id"], "like", room.user("news_jakarta"))

    assert refused.value.status_code == 404
    assert room.reactions.saved == {}


async def test_city_readers_keep_their_city_on_every_desk_with_the_cache_on(monkeypatch):
    from tests.unit._newsroom import Desks

    clock = Clock()
    desks = Desks(monkeypatch)
    desks.service.cache = ProofCache(Redis(clock), clock)
    await desks.enable_all()
    await desks.press_all(TODAY)

    for _round in range(2):
        leaks = []
        for name, principal in desks.estate.principals.items():
            if not principal.news_enabled:
                continue
            shown = {
                (section["view_id"], *subject(row))
                for section in (await desks.paper(name, TODAY))["sections"]
                for row in section["stories"]
            }
            allowed = {
                (table.view_id, *subject(row))
                for table in desks.tables.values()
                for row in desks.stories(TODAY)
                if row.semantic.view_id == table.view_id
                and warehouse.may_see(principal, *subject(row), table)
            }
            if shown != allowed:
                leaks.append(name)
        assert leaks == []

    bandung = await desks.paper("news_bandung", TODAY)
    names = {section["name"] for section in bandung["sections"]}
    assert "news_service_reliability" not in names
    cities = {
        row["slice"]["value"]
        for section in bandung["sections"]
        for row in section["stories"]
        if row["slice"] and row["slice"]["dimension"] == "city"
    }
    assert cities == {"Bandung"}
