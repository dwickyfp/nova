"""Regressions for SQL defects found in the November QA sweep."""

from __future__ import annotations

import pytest

from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.execution.adapters import (
    _prints_terminal_profile,
    _without_terminal_codes,
)
from app.sql_frontend.parser import parse_statement


def _statement(sql: str):
    return ast_builders.build(parse_statement(sql))


@pytest.mark.parametrize(
    "sql",
    [
        "EXPLAIN ANALYZE SELECT * FROM t",
        "EXPLAIN SELECT 1",
        "ANALYZE PROFILE FROM '01a1d437-92e7-7c07-ad0f-41668ad1e3aa'",
    ],
)
def test_profile_statements_are_recognised_from_the_tree(sql):
    assert _prints_terminal_profile(_statement(sql))


@pytest.mark.parametrize("sql", ["SELECT '\x1b[0m' AS x", "SHOW TABLES"])
def test_other_statements_keep_their_values(sql):
    assert not _prints_terminal_profile(_statement(sql))


def test_terminal_colour_codes_are_removed_from_profile_rows():
    row = ["    \x1b[0mQueryId: abc\x1b[0m", "\x1b[1m\x1b[31mScan\x1b[0m", 3, None]
    assert _without_terminal_codes(row) == ["    QueryId: abc", "Scan", 3, None]


def test_a_listed_folder_has_no_modification_time():
    from app.sql_frontend.execution.adapters import _listing_row

    columns = ["PATH", "SIZE", "IS_DIR", "MODIFICATION_TIME"]
    location_map = [("s3://bucket/nova/stages/x", "@files")]
    folder = _listing_row(
        ["s3://bucket/nova/stages/x/incoming", 0, 1, "1970-01-01T07:00:00"], location_map, columns
    )
    file = _listing_row(
        ["s3://bucket/nova/stages/x/a.csv", 20, 0, "2026-11-13T16:25:13"], location_map, columns
    )
    assert folder == ["@files/incoming", 0, 1, None]
    assert file == ["@files/a.csv", 20, 0, "2026-11-13T16:25:13"]


@pytest.mark.asyncio
async def test_streamed_listing_rows_drop_folder_times_too():
    from app.sql_frontend.execution.adapters import _LogicalLocationSink

    class Collect:
        def __init__(self):
            self.received: list[list] = []

        async def begin(self, columns, column_types):
            self.columns = columns

        async def rows(self, rows):
            self.received.extend(rows)

    target = Collect()
    sink = _LogicalLocationSink(target, [("s3://b/p", "@s")])
    await sink.begin(["PATH", "SIZE", "IS_DIR", "MODIFICATION_TIME"], ("", "", "", ""))
    await sink.rows([["s3://b/p/dir", 0, True, "1970-01-01T00:00:00"]])
    assert target.received == [["@s/dir", 0, True, None]]


@pytest.mark.parametrize(
    ("sql", "note"),
    [
        (
            "ALTER USER someone REQUIRE PASSWORD CHANGE",
            "The user will be asked to choose a new password the next time they sign in.",
        ),
        (
            "ALTER USER someone REQUIRE PASSWORD CHANGE OFF",
            "The user is no longer asked to choose a new password.",
        ),
    ],
)
@pytest.mark.asyncio
async def test_password_change_note_is_plain_language(monkeypatch, sql, note):
    from unittest.mock import AsyncMock

    from app.modules.users.service import user_service
    from app.sql_frontend.execution.adapters import FeatureAdapters

    monkeypatch.setattr(user_service, "user_exists", AsyncMock(return_value=True))
    adapters = FeatureAdapters.__new__(FeatureAdapters)
    adapters._audit = AsyncMock()
    adapters._set_flag = AsyncMock()
    result = await adapters._execute_force_password_change(
        sql=sql, normalized_sql=sql, username="admin", database=None, session_id="s",
        file_id=None, schema=None, role="ACCOUNTADMIN",
    )
    assert result.error is None
    assert result.warnings == [note]
    assert "StarRocks" not in note and "engine" not in note.lower()


