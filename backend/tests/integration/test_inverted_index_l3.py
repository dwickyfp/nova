"""L3 regressions for the inverted-index surface against a real 4.1.4 engine.

The unit suite pins the *assembled SQL*; this module pins the thing only a real
engine can answer: whether the documented StarRocks 4.1 forms are accepted, and
whether the full-text predicates the API advertises actually run.

Two 4.1 behaviours the unit tests cannot see are asserted here:

1. ``ALTER TABLE ... ADD INDEX ... USING GIN`` is **rejected unless the table has
   ``replicated_storage=false``** (or was created on 4.0+, where the engine
   disables it automatically). The Nova create path does not set that table
   property — it is the caller's table — so the L3 test creates the fixture with
   it, mirroring how a Nova DDL table would be created.
2. ``MATCH`` / ``MATCH_ANY`` / ``MATCH_ALL`` are **pushdown-only**: they are
   accepted against a GIN-indexed column in a ``WHERE`` clause and rejected
   anywhere else. This is the constraint the API's notes claim, so it is
   asserted rather than described.

StarRocks is optional: when unreachable the module skips rather than fails.
Point at an already-running engine via ``NOVA_ORCH_SR_PORT`` (default 29030).
"""

from __future__ import annotations

import asyncio
import os

import asyncmy
import pytest
import pytest_asyncio

from tests.integration._stack import require_shared_stack

_EXPLICIT_PORT = os.getenv("NOVA_ORCH_SR_PORT")
SR_HOST = os.getenv("NOVA_ORCH_SR_HOST", "127.0.0.1")
SR_PORT = int(_EXPLICIT_PORT or "29030")
SR_USER = os.getenv("NOVA_ORCH_SR_USER", "root")
SR_PASSWORD = os.getenv("NOVA_ORCH_SR_PASSWORD", "")

DB = "nova_inverted_probe"
TABLE = "articles"

pytestmark = pytest.mark.engine


async def _sr_reachable() -> bool:
    try:
        conn = await asyncio.wait_for(
            asyncmy.connect(host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD),
            timeout=5,
        )
    except Exception:
        return False
    conn.close()
    return True


@pytest_asyncio.fixture
async def engine(request):
    require_shared_stack(request)
    if not await _sr_reachable():
        pytest.skip("StarRocks not reachable")
    conn = await asyncmy.connect(host=SR_HOST, port=SR_PORT, user=SR_USER, password=SR_PASSWORD)
    try:
        async with conn.cursor() as cur:
            # 4.1.4 gates full-text inverted indexes behind this FE config. The
            # engine's own error names it, so it is not a Nova invention:
            #   The inverted index is disabled, enable it by setting FE config
            #   `enable_experimental_gin` to true.
            # A production Nova deployment must set it in fe.conf or run the
            # same ADMIN statement; the API surfaces the engine's message
            # verbatim when it is off, which is why this fixture turns it on.
            await cur.execute('ADMIN SET FRONTEND CONFIG ("enable_experimental_gin" = "true")')
            await cur.execute(f"DROP DATABASE IF EXISTS {DB}")
            await cur.execute(f"CREATE DATABASE {DB}")
            # ``replicated_storage=false`` is required for a full-text inverted
            # index on a pre-4.0-style table; a 4.1 CREATE TABLE with
            # ``replication_num=1`` still needs it set explicitly here because
            # the auto-disable only applies when the index is in the CREATE
            # statement, not added afterwards.
            await cur.execute(
                f"CREATE TABLE {DB}.{TABLE} ("
                "id BIGINT NOT NULL, content STRING NOT NULL) "
                "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
                'PROPERTIES("replication_num"="1", "replicated_storage"="false")'
            )
        yield conn
    finally:
        async with conn.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS {DB}")
        conn.close()


async def _execute(conn, sql: str):
    async with conn.cursor() as cur:
        await cur.execute(sql)
        return await cur.fetchall()


