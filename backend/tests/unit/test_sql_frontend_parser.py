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
