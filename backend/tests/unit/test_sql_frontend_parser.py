from dataclasses import replace

import pytest

from app.sql_frontend.ast.builder import AstBuilderRegistry, ast_builders
from app.sql_frontend.errors import SemanticError, SQLSyntaxError
from app.sql_frontend.parser import parse_statement, parsing_scope
from app.sql_frontend.source import SqlFragment
from app.sql_frontend.stages import stage_view


@pytest.mark.parametrize(
    "sql,node",
    [
        ("select 1", "NativeStatement"),
        ("SELECT '@stage.data.csv; ML_PREDICT'", "NativeStatement"),
        ("SELECT /* @stage */ 1", "NativeStatement"),
        ("SELECT @x", "NativeStatement"),
        (
            "SELECT * FROM @stage1.data.csv s JOIN @stage2.2026.*.parquet t ON s.id=t.id",
            "StageAwareStatement",
        ),
        ("LIST FILES @stage1/", "StageAwareStatement"),
        ("COPY INTO target FROM @stage1.data.csv", "StageAwareStatement"),
        ("COPY INTO @stage1.output.parquet FROM target", "StageAwareStatement"),
        ("INSERT INTO @stage1.output.parquet SELECT * FROM target", "StageAwareStatement"),
        ("CREATE TASK db.default.t AS INSERT INTO sink SELECT 1", "CreateTaskStatement"),
        ("SUBMIT TASK t AS INSERT INTO sink SELECT 1", "NativeStatement"),
        (
            "CREATE ML_MODEL m TYPE = REGRESSION TARGET = 'y' INPUT = (SELECT x,y FROM t)",
            "CreateMLModelStatement",
        ),
        ("SELECT ML_PREDICT('m', x) FROM t", "MLPredictStatement"),
        ("SELECT * FROM ML_PREDICT_TABLE('m', 'SELECT x FROM t')", "MLMaterializeStatement"),
        ("SELECT * FROM ML_FORECAST(MODEL => 'm', HORIZON => 3)", "MLForecastStatement"),
        ("ALTER USER 'alice' REQUIRE PASSWORD CHANGE", "ForcePasswordChangeStatement"),
        ("SHOW AVAILABLE ROLES", "SecurityStatement"),
        ("GRANT SELECT ON db.t TO ROLE analyst", "SecurityStatement"),
        ("EXPLAIN SELECT ML_PREDICT('m', x) FROM t", "NativeStatement"),
    ],
)
def test_classification(sql, node):
    assert type(ast_builders.build(parse_statement(sql))).__name__ == node


def test_spans_preserve_comments_whitespace_and_original_source():
    source = "-- heading\n  SELECT /* spaced */ 'a; b' AS x;"
    parsed = parse_statement(source, original_sql="original editor source")
    assert parsed.original_sql == "original editor source"
    assert parsed.span.line == 2 and parsed.span.column == 2
    assert SqlFragment(parsed.span, source).sql == "SELECT /* spaced */ 'a; b' AS x"
    assert parsed.span.end == source.index(";", source.index("AS x"))


@pytest.mark.parametrize(
    "sql", ["", "-- only comment", "SELECT 1; SELECT 2", "SELECT FROM", "SELECT 1 !"]
)
def test_complete_input_and_lexer_errors_fail_closed(sql):
    with pytest.raises(SQLSyntaxError):
        parse_statement(sql)


def test_positioned_diagnostics_do_not_echo_literals(capsys):
    with pytest.raises(SQLSyntaxError) as error:
        parse_statement("SELECT 1\nFROM 'sensitive-password'")
    assert error.value.diagnostics[0].line == 2
    assert error.value.diagnostics[0].column >= 0
    assert "sensitive-password" not in str(error.value)
    assert capsys.readouterr().err == ""


def test_request_cache_reuses_only_unchanged_source(monkeypatch):
    import app.sql_frontend.parser as parser

    original = parser.parse_tree
    calls = []

    def counted(sql):
        calls.append(sql)
        return original(sql)

    monkeypatch.setattr(parser, "parse_tree", counted)
    with parsing_scope():
        first = parse_statement("SELECT * FROM @s.a.csv")
        second = parse_statement("SELECT * FROM @s.a.csv", original_sql="editor")
        assert first.parse_tree is second.parse_tree
        assert second.original_sql == "editor"
        parse_statement("SELECT 2")
    parse_statement("SELECT 2")
    assert len(calls) == 3


