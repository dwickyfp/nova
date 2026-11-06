"""``MERGE INTO``: grammar, typed AST, validation, lowering and effects.

The pinned engine has no MERGE, so Nova lowers it to a partial-update Primary
Key upsert (``INSERT INTO target (key, written columns) SELECT ... FROM source
LEFT JOIN target``)
and, for ``WHEN MATCHED ... THEN DELETE``, a DELETE that runs first. The
real-engine behaviour (atomic duplicate failure, defaults, Snowflake and
PostgreSQL sources) is covered by the catalog acceptance run; these tests pin the
planning contract without an engine.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.query.repository import QueryResult
from app.sql_frontend.analysis.lineage import statement_lineage
from app.sql_frontend.analysis.semantics import semantics_registry
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.ast.statements import MergeStatement
from app.sql_frontend.binding.catalog import Binder
from app.sql_frontend.binding.models import BoundColumn, BoundTable, TableName
from app.sql_frontend.binding.starrocks import StarRocksCatalogProvider
from app.sql_frontend.context import ExecutionContext, PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import Atomicity, CompositePlan, EngineSqlPlan
from app.sql_frontend.planning.planner import SQLPlanner

ORDERS = BoundTable(
    TableName("orders", "sales"),
    "BASE TABLE",
    columns=(
        BoundColumn("id", "bigint", 1, False),
        BoundColumn("region", "varchar(16)", 2, True),
        BoundColumn("amount", "decimal(12,2)", 3, True),
        BoundColumn("status", "varchar(16)", 4, True, "new", default_sql="'new'"),
        BoundColumn("loaded_at", "datetime", 5, True, "CURRENT_TIMESTAMP",
                    default_sql="CURRENT_TIMESTAMP"),
        BoundColumn("double_amount", "decimal(21,2)", 6, True,
                    generated_expression="amount * 2"),
    ),
    key_type="PRIMARY",
    primary_key_columns=("id",),
)

PAIRS = BoundTable(
    TableName("pairs", "sales"),
    "BASE TABLE",
    columns=(
        BoundColumn("id", "int", 1, False),
        BoundColumn("city", "varchar(32)", 2, False),
        BoundColumn("note", "varchar(32)", 3, True),
    ),
    key_type="PRIMARY",
    primary_key_columns=("id", "city"),
)

UPSERT = (
    "MERGE INTO sales.orders AS t USING sf.SALES.ORDERS AS s ON t.id = s.ORDER_ID "
    "WHEN MATCHED AND s.IS_PAID THEN UPDATE SET status = 'paid', amount = s.AMOUNT "
    "WHEN NOT MATCHED THEN INSERT (id, region, amount) VALUES (s.ORDER_ID, s.REGION, s.AMOUNT)"
)


class Catalog:
    def __init__(self, *tables: BoundTable) -> None:
        self.tables = {(table.name.database, table.name.table): table for table in tables}

    async def resolve_table(self, name):
        table = self.tables.get((name.database, name.table))
        return BoundTable(name, table.table_type) if table else None

    async def get_columns(self, name):
        return self.tables[(name.database, name.table)].columns

    async def get_details(self, name):
        return self.tables[(name.database, name.table)]


def build(sql: str) -> MergeStatement:
    statement = ast_builders.build(parse_statement(sql))
    assert isinstance(statement, MergeStatement)
    return statement


async def plan(sql: str, *tables: BoundTable, database: str | None = "sales"):
    context = PlanningContext(database=database, binder=Binder(Catalog(*(tables or (ORDERS,)))))
    return await SQLPlanner().plan(build(sql), context)


# ── Grammar and AST ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        UPSERT,
        "merge into t using (select 1 as id) s on t.id = s.id "
        "when not matched then insert values (s.id)",
        "MERGE INTO db.t USING pg.public.src ON t.id = src.id WHEN MATCHED THEN DELETE",
        "MERGE INTO t x USING s y ON x.id = y.id WHEN MATCHED THEN UPDATE SET a = DEFAULT",
    ],
)
def test_merge_parses_to_the_typed_statement(sql):
    assert isinstance(ast_builders.build(parse_statement(sql)), MergeStatement)


@pytest.mark.parametrize(
    "sql",
    ["SELECT matched, merge FROM t", "SELECT * FROM matched", "CREATE TABLE t (matched INT)"],
)
def test_matched_and_merge_stay_identifiers(sql):
    assert not isinstance(ast_builders.build(parse_statement(sql)), MergeStatement)


def test_ast_slices_expressions_from_the_source():
    statement = build(UPSERT)
    assert statement.target == ("sales", "orders") and statement.target_alias == "t"
    assert statement.source_sql == "sf.SALES.ORDERS" and statement.source_alias == "s"
    assert statement.keys == (("id", "s.ORDER_ID"),)
    update, insert = statement.clauses
    assert update.kind == "update" and update.condition == "s.IS_PAID"
    assert update.assignments == (("status", "'paid'"), ("amount", "s.AMOUNT"))
    assert insert.columns == ("id", "region", "amount")
    assert insert.values == ("s.ORDER_ID", "s.REGION", "s.AMOUNT")


def test_keys_accept_either_side_and_composite_keys():
    statement = build(
        "MERGE INTO pairs p USING src s ON s.id = p.id AND p.city = UPPER(s.city) "
        "WHEN MATCHED THEN DELETE"
    )
    assert statement.keys == (("id", "s.id"), ("city", "UPPER(s.city)"))


# ── Effects and confirmation ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("sql", "updates", "deletes", "confirm"),
    [
        ("MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT VALUES (s.id)",
         False, False, False),
        (UPSERT, True, False, True),
        ("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE", False, True, True),
    ],
)
def test_effects_follow_the_clauses(sql, updates, deletes, confirm):
    analysis = semantics_registry.analyze(build(sql))
    assert analysis.effects.reads_data and analysis.effects.writes_data
    assert analysis.effects.updates_rows is updates
    assert analysis.effects.deletes_rows is deletes
    assert analysis.requires_confirmation is confirm


# ── Validation without metadata ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("MERGE INTO t USING s ON t.id = s.id AND s.x > 1 WHEN MATCHED THEN DELETE",
         "WHEN ... AND"),
        ("MERGE INTO t USING s ON t.id = s.id OR t.id = s.x WHEN MATCHED THEN DELETE",
         "joined by AND"),
        ("MERGE INTO t USING s ON t.id = t.other WHEN MATCHED THEN DELETE", "primary key"),
        ("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE "
         "WHEN NOT MATCHED THEN INSERT VALUES (s.id)", "two MERGE statements"),
        ("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET a = 1 "
         "WHEN MATCHED AND s.x THEN DELETE", "never reached"),
        ("MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT VALUES (s.id) "
         "WHEN NOT MATCHED AND s.x THEN INSERT VALUES (s.id)", "never reached"),
        ("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET a = 1, A = 2",
         "more than once"),
        ("MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT (id, id) "
         "VALUES (s.id, s.id)", "more than once"),
        ("MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT (id, a) "
         "VALUES (s.id)", "different number"),
        ("MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED AND t.a > 1 THEN "
         "INSERT VALUES (s.id)", "only the source"),
        ("MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT (id, a) "
         "VALUES (s.id, t.a)", "only the source"),
        ("MERGE INTO t USING db.t ON t.id = t.id WHEN MATCHED THEN DELETE", "alias"),
        ("MERGE INTO t USING (SELECT * FROM @stage1/orders.csv) s ON t.id = s.id "
         "WHEN MATCHED THEN DELETE",
         "@stage"),
    ],
)
def test_refusals_before_binding(sql, message):
    with pytest.raises(SemanticError, match=message):
        semantics_registry.validate(build(sql), PlanningContext(database="sales"))


# ── Planning against the target's metadata ──────────────────────────────────


async def test_upsert_is_one_atomic_insert_select():
    result = await plan(UPSERT)
    assert isinstance(result, EngineSqlPlan) and result.requires_confirmation
    sql = result.engine_sql
    # Only the key and the columns a clause writes: a matched row keeps the
    # rest, a new row gets their defaults, generated columns are the engine's.
    assert sql.startswith(
        "INSERT /*+ SET_VAR(enable_insert_partial_update=true) */ INTO sales.orders "
        "(`id`, `region`, `amount`, `status`, `loaded_at`) SELECT "
    )
    assert "double_amount" not in sql
    assert "FROM sf.SALES.ORDERS AS `s` LEFT JOIN sales.orders AS `t` ON t.id = s.ORDER_ID" in sql
    action = (
        "(CASE WHEN `t`.`id` IS NOT NULL THEN CASE WHEN (s.IS_PAID) THEN 1 END "
        "ELSE CASE WHEN TRUE THEN 2 END END)"
    )
    assert sql.endswith(f"WHERE {action} IN (1, 2)")
    # Duplicate source keys abort the statement before anything is written.
    assert (
        "IF(assert_true(COUNT(*) OVER (PARTITION BY s.ORDER_ID) = 1, "
        "'MERGE INTO sales.orders: more than one source row matches the same key'), "
        f"CASE {action} WHEN 2 THEN s.ORDER_ID ELSE `t`.`id` END, NULL)"
    ) in sql
    # Updated columns take the source value, others keep the target's.
    assert f"CASE {action} WHEN 1 THEN 'paid' WHEN 2 THEN 'new' ELSE `t`.`status` END" in sql
    assert f"CASE {action} WHEN 2 THEN s.REGION ELSE `t`.`region` END" in sql
    assert f"CASE {action} WHEN 2 THEN CURRENT_TIMESTAMP ELSE `t`.`loaded_at` END" in sql
    assert f"CASE {action} WHEN 1 THEN s.AMOUNT WHEN 2 THEN s.AMOUNT ELSE `t`.`amount` END" in sql


async def test_insert_only_merge_needs_no_confirmation():
    result = await plan(
        "MERGE INTO orders t USING src s ON t.id = s.id "
        "WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id)"
    )
    assert isinstance(result, EngineSqlPlan) and not result.requires_confirmation
    assert not result.effects.updates_rows and not result.effects.deletes_rows


async def test_insert_without_a_column_list_fills_columns_in_order_and_keys_from_on():
    result = await plan(
        "MERGE INTO orders t USING src s ON t.id = s.id "
        "WHEN NOT MATCHED THEN INSERT VALUES (s.id, s.r, s.a, DEFAULT, NOW())"
    )
    assert "(`id`, `region`, `amount`, `status`, `loaded_at`)" in result.engine_sql
    assert "WHEN 1 THEN 'new' ELSE `t`.`status` END" in result.engine_sql
    assert "WHEN 1 THEN NOW() ELSE `t`.`loaded_at` END" in result.engine_sql


async def test_update_only_merge_never_writes_columns_it_does_not_set():
    result = await plan(
        "MERGE INTO orders t USING src s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET amount = s.amount"
    )
    # loaded_at defaults to CURRENT_TIMESTAMP, which the engine's partial update
    # would reset, so it is written back unchanged.
    assert "INTO orders (`id`, `amount`, `loaded_at`) SELECT" in result.engine_sql
    assert ", `t`.`loaded_at` FROM src" in result.engine_sql
    for column in ("region", "status"):
        assert column not in result.engine_sql


async def test_partial_update_writes_what_a_generated_column_reads():
    """The engine refuses a partial update without a generated column's inputs."""
    result = await plan(
        "MERGE INTO orders t USING src s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET region = s.region"
    )
    assert "INTO orders (`id`, `region`, `amount`, `loaded_at`) SELECT" in result.engine_sql
    assert ", `t`.`amount`, `t`.`loaded_at` FROM src" in result.engine_sql


