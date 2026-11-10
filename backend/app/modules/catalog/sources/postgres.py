"""PostgreSQL as a Nova Catalog source."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from .base import (
    MATERIALIZED_VIEW,
    TABLE,
    VIEW,
    CatalogSource,
    Credentials,
    Endpoint,
    JdbcDriver,
    ObjectKind,
    ObjectKinds,
)
from .writes import LOAD_TABLE, WriteRefused, WriteRequest, WriteResult, match_columns

#: ``pg_class.relkind`` → what Nova shows. Sequences, indexes, and composite
#: types are not relations a catalog exposes.
RELKINDS: dict[str, ObjectKind] = {
    "r": ObjectKind(TABLE, "Table"),
    "p": ObjectKind(TABLE, "Partitioned table"),
    "f": ObjectKind(TABLE, "Foreign table"),
    "v": ObjectKind(VIEW, "View"),
    "m": ObjectKind(MATERIALIZED_VIEW, "Materialized view"),
}

_RELATIONS_SQL = (
    "SELECT n.nspname, c.relname, c.relkind FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind = ANY(%s) AND n.nspname = ANY(%s)"
)


class PostgresSource(CatalogSource):
    connector_type = "postgres"
    label = "PostgreSQL"
    # The driver the pinned upstream FE image ships, copied into the BE image.
    driver = JdbcDriver("postgresql-42.7.12.jar", "org.postgresql.Driver")
    jdbc_scheme = "postgresql"
    system_schemas: ClassVar[frozenset[str]] = frozenset({"pg_catalog", "information_schema"})
    default_port = 5432
    database_required = True
    endpoint_format = "host[:port]/database"
    #: libpq/JDBC options that only shape the transport.
    allowed_options: ClassVar[frozenset[str] | None] = frozenset(
        {"sslmode", "connectTimeout", "socketTimeout", "ApplicationName"}
    )

    async def read_objects(
        self, endpoint: Endpoint, credentials: Credentials, databases: Sequence[str]
    ) -> ObjectKinds:
        import psycopg

        if not databases:
            return {}
        async with await psycopg.AsyncConnection.connect(
            host=endpoint.direct_host,
            port=endpoint.port,
            dbname=endpoint.database,
            user=credentials.username,
            password=credentials.password,
            sslmode=endpoint.options.get("sslmode", "prefer"),
            connect_timeout=self.connect_timeout,
            application_name="nova-catalog",
        ) as connection:
            # A metadata read only: never leave a transaction open on the source.
            await connection.set_read_only(True)
            cursor = await connection.execute(
                _RELATIONS_SQL, [list(RELKINDS), list(databases)]
            )
            rows = await cursor.fetchall()
        return {
            (str(schema), str(name)): RELKINDS[str(kind)]
            for schema, name, kind in rows
            if str(kind) in RELKINDS
        }

    # ── Notebook write-back ──────────────────────────────────────

    supports_writeback = True

    async def write_rows(
        self, endpoint: Endpoint, credentials: Credentials, request: WriteRequest
    ) -> WriteResult:
        """Load the rows with ``COPY`` into a temporary table, then append,
        overwrite or ``MERGE`` (PostgreSQL 15+) into the target, in one
        transaction. Identifiers are quoted by psycopg; values never touch SQL text.
        """
        import psycopg
        from psycopg import sql

        async with await psycopg.AsyncConnection.connect(
            host=endpoint.direct_host,
            port=endpoint.port,
            dbname=endpoint.database,
            user=credentials.username,
            password=credentials.password,
            sslmode=endpoint.options.get("sslmode", "prefer"),
            connect_timeout=self.connect_timeout,
            application_name="nova-writeback",
        ) as connection, connection.transaction():
            await connection.execute(
                sql.SQL("SET LOCAL statement_timeout = {}").format(
                    sql.Literal(int(request.timeout_seconds) * 1000)
                )
            )
            cursor = await connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                [request.schema, request.table],
            )
            existing = [str(row[0]) for row in await cursor.fetchall()]
            if not existing:
                raise WriteRefused(
                    f"{request.schema}.{request.table} does not exist or the write "
                    "connection cannot see it"
                )
            columns = match_columns(request.columns, existing)
            keys = match_columns(request.keys, existing) if request.keys else []
            target = sql.Identifier(request.schema, request.table)
            load = sql.Identifier(LOAD_TABLE)
            names = sql.SQL(", ").join(map(sql.Identifier, columns))
            await connection.execute(
                sql.SQL(
                    "CREATE TEMP TABLE {load} ON COMMIT DROP AS "
                    "SELECT {names} FROM {target} WITH NO DATA"
                ).format(load=load, names=names, target=target)
            )
            async with connection.cursor() as copy_cursor, copy_cursor.copy(
                sql.SQL("COPY {load} ({names}) FROM STDIN").format(load=load, names=names)
            ) as copy:
                for row in request.rows:
                    await copy.write_row(row)
            return await self._apply(connection, request, target, load, columns, keys)

    async def _apply(self, connection, request, target, load, columns, keys) -> WriteResult:
        from psycopg import sql

        names = sql.SQL(", ").join(map(sql.Identifier, columns))
        insert = sql.SQL("INSERT INTO {target} ({names}) SELECT {names} FROM {load}").format(
            target=target, names=names, load=load
        )
        if request.mode == "append":
            inserted = (await connection.execute(insert)).rowcount
            return WriteResult(inserted=inserted)
        if request.mode == "overwrite":
            deleted = (await connection.execute(sql.SQL("DELETE FROM {}").format(target))).rowcount
            inserted = (await connection.execute(insert)).rowcount
            return WriteResult(inserted=inserted, deleted=deleted)

        version = await _scalar(connection, "SHOW server_version_num")
        if int(version) < 150000:
            raise WriteRefused("merge_into needs PostgreSQL 15 or later on this connection")
        key_list = sql.SQL(", ").join(map(sql.Identifier, keys))
        duplicate = await connection.execute(
            sql.SQL("SELECT 1 FROM {load} GROUP BY {keys} HAVING COUNT(*) > 1 LIMIT 1").format(
                load=load, keys=key_list
            )
        )
        if await duplicate.fetchone():
            raise WriteRefused("The data has more than one row for the same key")
        on = sql.SQL(" AND ").join(
            sql.SQL("t.{k} = s.{k}").format(k=sql.Identifier(key)) for key in keys
        )
        matched = await _scalar(
            connection,
            sql.SQL(
                "SELECT COUNT(*) FROM {load} s WHERE EXISTS (SELECT 1 FROM {target} t WHERE {on})"
            ).format(load=load, target=target, on=on),
        )
        total = await _scalar(connection, sql.SQL("SELECT COUNT(*) FROM {}").format(load))
        clauses: list[sql.Composable] = []
        values = [column for column in columns if column not in keys]
        if request.when_matched == "update" and values:
            clauses.append(
                sql.SQL("WHEN MATCHED THEN UPDATE SET {}").format(
                    sql.SQL(", ").join(
                        sql.SQL("{c} = s.{c}").format(c=sql.Identifier(column))
                        for column in values
                    )
                )
            )
        elif request.when_matched == "delete":
            clauses.append(sql.SQL("WHEN MATCHED THEN DELETE"))
        if request.when_not_matched == "insert":
            clauses.append(
                sql.SQL("WHEN NOT MATCHED THEN INSERT ({names}) VALUES ({values})").format(
                    names=sql.SQL(", ").join(map(sql.Identifier, columns)),
                    values=sql.SQL(", ").join(
                        sql.SQL("s.{}").format(sql.Identifier(column)) for column in columns
                    ),
                )
            )
        if not clauses:
            return WriteResult()
        await connection.execute(
            sql.SQL("MERGE INTO {target} t USING {load} s ON {on} {clauses}").format(
                target=target, load=load, on=on, clauses=sql.SQL(" ").join(clauses)
            )
        )
        updated = matched if request.when_matched == "update" and values else 0
        deleted = matched if request.when_matched == "delete" else 0
        inserted = total - matched if request.when_not_matched == "insert" else 0
        return WriteResult(inserted=int(inserted), updated=int(updated), deleted=int(deleted))


async def _scalar(connection, query) -> int:
    row = await (await connection.execute(query)).fetchone()
    return int(row[0]) if row and row[0] is not None else 0
