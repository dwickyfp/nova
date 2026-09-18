"""NOVA-89 — DDL allow-list + delegate-first regression tests.

The vulnerability: the DDL routers built engine SQL by f-string from caller
input and executed it on the **root** pool. ``guard_sql`` is not a parser, so
a backtick in an identifier or a column type such as
``INT) ENGINE=OLAP; CREATE USER `pwn` ...`` reached the root connection — a
vertical escalation from any authenticated user to root.

These tests are the acceptance bar for the fix:

* **AC1** — every interpolated identifier is validated against an allow-list
  before any SQL is built; a backtick/quote payload is rejected before the
  engine, per DDL route.
* **AC2** — the confirmed payload is refused, and the test *fails on the
  pre-change tree* (the pre-change tree had no validation at all).
* **AC3** — column types and distribution/partition clauses are mapped through
  an allow-list, never passed as raw fragments.
* **AC4/AC5** — object DDL runs on the caller's connection, so an
  authenticated user with no engine grants cannot cause a root write.

The last class in each section is the over-blocking control: a legitimate
statement must still reach the engine, because refusing everything is not a
fix. Unit-level: no engine, no network.
"""

import pytest

from app.common.identifiers import (
    DDLError,
    InvalidIdentifierError,
    check_column_type,
    check_distributed_by,
    check_identifier,
    check_partition_by,
    check_partition_value,
    check_property_key,
    check_property_value,
)
from app.core.exceptions import ForbiddenSQLError

#: The payload confirmed by the reviewer. It closes the column definition and
#: starts a second statement that creates an attacker user. On the pre-change
#: tree this string was interpolated into ``CREATE TABLE ... (col <type> ...)``
#: and handed to ``db.execute_system`` (root).
CONFIRMED_ESCALATION_PAYLOAD = "INT) ENGINE=OLAP; CREATE USER `pwn`"