@pytest.mark.asyncio
async def test_a_target_the_training_query_does_not_return_is_refused_before_a_job(monkeypatch):
    from unittest.mock import AsyncMock

    from app.modules.ml_engine import jobs
    from app.modules.ml_engine.service import MLEngineService, ml_engine_service
    from app.modules.ml_engine.spec import InvalidMLSpec

    service: MLEngineService = ml_engine_service
    monkeypatch.setattr(
        service, "_training_columns", AsyncMock(return_value=["visits", "spend"])
    )
    submit = AsyncMock()
    local = AsyncMock()
    monkeypatch.setattr(jobs, "submit_training", submit)
    monkeypatch.setattr(service, "train_model_locally", local)
    with pytest.raises(InvalidMLSpec, match="TARGET column 'loyal' is not returned"):
        await service.train_model(
            model_name="m", model_type="classification", algorithm="random_forest",
            training_sql="SELECT visits, spend FROM t", target_column="loyal",
            feature_columns=None, hyperparameters=None, test_size=0.2, database_name="db",
            username="analyst", password="pw", role="analyst_role", session_id="s1",
        )
    submit.assert_not_awaited()
    local.assert_not_awaited()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"target_column": "LOYAL"}, "TARGET column 'LOYAL'"),
        ({"target_column": "loyal", "feature_columns": ["visits", "age"]}, "FEATURES column 'age'"),
        ({"target_column": "loyal", "timestamp_column": "day"}, "TIMESTAMP column 'day'"),
    ],
)
@pytest.mark.asyncio
async def test_named_columns_match_exactly(monkeypatch, kwargs, message):
    from unittest.mock import AsyncMock

    from app.modules.ml_engine.service import ml_engine_service
    from app.modules.ml_engine.spec import InvalidMLSpec

    monkeypatch.setattr(
        ml_engine_service,
        "_training_columns",
        AsyncMock(return_value=["visits", "spend", "loyal"]),
    )
    monkeypatch.setattr(ml_engine_service, "train_model_locally", AsyncMock())
    with pytest.raises(InvalidMLSpec, match=message):
        await ml_engine_service.train_model(
            model_name="m", model_type="classification", algorithm="random_forest",
            training_sql="SELECT visits, spend, loyal FROM t", hyperparameters=None,
            test_size=0.2, database_name="db", username="analyst", password="pw",
            role="analyst_role", **{"feature_columns": None, **kwargs},
        )


@pytest.mark.asyncio
async def test_valid_columns_and_an_unreadable_query_still_train(monkeypatch):
    from unittest.mock import AsyncMock

    from app.modules.ml_engine.service import ml_engine_service

    local = AsyncMock(return_value={"status": "succeeded"})
    monkeypatch.setattr(ml_engine_service, "train_model_locally", local)
    arguments = dict(
        model_name="m", model_type="classification", algorithm="random_forest",
        training_sql="SELECT visits, loyal FROM t", target_column="loyal",
        feature_columns=["visits"], hyperparameters=None, test_size=0.2,
        database_name="db", username="analyst", password="pw", role="analyst_role",
    )
    monkeypatch.setattr(
        ml_engine_service, "_training_columns", AsyncMock(return_value=["visits", "loyal"])
    )
    assert await ml_engine_service.train_model(**arguments) == {"status": "succeeded"}
    # The check is advisory: a query it cannot read is left to training to report.
    monkeypatch.setattr(
        ml_engine_service, "_training_columns", AsyncMock(side_effect=RuntimeError("down"))
    )
    assert await ml_engine_service.train_model(**arguments) == {"status": "succeeded"}
    assert local.await_count == 2


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SUBMIT TASK t AS INSERT INTO sink SELECT 1", "SUBMIT TASK runs in the background"),
        (
            "SUBMIT TASK t SCHEDULE EVERY(INTERVAL 1 MINUTE) AS INSERT INTO sink SELECT 1",
            "Use CREATE TASK",
        ),
        (
            "CREATE ROUTINE LOAD db.job ON t PROPERTIES ('format' = 'json') FROM KAFKA "
            "('kafka_broker_list' = 'b:9092', 'kafka_topic' = 'x')",
            "Routine loads run in the background",
        ),
        ("RESUME ROUTINE LOAD FOR db.job", "Routine loads run in the background"),
    ],
)
@pytest.mark.asyncio
async def test_engine_background_jobs_are_refused(sql, message):
    from app.sql_frontend.context import PlanningContext
    from app.sql_frontend.errors import CapabilityUnsupportedError
    from app.sql_frontend.planning.planner import SQLPlanner

    with pytest.raises(CapabilityUnsupportedError, match=message):
        await SQLPlanner().plan(_statement(sql), PlanningContext())


@pytest.mark.parametrize(
    "sql",
    [
        "PAUSE ROUTINE LOAD FOR db.job",
        "STOP ROUTINE LOAD FOR db.job",
        "SHOW ROUTINE LOAD FROM db",
        "DROP TASK t",
    ],
)
@pytest.mark.asyncio
async def test_stopping_and_inspecting_background_jobs_stays_available(sql):
    from app.sql_frontend.context import PlanningContext
    from app.sql_frontend.planning.planner import SQLPlanner

    await SQLPlanner().plan(_statement(sql), PlanningContext(database="db"))


@pytest.mark.asyncio
async def test_nova_create_task_is_not_refused_as_a_background_job():
    from app.sql_frontend.analysis.validation import _native_background_jobs

    _native_background_jobs(_statement("CREATE TASK t AS INSERT INTO sink SELECT 1"))
