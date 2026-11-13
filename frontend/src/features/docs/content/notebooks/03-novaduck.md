---
title: Query and change Nova data with novaduck
description: Connect the notebook's DuckDB to Nova with one call, then use ordinary DuckDB SQL and Python.
---

## What it is

A notebook runs DuckDB 2.0. `novaduck.connect()` returns that DuckDB
connection, already signed in to Nova as you, under your active role. It is the
only Nova-specific call: everything after it is ordinary DuckDB SQL and the
DuckDB Python API, as described in the [DuckDB 2.0 documentation](https://duckdb.org/docs/2.0/).

## Connect

Start a notebook runtime, then run this Python cell:

```python
import novaduck
con = novaduck.connect()
con.sql('SELECT 42').fetchall()
```

It returns `[(42,)]`. SQL cells and `duckdb.sql(...)` use the same connection.

Nova's own tables are attached under the catalog name `nova`. Connected
sources appear under their connection names. DuckDB lists them:

```python
con.sql("SELECT database_name FROM duckdb_databases() WHERE type = 'mysql' ORDER BY 1").fetchall()
```

The result includes `('nova',)`.

## Before you start

Your active role needs permission on the tables you use. Create an example
database and table in the SQL worksheet first. Replace `notebook_examples`
consistently if that name is already in use.

```sql
CREATE DATABASE notebook_examples;
CREATE TABLE notebook_examples.orders (id INT, amount INT)
PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ('replication_num'='1');
```

## Read tables

Name a table in full, as `catalog.database.table`:

```python
con.sql("INSERT INTO nova.notebook_examples.orders VALUES (1, 100), (2, 200)")
con.sql("SELECT * FROM nova.notebook_examples.orders ORDER BY id").fetchall()
```

This returns `[(1, 100), (2, 200)]`. Filters run in Nova, so a `WHERE` clause
reads only the matching rows.

Everything else DuckDB offers works on these tables: joins with local files and
DataFrames, `FROM`-first queries, `GROUP BY ALL`, `PIVOT`, window functions with
`QUALIFY`, macros, `SUMMARIZE`, the relational API and `COPY ... TO` a local
file. For example:

```python
con.sql("FROM nova.notebook_examples.orders SELECT sum(amount) AS total").fetchall()
```

returns `[(300,)]`, and `con.table("nova.notebook_examples.orders").df()` gives a
pandas DataFrame. `SHOW TABLES FROM nova.notebook_examples` and
`DESCRIBE nova.notebook_examples.orders` list tables and columns.

To run a statement in Nova itself, such as a stage read or a Nova function, use
DuckDB's `mysql_query` on the `nova` catalog:

```python
con.sql("SELECT * FROM mysql_query('nova', 'SELECT CURRENT_ROLE()')").fetchall()
```

The result contains your active role.

## Change tables

`INSERT`, `UPDATE`, `DELETE` and `CREATE TABLE ... AS` work on Nova tables and
run as you. A readable table is not necessarily writable: your role decides.

```python
con.sql("UPDATE nova.notebook_examples.orders SET amount = 120 WHERE id = 1")
con.sql("DELETE FROM nova.notebook_examples.orders WHERE id = 2")
con.sql("SELECT * FROM nova.notebook_examples.orders ORDER BY id").fetchall()
```

This returns `[(1, 120)]`.

On a table with a primary key, `INSERT` of an existing key replaces that row,
so an `INSERT` is also an upsert:

```python
con.sql("INSERT INTO nova.notebook_examples.orders VALUES (1, 150), (3, 300)")
con.sql("SELECT * FROM nova.notebook_examples.orders ORDER BY id").fetchall()
```

This returns `[(1, 150), (3, 300)]`.

To keep a result as a new Nova table:

```python
con.sql("CREATE TABLE nova.notebook_examples.big_orders AS SELECT * FROM nova.notebook_examples.orders WHERE amount >= 200")
```

## Limits and errors

- Each statement takes effect when it runs. `BEGIN` and `ROLLBACK` do not undo
  changes to Nova tables.
- `MERGE INTO` is not available on Nova tables. Use `INSERT` into a table with
  a primary key (an upsert, as above), or `UPDATE` followed by `INSERT`.
- `TRUNCATE nova.<database>.<table>` (or `DELETE ... WHERE true`) empties a
  table. Any other `DELETE` needs a `WHERE` condition.
- `CREATE OR REPLACE TABLE` is not available on Nova tables. Run `DROP TABLE`,
  then `CREATE TABLE ... AS`.
- `USE` and `SET search_path` cannot point at a Nova catalog. Write full table
  names. If you ran one, restart the kernel.
- DuckDB 2.0 relations have no `fetchone()`; use `.limit(1).fetchall()`. A
  result you started reading must be read to the end before the connection runs
  another query: `.arrow()` returns a reader, so use `.arrow().read_all()`.
- Connected Postgres and Snowflake sources are read-only through this
  connection.
- If a write times out or the connection ends, its outcome is uncertain. Query
  the table before repeating it; repeating an `INSERT` into a table without a
  primary key can duplicate rows.
- Restarting the kernel clears local tables and variables. Run the import and
  `novaduck.connect()` again.