class RecordingConnection:
    """The caller-connection boundary: records every statement executed on it."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def cursor(self):
        return _RecordingCursor(self)


class _RecordingCursor:
    def __init__(self, conn: RecordingConnection) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    async def execute(self, sql: str, params=None) -> None:
        self._conn.calls.append(sql)


@pytest.fixture
def conn():
    return RecordingConnection()


# ── AC1: identifier allow-list ──────────────────────────────────────────────


class TestIdentifierAllowList:
    @pytest.mark.parametrize(
        "value",
        [
            "orders",
            "_orders",
            "orders_2",
            "Orders$",
            "ORDERS",
        ],
    )
    def test_bare_identifiers_are_accepted(self, value):
        assert check_identifier(value) == value

    @pytest.mark.parametrize(
        "payload",
        [
            "x`",
            "`x",
            "x`y",
            "x'",
            "x; DROP TABLE t",
            "x y",
            "x)",  # closes the backtick-quoted identifier
            "x.",  # cannot cross a qualified segment
            "1x",  # must not start with a digit
            "",
            "x\n",
        ],
    )
    def test_quoting_and_separator_payloads_are_rejected(self, payload):
        with pytest.raises(InvalidIdentifierError):
            check_identifier(payload)

    def test_refusal_is_a_forbidden_sql_error(self):
        # The API's existing refusal contract is ``ForbiddenSQLError``; the new
        # error must slot into it so routers do not grow a second shape.
        assert issubclass(InvalidIdentifierError, ForbiddenSQLError)
        assert issubclass(DDLError, ForbiddenSQLError)


class TestPerRouteIdentifierRefusal:
    """AC1: a backtick payload is refused before the engine, per DDL route."""

    @pytest.mark.parametrize("field", ["database", "view_name"])
    async def test_view_create_refuses_bad_identifier(self, conn, field):
        from app.modules.views.router import CreateViewRequest, create_view

        kwargs = {"database": "db1", "view_name": "v1", "select_sql": "SELECT 1"}
        kwargs[field] = "v1`; DROP USER root; --"
        req = CreateViewRequest(**kwargs)
        with pytest.raises(InvalidIdentifierError):
            await create_view(req, user={"username": "analyst"}, conn=conn)
        assert conn.calls == []

    @pytest.mark.parametrize("field", ["database", "table"])
    async def test_table_create_refuses_bad_identifier(self, conn, field):
        from app.modules.tables.router import CreateTableRequest, create_table

        kwargs = {
            "database": "db1",
            "table": "t1",
            "columns": [{"name": "id", "type": "INT"}],
        }
        kwargs[field] = "t1`; DROP USER root; --"
        req = CreateTableRequest(**kwargs)
        with pytest.raises(InvalidIdentifierError):
            await create_table(req, user={"username": "analyst"}, conn=conn)
        assert conn.calls == []

    async def test_table_create_refuses_backtick_column_name(self, conn):
        from app.modules.tables.router import CreateTableRequest, create_table

        req = CreateTableRequest(
            database="db1",
            table="t1",
            columns=[{"name": "id`; DROP USER root; --", "type": "INT"}],
        )
        with pytest.raises(InvalidIdentifierError):
            await create_table(req, user={"username": "analyst"}, conn=conn)
        assert conn.calls == []

    async def test_table_drop_refuses_bad_identifier(self, conn):
        from app.modules.tables.router import DropTableRequest, drop_table

        with pytest.raises(InvalidIdentifierError):
            await drop_table(
                DropTableRequest(database="db1", table="t1`"),
                user={"username": "analyst"},
                conn=conn,
            )
        assert conn.calls == []

    async def test_materialized_view_refuses_bad_identifier(self, conn):
        from app.modules.views.router import (
            CreateMaterializedViewRequest,
            create_materialized_view,
        )

        req = CreateMaterializedViewRequest(
            database="db1", mv_name="mv1`", select_sql="SELECT 1"
        )
        with pytest.raises(InvalidIdentifierError):
            await create_materialized_view(req, user={"username": "analyst"}, conn=conn)
        assert conn.calls == []


# ── AC2: the confirmed payload ──────────────────────────────────────────────


class TestConfirmedEscalationPayload:
    """AC2 — fails on the pre-change tree: the old tree had no validation."""

    def test_the_unchanged_guard_does_not_block_the_payload(self):
        """Why the allow-list is necessary: ``guard_sql`` is not a parser.

        The guard only blocks a handful of ACCOUNTADMIN/root/UDF statements. A
        column type that closes the definition and starts a new ``CREATE USER``
        is invisible to it, which is exactly how the payload reached the root
        pool. This documents the gap the fix closes — it passes on both trees.
        """
        from app.common.sql_guard import guard_sql

        # No exception: the guard accepts it.
        guard_sql(
            "CREATE TABLE `db1`.`t1` (\n"
            f"    `id` {CONFIRMED_ESCALATION_PAYLOAD} NULL\n"
            ")\nDISTRIBUTED BY HASH(id) BUCKETS 10\nPROPERTIES(\"replication_num\"=\"1\")"
        )

    def test_column_type_payload_is_rejected(self):
        with pytest.raises(DDLError):
            check_column_type(CONFIRMED_ESCALATION_PAYLOAD)

    async def test_table_create_refuses_the_payload_before_the_engine(self, conn):
        from app.modules.tables.router import CreateTableRequest, create_table

        req = CreateTableRequest(
            database="db1",
            table="t1",
            columns=[{"name": "id", "type": CONFIRMED_ESCALATION_PAYLOAD}],
        )
        with pytest.raises(ForbiddenSQLError):
            await create_table(req, user={"username": "analyst"}, conn=conn)
        assert conn.calls == [], "the payload reached the engine"

    async def test_alter_table_refuses_the_payload_before_the_engine(self, conn):
        from app.modules.tables.router import AlterTableRequest, alter_table

        req = AlterTableRequest(
            database="db1",
            table="t1",
            action="ADD_COLUMN",
            column_name="c1",
            column_type=CONFIRMED_ESCALATION_PAYLOAD,
        )
        with pytest.raises(ForbiddenSQLError):
            await alter_table(req, user={"username": "analyst"}, conn=conn)
        assert conn.calls == [], "the payload reached the engine"

    async def test_udf_return_type_payload_is_refused(self):
        from app.modules.functions.schemas import UDFCreate
        from app.modules.functions.service import function_service

        data = UDFCreate(
            name="f1",
            database="db1",
            args=[{"name": "x", "type": "INT"}],
            return_type=CONFIRMED_ESCALATION_PAYLOAD,
        )
        with pytest.raises(DDLError):
            await function_service.create_udf(data, RecordingConnection())


# ── AC3: types and clauses are mapped, not passed through ───────────────────


class TestClauseAllowList:
    @pytest.mark.parametrize(
        "value",
        ["INT", "BIGINT", "VARCHAR(255)", "DECIMAL(10, 2)", "ARRAY<INT>", "DATETIME"],
    )
    def test_allow_listed_types_are_accepted(self, value):
        assert check_column_type(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            CONFIRMED_ESCALATION_PAYLOAD,
            "INT; DROP TABLE t",
            "INT`",
            "INT'",
            "",
            "INT) ",
        ],
    )
    def test_type_fragments_are_rejected(self, value):
        with pytest.raises(DDLError):
            check_column_type(value)

    @pytest.mark.parametrize(
        "value",
        ["HASH(id)", "HASH(id, name)", "hash(a, b)", "RANDOM", "DUPLICATE"],
    )
    def test_allow_listed_distribution_is_accepted(self, value):
        assert check_distributed_by(value) == value

    @pytest.mark.parametrize(
        "value",
        ["HASH(id); DROP USER root", "HASH(id`)", "RANDOM; DROP TABLE t", "HASH()", "id"],
    )
    def test_distribution_fragments_are_rejected(self, value):
        with pytest.raises(DDLError):
            check_distributed_by(value)

    @pytest.mark.parametrize(
        "value",
        ["RANGE(dt)", "RANGE(date_col) (START ('2024-01-01') END ('2025-01-01'))", "LIST(city)"],
    )
    def test_allow_listed_partition_is_accepted(self, value):
        assert check_partition_by(value) == value

    @pytest.mark.parametrize(
        "value",
        ["RANGE(dt); DROP USER root", "RANGE(dt`)", "RANGE(dt) ' OR 1=1"],
    )
    def test_partition_fragments_are_rejected(self, value):
        with pytest.raises(DDLError):
            check_partition_by(value)

    @pytest.mark.parametrize(
        "value",
        ["[('2024-01-01')]", "VALUES [('a'), ('b')]", "LESS THAN ('2025-01-01')"],
    )
    def test_allow_listed_partition_value_is_accepted(self, value):
        assert check_partition_value(value) == value

    @pytest.mark.parametrize(
        "value",
        ["[('a')]; DROP USER root", "[('a`')]", "[('a\"; DROP USER root)]"],
    )
    def test_partition_value_fragments_are_rejected(self, value):
        with pytest.raises(DDLError):
            check_partition_value(value)

    @pytest.mark.parametrize("key", ["replication_num", "storage_medium", "bloom_filter_columns"])
    def test_property_keys_are_validated(self, key):
        assert check_property_key(key) == key

    @pytest.mark.parametrize("key", ["k\"; DROP USER root", "k`", "", "k with space"])
    def test_property_key_fragments_are_rejected(self, key):
        with pytest.raises(DDLError):
            check_property_key(key)

    @pytest.mark.parametrize("value", ["1", "true", "HLL", "3.5"])
    def test_property_values_are_validated(self, value):
        assert check_property_value(value) == value

    @pytest.mark.parametrize("value", ['1"; DROP USER root', "1`", "", "a b", "x,y"])
    def test_property_value_fragments_are_rejected(self, value):
        with pytest.raises(DDLError):
            check_property_value(value)

    async def test_table_create_distribution_payload_is_refused(self, conn):
        from app.modules.tables.router import CreateTableRequest, create_table

        req = CreateTableRequest(
            database="db1",
            table="t1",
            columns=[{"name": "id", "type": "INT"}],
            distributed_by="HASH(id); DROP USER root",
        )
        with pytest.raises(ForbiddenSQLError):
            await create_table(req, user={"username": "analyst"}, conn=conn)
        assert conn.calls == []

    async def test_mv_create_property_payload_is_refused(self, conn):
        from app.modules.views.router import (
            CreateMaterializedViewRequest,
            create_materialized_view,
        )

        req = CreateMaterializedViewRequest(
            database="db1",
            mv_name="mv1",
            select_sql="SELECT 1",
            properties={"replication_num": '1"; DROP USER root'},
        )
        with pytest.raises(ForbiddenSQLError):
            await create_materialized_view(req, user={"username": "analyst"}, conn=conn)
        assert conn.calls == []


# ── AC4/AC5: delegate-first ─────────────────────────────────────────────────


class TestDelegateFirst:
    """Object DDL runs on the caller's connection, never the root pool."""

    async def test_view_create_runs_on_the_caller_connection(self, conn):
        from app.modules.views.router import CreateViewRequest, create_view

        await create_view(
            CreateViewRequest(database="db1", view_name="v1", select_sql="SELECT 1"),
            user={"username": "analyst"},
            conn=conn,
        )
        assert len(conn.calls) == 1
        assert conn.calls[0].startswith("CREATE VIEW `db1`.`v1`")

    async def test_view_drop_runs_on_the_caller_connection(self, conn):
        from app.modules.views.router import DropViewRequest, drop_view

        await drop_view(
            DropViewRequest(database="db1", view_name="v1"),
            user={"username": "analyst"},
            conn=conn,
        )
        assert conn.calls == ["DROP VIEW `db1`.`v1`"]

    async def test_table_drop_runs_on_the_caller_connection(self, conn):
        from app.modules.tables.router import DropTableRequest, drop_table

        await drop_table(
            DropTableRequest(database="db1", table="t1"),
            user={"username": "analyst"},
            conn=conn,
        )
        assert conn.calls == ["DROP TABLE `db1`.`t1`"]

    async def test_udf_create_runs_on_the_caller_connection(self):
        from app.modules.functions.schemas import UDFCreate
        from app.modules.functions.service import function_service

        conn = RecordingConnection()
        sql = await function_service.create_udf(
            UDFCreate(
                name="f1",
                database="db1",
                args=[{"name": "x", "type": "INT"}],
                return_type="INT",
                body="x + 1",
            ),
            conn,
        )
        assert conn.calls == [sql]
        assert sql.startswith("CREATE FUNCTION db1.f1(x INT)")

    async def test_udf_drop_runs_on_the_caller_connection(self):
        from app.modules.functions.service import function_service

        conn = RecordingConnection()
        sql = await function_service.drop_udf("db1", "f1", conn)
        assert conn.calls == [sql]
        assert sql == "DROP FUNCTION IF EXISTS db1.f1"

    async def test_udf_drop_refuses_bad_name_before_the_engine(self):
        from app.modules.functions.service import function_service

        conn = RecordingConnection()
        with pytest.raises(InvalidIdentifierError):
            await function_service.drop_udf("db1", "f1`; DROP USER root", conn)
        assert conn.calls == []