async def test_delete_clauses_run_a_check_and_a_delete_before_the_upsert():
    result = await plan(
        "MERGE INTO pairs AS p USING (SELECT id, city, gone FROM pg.public.src) AS s "
        "ON p.id = s.id AND p.city = s.city "
        "WHEN MATCHED AND s.gone THEN DELETE WHEN MATCHED THEN UPDATE SET note = 'kept'",
        PAIRS,
    )
    assert isinstance(result, CompositePlan) and result.atomicity is Atomicity.BEST_EFFORT
    check, delete, upsert = result.steps
    assert check.engine_sql.startswith("SELECT assert_true(")
    assert "GROUP BY s.id, s.city" in check.engine_sql
    assert not check.requires_confirmation
    assert delete.engine_sql.startswith(
        "DELETE FROM pairs WHERE (`id`, `city`) IN (SELECT `p`.`id`, `p`.`city` "
    )
    assert delete.engine_sql.endswith("IN (1))") and delete.effects.deletes_rows
    assert "INTO pairs (`id`, `city`, `note`) SELECT" in upsert.engine_sql
    assert upsert.engine_sql.endswith("IN (2)") and upsert.effects.updates_rows


async def test_delete_only_merge_has_no_upsert():
    result = await plan("MERGE INTO orders t USING src s ON t.id = s.id WHEN MATCHED THEN DELETE")
    assert [step.engine_sql.split(" ")[0] for step in result.steps] == ["SELECT", "DELETE"]
    delete = result.steps[1].engine_sql
    assert "WHERE `id` IN (SELECT `t`.`id` FROM src AS `s` JOIN orders AS `t`" in delete


