---
title: Nova SQL reference
description: Every SQL statement Nova adds on top of standard StarRocks SQL, what it does, and where it is explained.
---

## What it is

Nova runs standard StarRocks SQL: `SELECT`, `CREATE TABLE`, `INSERT`,
`UPDATE`, views, materialized views, `SHOW`, `DESCRIBE`, and so on. On top of
that, Nova adds its own statements for files, connected sources, roles,
tasks, and streams. This page lists those additions.

Every statement, standard or Nova's own, runs with your account and active
role, and goes through the same checks in the worksheet and in MySQL clients.

## How to read the syntax

- `UPPER CASE` words are typed as shown.
- `<name>` is a value you choose.
- `[ ... ]` is optional. `a | b` means one or the other.

## Files in stages

| Statement | What it does | More |
| --- | --- | --- |
| `LIST [FILES] @<stage>[/<path>]` | Lists files and folders in a stage. | [Query files in a stage](/docs/sql/query-files-in-stages#list-files) |
| `SELECT ... FROM @<stage>/<file>` | Reads a CSV, TSV, or Parquet file as a table. Works anywhere a table name does: joins, subqueries, `INSERT ... SELECT`, `CREATE TABLE ... AS`. | [Query files in a stage](/docs/sql/query-files-in-stages#read-a-file) |
| `COPY INTO <table> FROM @<stage>/<path>` | Loads a file, or files matching a wildcard, into a table, column by column in order. | [Load data from files](/docs/sql/load-data-from-files) |
| `COPY INTO @<stage>/<file> FROM <table or query>` | Writes results to a stage. **Refused in the worksheet and MySQL clients.** | [Load data from files](/docs/sql/load-data-from-files#limits-and-errors) |
| `INSERT INTO @<stage>/<file> SELECT ...` | Writes results to a stage. **Refused in the worksheet and MySQL clients.** | [Load data from files](/docs/sql/load-data-from-files#limits-and-errors) |

`COPY INTO` needs a stage on one side. `COPY INTO orders_archive FROM orders`
is refused with
`COPY INTO reads from or writes to a stage (@stage); to copy between tables use INSERT INTO ... SELECT`.

```sql
LIST @docs_qa_sql_files;
SELECT COUNT(*) AS n FROM @docs_qa_sql_files.*.csv;
COPY INTO orders FROM @docs_qa_sql_files/new_orders.csv;
```

## Changing data

| Statement | What it does | More |
| --- | --- | --- |
| `MERGE INTO <target> [AS t] USING <source table or (query)> [AS s] ON <condition> WHEN MATCHED [AND <cond>] THEN UPDATE SET ... / THEN DELETE, WHEN NOT MATCHED [AND <cond>] THEN INSERT [(<cols>)] VALUES (...)` | Applies updates, inserts, or deletes from a source to a primary-key table. | [Add, change, and delete rows](/docs/sql/changing-data#merge-changes-from-another-table) |
| `TIMESTAMP '<date time>'` | The standard timestamp literal. Nova treats it as `DATETIME`. | [Query data with SELECT](/docs/sql/querying-data#date-and-time-values) |

## Connections and external catalogs

These manage the sources that external catalogs read from. The
[Connections page](/docs/data/connections-and-catalogs) does the same with
forms, and is the easier way to add a new source.

| Statement | What it does |
| --- | --- |
| `SHOW CONNECTIONS` | Lists connections: name, type, credentials, catalogs, owner, comment, created. |
| `DESCRIBE CONNECTION <name>` (or `DESC`) | Shows one connection. |
| `CREATE CONNECTION [IF NOT EXISTS] <name> TYPE = <starrocks or postgres or snowflake> ENDPOINT = '<address>' SECRET = '<secret reference>' [COMMENT = '<text>']` | Registers a source. `SECRET` names where the credentials are kept; it is never the password itself. |
| `DROP CONNECTION [IF EXISTS] <name>` | Removes a connection. Asks to confirm. |
| `CREATE FOREIGN CATALOG [IF NOT EXISTS] <name> USING CONNECTION <connection> [COMMENT '<text>']` | Creates a catalog whose schemas and tables can be queried as `<catalog>.<schema>.<table>`. |
| `REFRESH CATALOG <name> [SCHEMA <schema> [TABLE <table>]]` | Re-reads a catalog's list of schemas, tables, and columns. |

```sql
SHOW CONNECTIONS;
DESCRIBE CONNECTION shop_pg;
REFRESH CATALOG pg_shop SCHEMA public TABLE customers;
```

```text
Catalog  Status  Databases  Relations  Error
pg_shop  ok      1          6          NULL
```

Errors you may see:

| Message | Meaning |
| --- | --- |
| `Unknown CREATE CONNECTION clause PASSWORD; expected one of COMMENT, ENDPOINT, SECRET, TYPE` | Passwords are never typed into SQL. Use a secret reference, or enter credentials on the Connections page. |
| `Unsupported connection type 'mysql'; expected one of postgres, snowflake, starrocks` | Only these three source types are supported. |
| `SECRET must be a secret reference: ...` | The value is not a reference such as `aws://<name>` or `nova://<connection>`. |
| `Connection '<name>' does not exist` | Check the name with `SHOW CONNECTIONS`. |

## Roles and access

| Statement | What it does | More |
| --- | --- | --- |
| `USE ROLE <role>` / `SET ROLE <role>` | Switches your session to another of your roles. Run it on its own. `USE ROLE DEFAULT` returns to your default role. | [Use the SQL worksheet](/docs/sql/sql-workspace#switch-role-from-sql) |
| `SHOW AVAILABLE ROLES` | Lists the roles you can switch to. | |
| `SHOW CURRENT ACCESS` | Shows who you are, your active role, and whether access policies are active. | |
| `CREATE ROLE`, `DROP ROLE`, `GRANT`, `REVOKE`, `SHOW ROLES`, `SHOW GRANTS` | Manage roles and table access through Nova's access policies. | [Roles](/docs/access-control/roles) |
| `ALTER USER <user> REQUIRE PASSWORD CHANGE [OFF]` | Makes a user set a new password at next sign-in, or turns that off. | [Users](/docs/access-control/users) |

```sql
SHOW CURRENT ACCESS;
```

```text
Principal   Active Role   Authorization  Policy Health
nova_admin  ACCOUNTADMIN  Ranger         Active
```

## Tasks and streams

These are described in [Tasks and streams reference](/docs/tasks/reference):

- `CREATE TASK`, `EXECUTE TASK`, `ALTER TASK`, `DROP TASK`, `SHOW TASKS`,
  `DESCRIBE TASK`
- `CREATE STREAM`, `DROP STREAM`, `SHOW STREAMS`, `DESCRIBE STREAM`,
  `SHOW STREAM STATUS`, `SHOW STREAM BACKLOG`

## Machine learning

Nova also adds `CREATE ML_MODEL` and the `ML_FORECAST(...)` table function for
training and forecasting with SQL. See the
[Machine learning with SQL](/docs/ai-ml/machine-learning-sql). Text-generating functions are
described in [AI SQL functions](/docs/ai-ml/ai-sql-functions).

## Statements Nova refuses

| Statement | Message |
| --- | --- |
| `DROP USER root` | `root user cannot be dropped` |
| `ALTER ROLE ACCOUNTADMIN ...`, `DROP ROLE ACCOUNTADMIN`, `GRANT ACCOUNTADMIN TO ROLE ...`, `REVOKE ... FROM ACCOUNTADMIN` | The protected administrator role cannot be changed. |
| `SELECT ... INTO OUTFILE ...` | `INTO OUTFILE exports query results to a file and is not read-only.` |
| `INSERT INTO FILES(...) ...` | `INTO FILES writes to a file path and is not read-only.` |
| `COPY INTO @stage ...`, `INSERT INTO @stage ...` | `INTO @stage exports query results and is not read-only.` |
| `SET sql_dialect = '<other>'` | `Nova parses StarRocks SQL only; sql_dialect must remain StarRocks` |
| `SUBMIT TASK ...` | `SUBMIT TASK runs in the background without your sign-in and active role. Use CREATE TASK, which runs as you.` See [Create a task](/docs/tasks/create-a-task). |
| `CREATE ROUTINE LOAD ...`, `RESUME ROUTINE LOAD ...` | `Routine loads run in the background without your sign-in and active role. Load files with COPY INTO, or schedule a load with CREATE TASK.` |
| `CREATE PIPE ...`, `ALTER PIPE ... RESUME` | `Pipes run in the background without your sign-in and active role. Load files with COPY INTO, or schedule a load with CREATE TASK.` |