async def _wait_for_schema_change(conn, timeout_s: float = 60.0) -> None:
    """Block until every pending ``ALTER TABLE`` schema change has finished.

    ``ALTER TABLE ... ADD/DROP INDEX`` is asynchronous on 4.1.4: the statement
    returns immediately and the index appears only once the schema change
    completes (observed ~14s on the test stack). Issuing a second ALTER against
    the table while one is in flight fails with "A schema change operation is in
    progress", so this waits rather than sleeps a fixed amount.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
            await cur.execute(f"SHOW ALTER TABLE COLUMN FROM {DB}")
            rows = await cur.fetchall()
        states = [str(row.get("State") or "") for row in rows]
        if not states or all(state in ("FINISHED", "CANCELLED") for state in states):
            return
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"schema change did not settle in {timeout_s}s: {states}")
        await asyncio.sleep(1)


async def _index_names(conn) -> set[str]:
    async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
        await cur.execute(f"SHOW INDEX FROM {DB}.{TABLE}")
        rows = await cur.fetchall()
    return {str(row.get("Key_name")) for row in rows}


class TestInvertedIndexDdl:
    async def test_create_list_query_and_drop_the_documented_way(self, engine):
        """The full lifecycle the API wraps, against 4.1.4."""
        await _execute(
            engine,
            f"ALTER TABLE {DB}.{TABLE} ADD INDEX idx_content (content) "
            'USING GIN ("parser"="english")',
        )
        await _wait_for_schema_change(engine)

        assert "idx_content" in await _index_names(engine)

        await _execute(
            engine,
            f"INSERT INTO {DB}.{TABLE} VALUES " "(1, 'machine learning'), (2, 'database systems')",
        )

        matched = await _execute(
            engine,
            f"SELECT id FROM {DB}.{TABLE} " "WHERE content MATCH_ANY 'machine learning'",
        )
        assert {int(row[0]) for row in matched} == {1}

        # MATCH_ALL: both terms must be present.
        all_rows = await _execute(
            engine,
            f"SELECT id FROM {DB}.{TABLE} WHERE content MATCH_ALL 'machine learning'",
        )
        assert {int(row[0]) for row in all_rows} == {1}

        # MATCH with the documented wildcard form.
        wildcard = await _execute(
            engine,
            f"SELECT id FROM {DB}.{TABLE} WHERE content MATCH 'database%'",
        )
        assert {int(row[0]) for row in wildcard} == {2}

        await _execute(engine, f"ALTER TABLE {DB}.{TABLE} DROP INDEX idx_content")
        await _wait_for_schema_change(engine)
        assert "idx_content" not in await _index_names(engine)

    async def test_match_outside_a_where_clause_is_rejected(self, engine):
        """The pushdown-only constraint the API's predicate notes state.

        The engine reports this at execution, and the error comes from the BE
        ("Match can only used as a pushdown predicate on column with GIN in a
        single query"), so the table has to hold a row for the query to reach
        the backend. The message is asserted loosely — the point is the shape,
        not the exact wording of a BE error string.
        """
        await _execute(
            engine,
            f"ALTER TABLE {DB}.{TABLE} ADD INDEX idx_content (content) "
            'USING GIN ("parser"="english")',
        )
        await _wait_for_schema_change(engine)
        try:
            await _execute(engine, f"INSERT INTO {DB}.{TABLE} VALUES (1, 'machine')")
            with pytest.raises(asyncmy.errors.ProgrammingError) as excinfo:
                await _execute(engine, f"SELECT content MATCH 'machine' FROM {DB}.{TABLE}")
            assert "pushdown predicate" in str(excinfo.value)
        finally:
            await _execute(engine, f"ALTER TABLE {DB}.{TABLE} DROP INDEX idx_content")
            await _wait_for_schema_change(engine)

    async def test_builtin_imp_lib_is_accepted_and_recorded(self, engine):
        """v4.1's built-in implementation keyword parses on the pinned engine."""
        await _execute(
            engine,
            f"ALTER TABLE {DB}.{TABLE} ADD INDEX idx_builtin (content) "
            'USING GIN ("parser"="english", "imp_lib"="builtin")',
        )
        await _wait_for_schema_change(engine)

        async with engine.cursor(asyncmy.cursors.DictCursor) as cur:
            await cur.execute(f"SHOW INDEX FROM {DB}.{TABLE}")
            rows = await cur.fetchall()
        row = next(r for r in rows if str(r.get("Key_name")) == "idx_builtin")
        # The engine records the resolved implementation in ``Index_type``.
        assert "builtin" in str(row.get("Index_type"))