async def test_without_aliases_the_table_names_qualify_columns():
    result = await plan(
        "MERGE INTO sales.orders USING src ON orders.id = src.id "
        "WHEN MATCHED THEN UPDATE SET amount = src.amount"
    )
    assert "FROM src LEFT JOIN sales.orders ON orders.id = src.id" in result.engine_sql
    assert "sales.orders.`amount`" in result.engine_sql


@pytest.mark.parametrize(
    ("sql", "tables", "message"),
    [
        ("MERGE INTO pg.public.orders t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
         (ORDERS,), "external catalog"),
        ("MERGE INTO orders t USING s ON t.region = s.r WHEN MATCHED THEN DELETE",
         (ORDERS,), "region is not one of them"),
        ("MERGE INTO pairs t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
         (PAIRS,), "city is missing"),
        ("MERGE INTO orders t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET id = 2",
         (ORDERS,), "key column"),
        ("MERGE INTO orders t USING s ON t.id = s.id "
         "WHEN MATCHED THEN UPDATE SET double_amount = 2",
         (ORDERS,), "generated"),
        ("MERGE INTO orders t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET nope = 2",
         (ORDERS,), "unknown column"),
        ("MERGE INTO orders t USING s ON t.id = s.id "
         "WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id + 1)",
         (ORDERS,), "matched on"),
        ("MERGE INTO orders t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT VALUES (s.id)",
         (ORDERS,), "1 values for 5 columns"),
        ("MERGE INTO pairs t USING s ON t.id = s.id AND t.city = s.c "
         "WHEN MATCHED THEN UPDATE SET must = 1 "
         "WHEN NOT MATCHED THEN INSERT (id, city) VALUES (s.id, s.c)",
         (BoundTable(TableName("pairs", "sales"), "BASE TABLE",
                     columns=(*PAIRS.columns, BoundColumn("must", "int", 4, False)),
                     key_type="PRIMARY", primary_key_columns=("id", "city")),),
         "NOT NULL"),
        ("MERGE INTO orders t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
         (BoundTable(TableName("orders", "sales"), "BASE TABLE", columns=ORDERS.columns,
                     key_type="DUPLICATE"),), "Primary Key table"),
        ("MERGE INTO orders t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
         (BoundTable(TableName("orders", "sales"), "VIEW", columns=ORDERS.columns,
                     key_type="PRIMARY", primary_key_columns=("id",)),), "Primary Key table"),
    ],
)
async def test_refusals_after_binding(sql, tables, message):
    with pytest.raises(SemanticError, match=message):
        await plan(sql, *tables)


