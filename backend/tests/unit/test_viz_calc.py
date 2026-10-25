"""The calculation language: parsing, checking, lowering and compilation.

Engine semantics (that each layered query returns the right numbers) are
verified against StarRocks separately; these tests pin the contracts the
editor and the compiler rely on: positions on errors, evaluation levels,
the order of operations, and that user text never reaches SQL unquoted.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.viz.calc import CalcError, analyze_calculations, catalog, check_formula, parse
from app.modules.viz.calc.lower import Lowerer, infer_types, window_sql
from app.modules.viz.calc.syntax import Call, FieldRef, Literal_, Lod, tokenize
from app.modules.viz.compiler import (
    CompileError,
    compile_worksheet,
    infer_column_types,
    source_probe_sql,
)
from app.modules.viz.presmodel import PresModelError
from app.modules.viz.presmodel import build as build_presmodel
from app.modules.viz.service import SourceSchema, data_pane, viz_service
from app.modules.viz.spec import WorksheetSpec

COLUMNS = {
    "region": "string",
    "state": "string",
    "customer": "string",
    "order_date": "date",
    "sales": "number",
    "profit": "number",
}

USER = {
    "username": "analyst",
    "encrypted_password": "ciphertext",
    "session_id": "s1",
    "roles": ["analyst"],
    "active_role": "analyst",
}


def sheet(**body) -> WorksheetSpec:
    payload = {"source": {"kind": "sql", "sql": "SELECT * FROM sales"}, "marks": {"type": "bar"}}
    payload.update(body)
    return WorksheetSpec.model_validate(payload)


def pill(field: str, aggregation: str | None = None, **extra) -> dict:
    return {"field": field, "aggregation": aggregation, "discrete": aggregation is None, **extra}


def compile_sql(spec: WorksheetSpec) -> str:
    return compile_worksheet(spec, known_columns=list(COLUMNS), column_types=COLUMNS).sql


def lower(formula: str, **callbacks) -> str:
    calcs = analyze_calculations(COLUMNS, {"f": formula})
    node = calcs["f"].expanded
    types = infer_types(node, COLUMNS)
    return Lowerer(types=types, column=lambda name: f"`{name}`", **callbacks).sql(node)


# ── Syntax ─────────────────────────────────────────────────────


def test_tokens_cover_fields_literals_dates_and_comments() -> None:
    tokens = tokenize("[Order Date] >= #2024-01-31# // trailing\n AND 'It''s' /* x */")
    kinds = [(t.kind, t.value) for t in tokens]
    assert kinds == [
        ("field", "Order Date"),
        ("op", ">="),
        ("date", "2024-01-31"),
        ("ident", "AND"),
        ("string", "It's"),
        ("eof", ""),
    ]
    assert tokenize("[a]]b]")[0].value == "a]b"


def test_parser_builds_if_case_lod_and_precedence() -> None:
    tree = parse("1 + 2 * 3 ^ 2")
    assert tree == parse("1 + (2 * (3 ^ 2))")
    assert parse("-2 ^ 2") == parse("-(2 ^ 2)")
    node = parse("IF [sales] > 10 THEN 'a' ELSEIF [sales] > 5 THEN 'b' ELSE 'c' END")
    assert len(node.branches) == 2 and node.otherwise is not None
    lod = parse("{ FIXED [region], [state] : SUM([sales]) }")
    assert isinstance(lod, Lod) and lod.kind == "fixed" and len(lod.dims) == 2
    assert parse("{SUM([sales])}").kind == "table"
    assert parse("{FIXED : SUM([sales])}").dims == ()
    case = parse("CASE [region] WHEN 'East' THEN 1 ELSE 0 END")
    assert len(case.whens) == 1
    assert parse("[region] IN ('East', 'West')").options[1] == Literal_("string", "West")
    assert parse("sales") == FieldRef("sales")
    assert parse("sum([sales])") == Call("SUM", (FieldRef("sales"),))


@pytest.mark.parametrize(
    ("formula", "message", "span"),
    [
        ("", "empty", None),
        ("SUM([sales]", "close SUM", (11, 11)),
        ("[sales] +", "formula ended", None),
        ("'open", "never closed", (0, 5)),
        ("#2024/01/01#", "YYYY-MM-DD", (0, 12)),
        ("1 < 2 < 3", "chained", (6, 7)),
        ("IF 1 THEN 2", "END", None),
        ("CASE [a] ELSE 1 END", "WHEN", None),
        ("{FIXED [a] SUM([b])}", "':'", None),
        ("[sales] @ 2", "Unexpected character", (8, 9)),
        ("__COUNT_ROWS()", "reserved", (0, 12)),
        ("[]", "empty", (0, 2)),
    ],
)
def test_syntax_errors_name_the_problem_and_its_position(formula, message, span) -> None:
    with pytest.raises(CalcError, match=message) as info:
        parse(formula)
    if span is not None:
        assert (info.value.start, info.value.end) == span


def test_formulas_are_bounded() -> None:
    with pytest.raises(CalcError, match="at most"):
        parse("1" * 9000)
    with pytest.raises(CalcError, match="nested too deeply"):
        parse("(" * 80 + "1" + ")" * 80)


# ── Analyzer ───────────────────────────────────────────────────


def test_levels_and_types_are_inferred() -> None:
    calcs = analyze_calculations(
        COLUMNS,
        {
            "Margin": "SUM([profit]) / SUM([sales])",
            "Big": "[sales] > 100",
            "Label": "UPPER([region]) + '-' + [state]",
            "Run": "RUNNING_SUM([Total])",
            "Total": "SUM([sales])",
            "First": "{FIXED [customer] : MIN([order_date])}",
            "Shifted": "[order_date] + 7",
            "Age": "#2026-01-01# - [order_date]",
        },
    )
    assert (calcs["Margin"].level, calcs["Margin"].data_type, calcs["Margin"].role) == (
        "aggregate",
        "number",
        "measure",
    )
    assert (calcs["Big"].level, calcs["Big"].data_type, calcs["Big"].role) == (
        "row",
        "boolean",
        "dimension",
    )
    assert calcs["Label"].data_type == "string"
    assert calcs["Run"].level == "table"
    assert calcs["Run"].references == ["Total"]
    assert (calcs["First"].level, calcs["First"].data_type) == ("row", "date")
    assert calcs["Shifted"].data_type == "date"
    assert calcs["Age"].data_type == "number"


@pytest.mark.parametrize(
    ("formula", "message"),
    [
        ("[sales] + SUM([sales])", "Cannot mix aggregate and non-aggregate"),
        ("SUM(SUM([sales]))", "already aggregated"),
        ("RUNNING_SUM([sales])", "works on aggregated values"),
        ("{FIXED [region] : [sales]}", "must be an aggregate"),
        ("{FIXED [region] : SUM({INCLUDE [state] : SUM([sales])})}", "cannot appear inside FIXED"),
        ("{FIXED {FIXED [region] : MIN([state])} : SUM([sales])}", "cannot itself be"),
        ("[region] + 1", "Cannot add text and a number"),
        ("[region] * 2", "needs numbers"),
        ("SUM([region])", "expects a number"),
        ("IF [sales] THEN 1 END", "must be true/false"),
        ("IF [sales] > 1 THEN 'a' ELSE 2 END", "same type"),
        ("[sales] = 'x'", "Cannot compare"),
        ("NOPE(1)", "Unknown function NOPE"),
        ("LEFT([region])", "LEFT is written"),
        ("DATEPART(month, [order_date])", "quoted text"),
        ("DATEPART('fortnight', [order_date])", "accepts"),
        ("LOOKUP(SUM([sales]), [sales])", "whole-number constant"),
        ("RANK(SUM([sales]), 'up')", "'asc' or 'desc'"),
        ("[ghost] + 1", "does not exist"),
        ("TOTAL(RUNNING_SUM(SUM([sales])))", "not another table calculation"),
        ("PERCENTILE([sales], [profit])", "constant between 0 and 1"),
    ],
)
def test_semantic_errors_explain_the_rule(formula, message) -> None:
    with pytest.raises(CalcError, match=message):
        analyze_calculations(COLUMNS, {"f": formula})


def test_error_positions_point_into_the_formula() -> None:
    with pytest.raises(CalcError) as info:
        check_formula("1 + [ghost]", COLUMNS, {})
    assert (info.value.start, info.value.end) == (4, 11)


def test_cycles_and_name_clashes_are_rejected() -> None:
    with pytest.raises(CalcError, match="cycle"):
        analyze_calculations(COLUMNS, {"a": "[b] + 1", "b": "[a] + 1"})
    with pytest.raises(CalcError, match="same name as a column"):
        analyze_calculations(COLUMNS, {"sales": "1"})
    with pytest.raises(CalcError, match="cannot start with"):
        analyze_calculations(COLUMNS, {"__nova_x": "1"})


def test_check_formula_replaces_the_calculation_being_edited() -> None:
    saved = {"Margin": "SUM([profit]) / SUM([sales])", "Uses": "[Margin] * 100"}
    info = check_formula("SUM([profit])", COLUMNS, saved, name="Margin")
    assert info.level == "aggregate"
    with pytest.raises(CalcError, match="cycle"):
        check_formula("[Uses] + 1", COLUMNS, saved, name="Margin")
    with pytest.raises(CalcError, match="already called"):
        check_formula("1", COLUMNS, saved, name="sales")


def test_field_names_resolve_case_insensitively() -> None:
    calcs = analyze_calculations(COLUMNS, {"f": "SUM([SALES])"})
    assert calcs["f"].references == ["sales"]


def test_catalog_documents_functions_and_lods() -> None:
    entries = {entry["name"]: entry for entry in catalog()}
    assert entries["RUNNING_SUM"]["kind"] == "table"
    assert entries["COUNTD"]["kind"] == "aggregate"
    assert entries["FIXED"]["kind"] == "lod"
    assert all(entry["description"] and entry["syntax"] for entry in entries.values())


# ── Lowering ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("formula", "sql"),
    [
        ("[region] + '-' + [state]", "CONCAT(CONCAT(`region`, '-'), `state`)"),
        ("[order_date] + 7", "TIMESTAMPADD(DAY, 7, `order_date`)"),
        ("[order_date] - 2", "TIMESTAMPADD(DAY, -(2), `order_date`)"),
        ("#2026-01-01# - [order_date]", "DATEDIFF(CAST('2026-01-01' AS DATE), `order_date`)"),
        ("[sales] ^ 2", "POWER(`sales`, 2)"),
        ("ZN([profit])", "COALESCE(`profit`, 0)"),
        (
            "IIF([sales] > 1, 'a', 'b')",
            "(CASE WHEN (`sales` > 1) THEN 'a' WHEN NOT ((`sales` > 1)) THEN 'b' ELSE NULL END)",
        ),
        ("CONTAINS([region], 'as')", "(LOCATE('as', `region`) > 0)"),
        ("SPLIT([region], '-', 2)", "SPLIT_PART(`region`, '-', 2)"),
        ("DATEPART('weekday', [order_date])", "DAYOFWEEK(`order_date`)"),
        ("DATENAME('month', [order_date])", "MONTHNAME(`order_date`)"),
        ("DATETRUNC('quarter', [order_date])", "DATE_TRUNC('quarter', `order_date`)"),
        ("DATEADD('month', 3, [order_date])", "TIMESTAMPADD(MONTH, 3, `order_date`)"),
        (
            "DATEDIFF('year', #2023-12-31#, [order_date])",
            "TIMESTAMPDIFF(YEAR, DATE_TRUNC('year', CAST('2023-12-31' AS DATE)), "
            "DATE_TRUNC('year', `order_date`))",
        ),
        (
            "DATEDIFF('quarter', [order_date], TODAY())",
            "(TIMESTAMPDIFF(MONTH, DATE_TRUNC('quarter', `order_date`), DATE_TRUNC('quarter', "
            "CURRENT_DATE())) DIV 3)",
        ),
        (
            "MAKEDATE(2024, 2, 9)",
            "STR_TO_DATE(CONCAT(CAST(2024 AS BIGINT), '-', CAST(2 AS BIGINT), '-', CAST(9 AS "
            "BIGINT)), '%Y-%m-%d')",
        ),
        ("INT([region])", "CAST(CAST(`region` AS DOUBLE) AS BIGINT)"),
        ("MAX([sales], [profit])", "GREATEST(`sales`, `profit`)"),
        ("MEDIAN([sales])", "PERCENTILE_CONT(CAST(`sales` AS DOUBLE), 0.5)"),
        ("COUNTD([customer])", "COUNT(DISTINCT `customer`)"),
        ("ATTR([region])", "(CASE WHEN MIN(`region`) = MAX(`region`) THEN MIN(`region`) END)"),
        ("LOG([sales], 2)", "(LN(`sales`) / LN(2))"),
        ("[region] IN ('a', 'b')", "(`region` IN ('a', 'b'))"),
        ("CASE [region] WHEN 'a' THEN 1 END", "(CASE `region` WHEN 'a' THEN 1 END)"),
        ("NOT ISNULL([sales]) AND TRUE", "((NOT (`sales` IS NULL)) AND TRUE)"),
        ("#2024-01-02 10:00:00#", "CAST('2024-01-02 10:00:00' AS DATETIME)"),
    ],
)
def test_lowering_uses_engine_builtins(formula, sql) -> None:
    assert lower(formula) == sql


def test_text_literals_cannot_escape_their_quotes() -> None:
    # A trailing backslash would escape the closing quote if left alone.
    assert lower("[region] = 'a\\'") == "(`region` = 'a\\\\')"
    assert lower("[region] = 'it''s'") == "(`region` = 'it''s')"


def test_window_functions_follow_partition_and_addressing() -> None:
    node = parse("RUNNING_SUM(SUM([sales]))")
    assert window_sql(node, ["`a`"], partition=["`p`"], order=["`o`"]) == (
        "SUM(`a`) OVER (PARTITION BY `p` ORDER BY `o` ROWS BETWEEN UNBOUNDED PRECEDING AND "
        "CURRENT ROW)"
    )
    assert window_sql(node, ["`a`"], partition=[], order=[]) == "SUM(`a`) OVER ()"
    window = parse("WINDOW_AVG(SUM([sales]), -2, 0)")
    assert window_sql(window, ["`a`", "-2", "0"], partition=[], order=["`o`"]) == (
        "AVG(`a`) OVER (ORDER BY `o` ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)"
    )
    with pytest.raises(CalcError, match="first <= last"):
        window_sql(parse("WINDOW_SUM(SUM([sales]), 1, -1)"), ["`a`"], partition=[], order=["`o`"])
    lookup = parse("LOOKUP(SUM([sales]), 2)")
    assert (
        window_sql(lookup, ["`a`", "2"], partition=[], order=["`o`"])
        == "LEAD(`a`, 2) OVER (ORDER BY `o`)"
    )
    assert window_sql(lookup, ["`a`", "2"], partition=[], order=[]) == "NULL"
    assert (
        window_sql(parse("LOOKUP(SUM([sales]), 0)"), ["`a`", "0"], partition=[], order=[]) == "`a`"
    )
    rank = parse("RANK_PERCENTILE(SUM([sales]))")
    assert window_sql(rank, ["`a`"], partition=["`p`"], order=[]) == (
        "PERCENT_RANK() OVER (PARTITION BY `p` ORDER BY `a` ASC)"
    )
    assert window_sql(parse("LAST()"), [], partition=[], order=["`o`"]) == (
        "(COUNT(*) OVER () - ROW_NUMBER() OVER (ORDER BY `o`))"
    )
    assert window_sql(parse("FIRST()"), [], partition=[], order=["`o`"]) == (
        "(1 - ROW_NUMBER() OVER (ORDER BY `o`))"
    )
    assert (
        window_sql(parse("SIZE()"), [], partition=["`p`"], order=[])
        == "COUNT(*) OVER (PARTITION BY `p`)"
    )
    assert window_sql(parse("INDEX()"), [], partition=[], order=[]) == "ROW_NUMBER() OVER ()"


def test_table_calculations_need_a_view() -> None:
    with pytest.raises(CalcError, match="only available on a view"):
        lower("RUNNING_SUM(SUM([sales]))")


# ── Compiler ───────────────────────────────────────────────────


def test_a_plain_view_with_calculations_stays_one_select() -> None:
    spec = sheet(
        columns=[pill("Tier"), pill("order_date", date_part="year")],
        rows=[pill("Margin", "agg")],
        calculations=[
            {"name": "Tier", "formula": "IF [sales] >= 100 THEN 'Big' ELSE 'Small' END"},
            {"name": "Margin", "formula": "SUM([profit]) / SUM([sales])"},
        ],
    )
    compiled = compile_worksheet(spec, known_columns=list(COLUMNS), column_types=COLUMNS)
    assert compiled.sql == (
        "SELECT (CASE WHEN (`sales` >= 100) THEN 'Big' ELSE 'Small' END) AS `Tier`, "
        "YEAR(`order_date`) AS `year(order_date)`, (SUM(`profit`) / SUM(`sales`)) AS `Margin` "
        "FROM (SELECT * FROM sales) AS nova_source "
        "GROUP BY (CASE WHEN (`sales` >= 100) THEN 'Big' ELSE 'Small' END), YEAR(`order_date`)"
    )
    resolved = compiled.spec
    assert resolved is not None
    assert resolved.rows[0].aggregation == "agg"
    assert resolved.columns[1].field_type == "ordinal"
    assert [f.label for f in compiled.output_fields] == ["Tier", "year(order_date)", "Margin"]
    assert compiled.ordered is False


def test_continuous_dates_truncate_and_pills_can_use_any_aggregation() -> None:
    spec = sheet(
        columns=[pill("order_date", date_part="month", discrete=False)],
        rows=[pill("sales", "median"), pill("customer", "distinct_count"), pill("profit", "stdev")],
    )
    sql = compile_sql(spec)
    assert "DATE_TRUNC('month', `order_date`) AS `month_trunc(order_date)`" in sql
    assert "PERCENTILE_CONT(CAST(`sales` AS DOUBLE), 0.5) AS `median(sales)`" in sql
    assert "COUNT(DISTINCT `customer`) AS `distinct_count(customer)`" in sql
    assert "STDDEV_SAMP(`profit`) AS `stdev(profit)`" in sql


def test_fixed_runs_after_context_and_before_dimension_filters() -> None:
    spec = sheet(
        columns=[pill("region")],
        rows=[pill("RegionSales", "max")],
        calculations=[{"name": "RegionSales", "formula": "{FIXED [region] : SUM([sales])}"}],
        filters=[
            {"kind": "categorical", "field": "state", "values": ["NY"], "context": True},
            {"kind": "categorical", "field": "customer", "values": ["c1"]},
        ],
    )
    sql = compile_sql(spec)
    context = sql.index("nova_context AS (SELECT * FROM nova_source WHERE (`state` IN ('NY')))")
    fixed = sql.index(
        "nova_lod_0 AS (SELECT `region` AS `__nova_k0`, SUM(`sales`) AS `__nova_v` FROM "
        "nova_context GROUP BY `region`)"
    )
    joined = sql.index(
        "nova_rows_0 AS (SELECT nova_context.*, nova_lod_0.`__nova_v` AS `__nova_lod_0` FROM "
        "nova_context LEFT JOIN nova_lod_0 ON `region` <=> nova_lod_0.`__nova_k0`)"
    )
    dimension = sql.index(
        "nova_filtered AS (SELECT * FROM nova_rows_0 WHERE (`customer` IN ('c1')))"
    )
    assert context < fixed < joined < dimension
    # MAX over a pure LOD reads each region's value once.
    assert (
        "SELECT DISTINCT `region` AS `__nova_g0`, `__nova_lod_0` AS `__nova_v` FROM nova_filtered"
        in sql
    )
    assert "MAX(`__nova_v`) AS `__nova_v`" in sql


def test_include_and_exclude_follow_the_view_dimensions() -> None:
    spec = sheet(
        columns=[pill("region"), pill("order_date", date_part="year")],
        rows=[pill("PerCustomer", "agg"), pill("Share", "agg")],
        calculations=[
            {"name": "PerCustomer", "formula": "AVG({INCLUDE [customer] : SUM([sales])})"},
            {
                "name": "Share",
                "formula": "SUM([sales]) / SUM({EXCLUDE [order_date] : SUM([sales])})",
            },
        ],
        filters=[{"kind": "categorical", "field": "state", "values": ["NY"]}],
    )
    sql = compile_sql(spec)
    include = (
        "nova_lod_0 AS (SELECT `region` AS `__nova_k0`, YEAR(`order_date`) AS `__nova_k1`, "
        "`customer` AS `__nova_k2`, SUM(`sales`) AS `__nova_v` FROM nova_filtered"
    )
    # EXCLUDE [order_date] drops YEAR([order_date]) from the view's level.
    exclude = (
        "nova_lod_1 AS (SELECT `region` AS `__nova_k0`, SUM(`sales`) AS `__nova_v` "
        "FROM nova_rows_0 GROUP BY `region`)"
    )
    assert include in sql
    assert exclude in sql
    # Pure LOD aggregates count each LOD group once, not once per row.
    assert (
        "SELECT DISTINCT `region` AS `__nova_g0`, YEAR(`order_date`) AS `__nova_g1`, `customer` "
        "AS `__nova_g2`" in sql
    )


def test_table_calculations_partition_by_unaddressed_dimensions() -> None:
    spec = sheet(
        columns=[pill("region"), pill("state")],
        rows=[pill("Rank", "agg"), pill("Pct", "agg")],
        calculations=[
            {"name": "Rank", "formula": "RANK(SUM([sales]))", "addressing": ["state"]},
            {"name": "Pct", "formula": "SUM([sales]) / TOTAL(SUM([sales]))"},
        ],
    )
    compiled = compile_worksheet(spec, known_columns=list(COLUMNS), column_types=COLUMNS)
    assert "RANK() OVER (PARTITION BY `__nova_d0` ORDER BY `__nova_a0` DESC)" in compiled.sql
    assert "SUM(`__nova_a0`) OVER () AS `__nova_t1`" in compiled.sql
    assert compiled.sql.endswith("ORDER BY `region`, `state`")
    assert compiled.ordered is True


def test_nested_table_calculations_compute_in_layers() -> None:
    spec = sheet(
        columns=[pill("order_date", date_part="month")],
        rows=[pill("Smooth", "agg")],
        calculations=[
            {"name": "Run", "formula": "RUNNING_SUM(SUM([sales]))"},
            {"name": "Smooth", "formula": "WINDOW_AVG([Run], -1, 0)"},
        ],
    )
    sql = compile_sql(spec)
    assert "nova_table_1 AS (SELECT nova_view.*, SUM(`__nova_a0`) OVER (ORDER BY `__nova_d0`" in sql
    assert (
        "nova_table_2 AS (SELECT nova_table_1.*, AVG(`__nova_t0`) OVER (ORDER BY `__nova_d0` ROWS "
        "BETWEEN 1 PRECEDING AND CURRENT ROW)" in sql
    )


def test_total_of_an_average_reaggregates_sums_and_counts() -> None:
    spec = sheet(
        columns=[pill("region")],
        rows=[pill("T", "agg")],
        calculations=[{"name": "T", "formula": "TOTAL(AVG([sales]))"}],
    )
    sql = compile_sql(spec)
    assert "(SUM(`__nova_a1`) OVER () / NULLIF(SUM(`__nova_a2`) OVER (), 0))" in sql
    with pytest.raises(CompileError, match="TOTAL supports"):
        compile_sql(
            sheet(
                columns=[pill("region")],
                rows=[pill("T", "agg")],
                calculations=[{"name": "T", "formula": "TOTAL(COUNTD([customer]))"}],
            )
        )


def test_measure_filters_apply_after_aggregation() -> None:
    spec = sheet(
        columns=[pill("state")],
        rows=[pill("sales", "sum")],
        filters=[
            {"kind": "quantitative", "field": "sales", "aggregation": "sum", "min": 150},
            {"kind": "quantitative", "field": "Margin", "max": 0.5},
        ],
        calculations=[{"name": "Margin", "formula": "SUM([profit]) / SUM([sales])"}],
    )
    sql = compile_sql(spec)
    assert (
        "nova_measures AS (SELECT * FROM nova_view WHERE `__nova_a0` >= 150.0 AND (`__nova_a1` / "
        "`__nova_a0`) <= 0.5)" in sql
    )


def test_compiler_rejects_misplaced_calculations() -> None:
    calcs = [
        {"name": "Margin", "formula": "SUM([profit]) / SUM([sales])"},
        {"name": "Run", "formula": "RUNNING_SUM(SUM([sales]))"},
        {"name": "Share", "formula": "SUM({INCLUDE [customer] : SUM([sales])})"},
        {"name": "First", "formula": "{FIXED [customer] : MIN([order_date])}"},
    ]
    cases = [
        (dict(columns=[pill("Margin")]), "can only be used as a measure"),
        (dict(columns=[pill("region")], rows=[pill("region", "sum")]), "needs a number"),
        (dict(columns=[pill("region")], rows=[pill("sales", "agg")]), "not an aggregate"),
        (
            dict(
                columns=[pill("region")],
                filters=[{"kind": "quantitative", "field": "Run", "min": 1}],
            ),
            "table calculation",
        ),
        (
            dict(
                columns=[pill("region")],
                filters=[{"kind": "categorical", "field": "Margin", "values": ["1"]}],
            ),
            "needs a range filter",
        ),
        (
            dict(
                columns=[pill("region")],
                filters=[
                    {
                        "kind": "relative_date",
                        "field": "First",
                        "range": "year_to_date",
                        "context": True,
                    }
                ],
            ),
            "cannot be a context filter",
        ),
        (dict(columns=[pill("ghost")]), "not a column"),
        (dict(columns=[pill("sales", date_part="year")]), "not a date"),
    ]
    for body, message in cases:
        with pytest.raises(CompileError, match=message):
            compile_sql(sheet(calculations=calcs, **body))


def test_calculation_errors_name_the_field() -> None:
    with pytest.raises(CompileError, match=r"\[Bad\]: Unknown function NOPE"):
        compile_sql(
            sheet(columns=[pill("region")], calculations=[{"name": "Bad", "formula": "NOPE()"}])
        )


def test_reserved_column_names_are_rejected() -> None:
    with pytest.raises(CompileError, match="cannot start with"):
        compile_worksheet(sheet(columns=[pill("__nova_d0")]), known_columns=["__nova_d0"])


def test_without_known_columns_formula_fields_are_still_quoted() -> None:
    spec = sheet(
        columns=[pill("odd`name")],
        rows=[pill("M", "agg")],
        calculations=[{"name": "M", "formula": "SUM([x`y])"}],
    )
    sql = compile_worksheet(spec).sql
    assert "`odd``name`" in sql and "SUM(`x``y`)" in sql


def test_calculation_names_are_unique() -> None:
    with pytest.raises(ValueError, match="unique"):
        sheet(
            columns=[pill("region")],
            calculations=[{"name": "a", "formula": "1"}, {"name": "a", "formula": "2"}],
        )


def test_column_types_are_inferred_from_samples() -> None:
    from datetime import date, datetime
    from decimal import Decimal

    types = infer_column_types(
        ["a", "b", "c", "d", "e", "f", "g"],
        [
            [None, 1, Decimal("2"), date(2024, 1, 1), datetime(2024, 1, 1), True, "x"],
            [1, 2, 3, 4, 5, 6, 7],
        ],
    )
    assert types == {
        "a": "number",
        "b": "number",
        "c": "number",
        "d": "date",
        "e": "datetime",
        "f": "boolean",
        "g": "string",
    }
    assert source_probe_sql(sheet(columns=[pill("region")]), rows=100).endswith("LIMIT 100")


# ── Presentation model ─────────────────────────────────────────


def test_presmodel_derives_date_levels_in_memory() -> None:
    spec = sheet(
        columns=[pill("order_date", date_part="month", field_type="ordinal")],
        rows=[pill("sales", "median")],
    )
    model = build_presmodel(
        spec,
        ["order_date", "sales"],
        [["2024-01-03", 1], ["2024-01-20", 3], ["2024-02-01", 10], [None, 4]],
    )
    assert [column["name"] for column in model["columns"]] == ["month(order_date)", "median(sales)"]
    assert model["rows"] == [[None, 4.0], [1, 2.0], [2, 10.0]]
    assert model["axes"]["x"]["field"] == "month(order_date)"
    assert "median(sales)" not in model["totals"]

    weekly = build_presmodel(
        sheet(
            columns=[pill("order_date", date_part="week", discrete=False)],
            rows=[pill("sales", "sum")],
        ),
        ["order_date", "sales"],
        [["2024-01-03", 1], ["2024-01-04", 2]],
    )
    assert weekly["rows"] == [["2024-01-01", 3.0]]


def test_presmodel_keeps_engine_order_and_engine_table_calcs() -> None:
    spec = sheet(columns=[pill("region")], rows=[pill("Run", "agg")])
    model = build_presmodel(spec, ["region", "Run"], [["B", 5], ["A", 9]], preserve_order=True)
    assert model["rows"] == [["B", 5], ["A", 9]]
    assert model["totals"] == {}
    with pytest.raises(PresModelError, match="engine"):
        build_presmodel(spec, ["region", "sales"], [["A", 1]])


def test_presmodel_statistics_aggregations() -> None:
    spec = sheet(
        columns=[pill("region")],
        rows=[pill("sales", "stdev"), pill("sales", "varp"), pill("sales", "attr")],
    )
    model = build_presmodel(spec, ["region", "sales"], [["A", 1], ["A", 3]])
    stdev, varp, attr = model["rows"][0][1:]
    assert round(stdev, 4) == 1.4142 and varp == 1.0 and attr is None


# ── Service and routes ─────────────────────────────────────────


def _result(columns, rows):
    return SimpleNamespace(
        success=True, error=None, columns=columns, rows=rows, row_count=len(rows), elapsed_ms=1.0
    )


@pytest.mark.asyncio
async def test_compute_returns_the_data_pane_and_resolved_spec(monkeypatch) -> None:
    calls: list[str] = []

    async def execute(sql, user, *, max_rows=500):
        calls.append(sql)
        if "LIMIT 100" in sql:
            return _result(["region", "sales"], [["East", 10.5]])
        return _result(["region", "Margin"], [["East", 0.25]])

    monkeypatch.setattr(viz_service, "_execute", execute)
    spec = sheet(
        columns=[pill("region")],
        rows=[pill("Margin", "agg")],
        calculations=[{"name": "Margin", "formula": "SUM([sales]) / 2"}],
    )
    computed = await viz_service.compute(spec, USER)
    assert computed.presmodel["rows"] == [["East", 0.25]]
    names = {(f["name"], f["kind"], f["role"]) for f in computed.fields}
    assert ("region", "column", "dimension") in names
    assert ("sales", "column", "measure") in names
    assert ("Margin", "calculation", "measure") in names
    assert computed.spec["rows"][0]["aggregation"] == "agg"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_calculation_check_reports_types_or_positions(monkeypatch) -> None:
    monkeypatch.setattr(
        viz_service,
        "_execute",
        AsyncMock(return_value=_result(["region", "sales"], [["East", 1]])),
    )
    spec = sheet(columns=[pill("region")])
    ok = await viz_service.check_calculation(spec, USER, formula="SUM([sales]) * 2")
    assert ok == {
        "valid": True,
        "data_type": "number",
        "level": "aggregate",
        "role": "measure",
        "references": ["sales"],
    }
    bad = await viz_service.check_calculation(spec, USER, formula="SUM([region])")
    assert bad["valid"] is False and bad["error"]["start"] == 4


@pytest.mark.asyncio
async def test_field_domain_lists_members_or_bounds(monkeypatch) -> None:
    calls: list[str] = []

    async def execute(sql, user, *, max_rows=500):
        calls.append(sql)
        if "LIMIT 100" in sql:
            return _result(["region", "sales"], [["East", 1]])
        if "MIN(`sales`)" in sql:
            return _result(["min(sales)", "max(sales)"], [[1, 99]])
        return _result(["region"], [["East"], ["West"]])

    monkeypatch.setattr(viz_service, "_execute", execute)
    spec = sheet(
        columns=[pill("region")],
        filters=[
            {"kind": "categorical", "field": "region", "values": ["East"], "context": True},
            {"kind": "categorical", "field": "region", "values": ["West"]},
        ],
    )
    members = await viz_service.field_domain(spec, USER, field_name="region")
    assert members["values"] == ["East", "West"] and members["truncated"] is False
    # Only context filters narrow the member list.
    assert "WHERE (`region` IN ('East'))" in calls[1] and "'West'" not in calls[1]
    bounds = await viz_service.field_domain(spec, USER, field_name="sales")
    assert (bounds["min"], bounds["max"]) == (1, 99)


@pytest.mark.asyncio
async def test_authoring_routes_validate_their_input(monkeypatch) -> None:
    from app.modules.agents.studio_router import (
        check_viz_calculation,
        describe_viz_source,
        list_viz_functions,
        viz_field_domain,
    )
    from app.modules.agents.studio_schemas import (
        VizCalcCheckRequest,
        VizFieldDomainRequest,
        VizSourceRequest,
    )

    monkeypatch.setattr(
        viz_service,
        "_execute",
        AsyncMock(return_value=_result(["region", "sales"], [["East", 1]])),
    )
    source = {"kind": "sql", "sql": "SELECT * FROM sales"}
    described = await describe_viz_source(VizSourceRequest(source=source), user=USER)
    assert [field["name"] for field in described["fields"]] == ["region", "sales"]
    checked = await check_viz_calculation(
        VizCalcCheckRequest(source=source, formula="[sales] + 1"), user=USER
    )
    assert checked["level"] == "row"
    with pytest.raises(HTTPException) as info:
        await describe_viz_source(
            VizSourceRequest(
                source=source,
                calculations=[{"name": "a", "formula": "1"}, {"name": "a", "formula": "2"}],
            ),
            user=USER,
        )
    assert info.value.status_code == 422
    with pytest.raises(HTTPException):
        await viz_field_domain(
            VizFieldDomainRequest(source={"kind": "sql", "sql": "SELECT 1; SELECT 2"}, field="a"),
            user=USER,
        )
    functions = await list_viz_functions(user=USER)
    assert any(entry["name"] == "LOOKUP" for entry in functions["functions"])


def test_data_pane_lists_calculations_with_their_role() -> None:
    spec = sheet(
        columns=[pill("region")],
        calculations=[{"name": "Big", "formula": "[sales] > 1", "addressing": None}],
    )
    calcs = analyze_calculations(
        {"region": "string", "sales": "number"}, spec.calculation_formulas()
    )
    pane = data_pane(
        SourceSchema(["region", "sales"], {"region": "string", "sales": "number"}), spec, calcs
    )
    assert pane[-1] == {
        "name": "Big",
        "kind": "calculation",
        "data_type": "boolean",
        "role": "dimension",
        "level": "row",
        "formula": "[sales] > 1",
        "addressing": None,
    }


def test_presmodel_orders_category_lines_by_name() -> None:
    spec = sheet(columns=[pill("region")], rows=[pill("sales", "sum")], marks={"type": "line"})
    model = build_presmodel(
        spec, ["region", "sum(sales)"], [["West", 1], ["East", 5], ["North", 2]]
    )
    assert [row[0] for row in model["rows"]] == ["East", "North", "West"]