def test_stage_spans_are_per_reference_and_exclude_aliases():
    sql = "SELECT * FROM @s.a.csv a JOIN @s.b.csv b ON a.id=b.id"
    refs = stage_view(parse_statement(sql)).stage_refs
    assert [sql[ref.start : ref.end] for ref in refs] == ["@s.a.csv", "@s.b.csv"]


def test_unregistered_extension_context_and_duplicates_fail_closed():
    registry = AstBuilderRegistry()
    registry.register("QueryStatementContext", lambda parsed: ast_builders.build(parsed))
    with pytest.raises(ValueError, match="already registered"):
        registry.register("QueryStatementContext", lambda parsed: None)
    fake = type("NovaDummyStatementContext", (), {})()
    with pytest.raises(SemanticError, match="No AST builder"):
        registry.build(replace(parse_statement("SELECT 1"), statement_context=fake))


def test_debug_representations_hide_source_and_authentication():
    from app.sql_frontend.context import ExecutionContext

    parsed = parse_statement("CREATE USER 'alice' IDENTIFIED BY 'private-value'")
    context = ExecutionContext(
        "alice", encrypted_password="encrypted-value", statements={0: ast_builders.build(parsed)}
    )
    assert "private-value" not in repr(parsed)
    assert "private-value" not in repr(context)
    assert "encrypted-value" not in repr(context)


# ── Parsing off the event loop ──────────────────────────────────────────────


def _large_insert(rows: int = 400) -> str:
    values = ", ".join(f"({i}, 'name{i}', {i}.5)" for i in range(rows))
    return f"INSERT INTO db.t VALUES {values}"


@pytest.fixture
def parse_threads(monkeypatch):
    """Record which thread each parse runs on."""
    import threading

    from app.sql_frontend import parser

    seen: list[str] = []
    original = parser.parse_tree

    def recording(sql, **kwargs):
        seen.append(threading.current_thread().name)
        return original(sql, **kwargs)

    monkeypatch.setattr(parser, "parse_tree", recording)
    return seen


async def test_short_statement_is_parsed_inline(monkeypatch, parse_threads):
    import threading

    from app.core.config import settings
    from app.sql_frontend.parser import parse_statement_async

    monkeypatch.setattr(settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 8192)

    await parse_statement_async("SELECT 1")

    assert parse_threads == [threading.current_thread().name]


async def test_large_statement_is_parsed_on_a_worker_thread(monkeypatch, parse_threads):
    from app.core.config import settings
    from app.sql_frontend.parser import parse_statement_async

    monkeypatch.setattr(settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 64)

    await parse_statement_async(_large_insert())

    assert len(parse_threads) == 1 and parse_threads[0].startswith("nova-sql-parse")


async def test_zero_threshold_keeps_every_parse_inline(monkeypatch, parse_threads):
    import threading

    from app.core.config import settings
    from app.sql_frontend.parser import parse_statement_async

    monkeypatch.setattr(settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 0)

    await parse_statement_async(_large_insert())

    assert parse_threads == [threading.current_thread().name]


@pytest.mark.parametrize(
    "sql",
    [
        "select 1",
        "SELECT * FROM @stage1.data.csv s JOIN @stage2.2026.*.parquet t ON s.id=t.id",
        "CREATE ML_MODEL m TYPE = REGRESSION TARGET = 'y' INPUT = (SELECT x,y FROM t)",
        "SELECT ML_PREDICT('m', x) FROM t",
        "GRANT SELECT ON db.t TO ROLE analyst",
        _large_insert(50),
    ],
)
async def test_worker_parse_matches_the_inline_parse(monkeypatch, sql):
    from app.core.config import settings
    from app.sql_frontend.parser import parse_statement_async

    monkeypatch.setattr(settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 1)

    inline = parse_statement(sql, original_sql=f"{sql} ")
    offloaded = await parse_statement_async(sql, original_sql=f"{sql} ")

    assert offloaded.parse_tree.toStringTree() == inline.parse_tree.toStringTree()
    assert [token.text for token in offloaded.visible_tokens] == [
        token.text for token in inline.visible_tokens
    ]
    assert (offloaded.span, offloaded.original_sql, offloaded.normalized_sql) == (
        inline.span,
        inline.original_sql,
        inline.normalized_sql,
    )
    assert type(ast_builders.build(offloaded)) is type(ast_builders.build(inline))