async def test_a_target_needs_a_database():
    with pytest.raises(SemanticError, match="needs a database"):
        await plan("MERGE INTO orders t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
                   database=None)


async def test_auto_increment_column_is_left_to_the_engine_unless_another_clause_sets_it():
    table = BoundTable(
        TableName("orders", "sales"),
        "BASE TABLE",
        columns=(BoundColumn("id", "int", 1, False),
                 BoundColumn("seq", "bigint", 2, False, auto_increment=True)),
        key_type="PRIMARY",
        primary_key_columns=("id",),
    )
    result = await plan("MERGE INTO orders t USING s ON t.id = s.id "
                        "WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id)", table)
    assert "INTO orders (`id`) SELECT" in result.engine_sql
    with pytest.raises(SemanticError, match="auto-increment"):
        await plan("MERGE INTO orders t USING s ON t.id = s.id "
                   "WHEN MATCHED THEN UPDATE SET seq = 1 "
                   "WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id)", table)


# ── Lineage and binding ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("sql", "sources"),
    [
        (UPSERT, ["sf.SALES.ORDERS"]),
        ("MERGE INTO orders t USING "
         "(SELECT a.id FROM pg.public.a a JOIN sales.b b ON a.id = b.id) s "
         "ON t.id = s.id WHEN MATCHED THEN DELETE", ["pg.public.a", "default_catalog.sales.b"]),
    ],
)
def test_merge_has_lineage_from_its_source(sql, sources):
    lineage = statement_lineage(sql, database="sales")
    assert lineage is not None and lineage.kind == "merge"
    assert lineage.target.full_name == "default_catalog.sales.orders"
    assert [source.full_name for source in lineage.sources] == sources