# ── Over-blocking controls ──────────────────────────────────────────────────


class TestLegitimateDDLStillRuns:
    """A fix that refuses ordinary DDL is a regression, not safety."""

    async def test_table_create_with_full_clauses_succeeds(self, conn):
        from app.modules.tables.router import CreateTableRequest, create_table

        req = CreateTableRequest(
            database="db1",
            table="orders",
            columns=[
                {"name": "id", "type": "BIGINT", "nullable": False},
                {"name": "name", "type": "VARCHAR(255)"},
            ],
            keys=["id"],
            distributed_by="HASH(id)",
            buckets=8,
            partition_by="RANGE(dt) (START ('2024-01-01') END ('2025-01-01'))",
            properties={"replication_num": "1"},
            comment="orders table",
        )
        result = await create_table(req, user={"username": "analyst"}, conn=conn)
        assert result["success"] is True
        assert len(conn.calls) == 1
        assert conn.calls[0].startswith("CREATE TABLE `db1`.`orders`")

    async def test_materialized_view_with_clauses_succeeds(self, conn):
        from app.modules.views.router import (
            CreateMaterializedViewRequest,
            create_materialized_view,
        )

        req = CreateMaterializedViewRequest(
            database="db1",
            mv_name="mv1",
            select_sql="SELECT id, COUNT(*) FROM orders GROUP BY id",
            distributed_by="HASH(id)",
            buckets=4,
            refresh_strategy="ASYNC",
            properties={"replication_num": "1"},
        )
        result = await create_materialized_view(
            req, user={"username": "analyst"}, conn=conn
        )
        assert result["success"] is True
        assert len(conn.calls) == 1
        assert "DISTRIBUTED BY HASH(id) BUCKETS 4" in conn.calls[0]

    async def test_alter_add_column_with_a_normal_type_succeeds(self, conn):
        from app.modules.tables.router import AlterTableRequest, alter_table

        req = AlterTableRequest(
            database="db1",
            table="orders",
            action="ADD_COLUMN",
            column_name="total",
            column_type="DECIMAL(10, 2)",
        )
        result = await alter_table(req, user={"username": "analyst"}, conn=conn)
        assert result["success"] is True
        assert conn.calls == [
            "ALTER TABLE `db1`.`orders` ADD COLUMN `total` DECIMAL(10, 2)"
        ]