async def test_worker_parse_reports_the_same_syntax_error(monkeypatch):
    from app.core.config import settings
    from app.sql_frontend.parser import parse_statement_async

    monkeypatch.setattr(settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 1)
    sql = "SELECT FROM WHERE password = 'hunter2'"

    with pytest.raises(SQLSyntaxError) as inline:
        parse_statement(sql)
    with pytest.raises(SQLSyntaxError) as offloaded:
        await parse_statement_async(sql)

    assert offloaded.value.diagnostics == inline.value.diagnostics
    assert "hunter2" not in str(offloaded.value)


async def test_worker_parse_rejects_more_than_one_statement(monkeypatch):
    from app.core.config import settings
    from app.sql_frontend.parser import parse_statement_async

    monkeypatch.setattr(settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 1)

    with pytest.raises(SQLSyntaxError):
        await parse_statement_async("SELECT 1; SELECT 2")


async def test_worker_parse_fills_and_reuses_the_request_cache(monkeypatch, parse_threads):
    from app.core.config import settings
    from app.sql_frontend.parser import parse_statement_async

    monkeypatch.setattr(settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 64)
    sql = _large_insert()

    with parsing_scope():
        first = await parse_statement_async(sql)
        second = await parse_statement_async(sql, original_sql="-- note\n" + sql)
        # The synchronous entry point shares the same request cache.
        third = parse_statement(sql)

    assert len(parse_threads) == 1
    assert second.parse_tree is first.parse_tree and third.parse_tree is first.parse_tree
    assert second.original_sql.startswith("-- note")


async def test_worker_parse_keeps_nothing_outside_a_request(monkeypatch, parse_threads):
    from app.core.config import settings
    from app.sql_frontend.parser import parse_statement_async

    monkeypatch.setattr(settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 64)
    sql = _large_insert()

    await parse_statement_async(sql)
    await parse_statement_async(sql)

    assert len(parse_threads) == 2


async def test_worker_and_inline_parses_do_not_disturb_each_other(monkeypatch):
    import asyncio

    from app.core.config import settings
    from app.sql_frontend.parser import parse_statement_async

    monkeypatch.setattr(settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 2000)
    large = [_large_insert(150 + step) for step in range(6)]
    small = [f"SELECT a{step}, COUNT(*) FROM t WHERE b > {step} GROUP BY 1" for step in range(40)]
    expected_large = [parse_statement(sql).parse_tree.toStringTree() for sql in large]
    expected_small = [parse_statement(sql).parse_tree.toStringTree() for sql in small]

    async def inline_parses():
        trees = []
        for sql in small:
            trees.append((await parse_statement_async(sql)).parse_tree.toStringTree())
            await asyncio.sleep(0)
        return trees

    *offloaded, inline = await asyncio.gather(
        *(parse_statement_async(sql) for sql in large), inline_parses()
    )

    assert [item.parse_tree.toStringTree() for item in offloaded] == expected_large
    assert inline == expected_small


async def test_event_loop_keeps_running_during_a_large_parse(monkeypatch):
    import asyncio

    from app.core.config import settings
    from app.sql_frontend.parser import parse_statement_async

    monkeypatch.setattr(settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 64)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        await parse_statement_async(_large_insert(1500))
    finally:
        task.cancel()

    # An inline parse would leave the ticker at zero for the whole statement.
    assert ticks > 0


async def test_worker_parse_leaves_the_event_loop_prediction_caches_alone(monkeypatch):
    """The runtime grows its DFA caches without a lock, so threads must not share them."""
    from app.core.config import settings
    from app.sql_dialect.grammar import StarRocksLexer, StarRocksParser
    from app.sql_frontend.parser import parse_statement_async

    def shared_states() -> int:
        caches = (*StarRocksLexer.decisionsToDFA, *StarRocksParser.decisionsToDFA)
        return sum(len(dfa.states) for dfa in caches)

    monkeypatch.setattr(settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 1)
    # Syntax no other test in this module parses, so an inline parse would have
    # to add states to the shared caches.
    sql = (
        "SELECT RANK() OVER (PARTITION BY region ORDER BY total DESC "
        "ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) FROM sales QUALIFY 1 = 1"
    )
    before = shared_states()

    await parse_statement_async(sql)

    assert shared_states() == before
    parse_statement(sql)
    assert shared_states() > before