async def test_details_carry_the_column_default_as_sql():
    repo = AsyncMock()
    ddl = (
        "CREATE TABLE t (id INT NOT NULL, s VARCHAR(8) DEFAULT \"new\", "
        "at DATETIME DEFAULT CURRENT_TIMESTAMP, n INT) PRIMARY KEY(id) DISTRIBUTED BY HASH(id)"
    )
    repo.execute_as_user.side_effect = [
        QueryResult(rows=[["t", ddl]]),
        QueryResult(rows=[["id", "INT", 1, "NO", None, ""],
                          ["s", "VARCHAR(8)", 2, "YES", "new", ""],
                          ["at", "DATETIME", 3, "YES", "CURRENT_TIMESTAMP", ""],
                          ["n", "INT", 4, "YES", None, ""]]),
    ]
    provider = StarRocksCatalogProvider(
        repo, ExecutionContext("alice", database="db", role="analyst"), lambda: "pw"
    )
    details = await provider.get_details(TableName("t", "db"))
    assert [column.default_sql for column in details.columns] == [
        None, '"new"', "CURRENT_TIMESTAMP", None,
    ]


async def test_details_carry_a_generated_expression_as_written():
    repo = AsyncMock()
    ddl = (
        "CREATE TABLE t (id INT NOT NULL, amount INT, twice INT NULL AS amount * 2) "
        "PRIMARY KEY(id) DISTRIBUTED BY HASH(id)"
    )
    repo.execute_as_user.side_effect = [
        QueryResult(rows=[["t", ddl]]),
        QueryResult(rows=[["id", "INT", 1, "NO", None, ""], ["amount", "INT", 2, "YES", None, ""],
                          ["twice", "INT", 3, "YES", None, ""]]),
    ]
    provider = StarRocksCatalogProvider(
        repo, ExecutionContext("alice", database="db", role="analyst"), lambda: "pw"
    )
    details = await provider.get_details(TableName("t", "db"))
    assert details.columns[2].generated_expression == "amount * 2"
