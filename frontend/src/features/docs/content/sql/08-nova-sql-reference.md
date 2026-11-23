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

## Query schema

| Statement | What it does | More |
| --- | --- | --- |
| `EXPLAIN SCHEMA <query>` | Returns column names, SQL types and nullability after checking your source access, without reading result rows. | [Inspect a result schema](/docs/sql/querying-data#inspect-a-result-schema) |

## Files in stages

| Statement | What it does | More |
| --- | --- | --- |
| `LIST [FILES] @<stage>[/<path>]` | Lists files and folders in a stage. | [Query files in a stage](/docs/sql/query-files-in-stages#list-files) |
| `SELECT ... FROM @<stage>/<file>` | Reads a CSV, TSV, or Parquet file as a table. Works anywhere a table name does: joins, subqueries, `INSERT ... SELECT`, `CREATE TABLE ... AS`. | [Query files in a stage](/docs/sql/query-files-in-stages#read-a-file) |
| `COPY INTO <table> FROM @<stage>/<path>` | Loads a file, or files matching a wildcard, into a table, column by column in order. | [Load data from files](/docs/sql/load-data-from-files) |
| `COPY INTO @<stage>/<folder>/ FROM <table or (query)>` | Writes results to CSV files in a stage, after you confirm. Your active role needs write access to the stage. Refused in MySQL clients. | [Unload data to a stage](/docs/sql/unload-data-to-stages#copy-into-and-insert-into-a-stage) |
| `INSERT INTO @<stage>/<folder>/ SELECT ...` | The same as above, written as an insert. | [Unload data to a stage](/docs/sql/unload-data-to-stages#copy-into-and-insert-into-a-stage) |
| `INSERT INTO FILES('path' = '@<stage>/<folder>/', 'format' = ..., ...) SELECT ...` | Writes query results to CSV, Parquet, or ORC files in a stage, with every documented unload option. Asks for confirmation. | [Unload data to a stage](/docs/sql/unload-data-to-stages#insert-into-files) |
| `SELECT ... INTO OUTFILE '@<stage>/<folder>/<prefix>' FORMAT AS CSV \| PARQUET [PROPERTIES (...)]` | Writes one query's results to CSV or Parquet files in a stage. Asks for confirmation. | [Unload data to a stage](/docs/sql/unload-data-to-stages#select-into-outfile) |
| `EXPORT TABLE <table> [PARTITION (...)] [(<columns>)] TO '@<stage>/<folder>/' [PROPERTIES (...)] [WITH BROKER]` | Starts a background job that writes a table to CSV files in a stage. Asks for confirmation. | [Unload data to a stage](/docs/sql/unload-data-to-stages#export-table) |
| `SHOW EXPORT`, `CANCEL EXPORT` | Shows export jobs, with their stage path, and cancels one. | [Unload data to a stage](/docs/sql/unload-data-to-stages#check-or-cancel-an-export) |
| `LOAD LABEL [<db>.]<label> (DATA INFILE("@<stage>/<path>" [, ...]) [NEGATIVE] INTO TABLE <table> [PARTITION (...)] [COLUMNS TERMINATED BY ...] [FORMAT AS ...] [(<columns>)] [COLUMNS FROM PATH AS (...)] [SET (...)] [WHERE ...] [, ...]) [WITH BROKER] [PROPERTIES (...)]` | Starts a Broker Load job that loads CSV, Parquet, ORC or JSON files from stages, all or nothing. Your active role needs read access to every stage. | [Load files with Broker Load](/docs/sql/load-files-with-broker-load) |
| `SHOW LOAD [FROM <db>] [WHERE ...]`, `ALTER LOAD FOR <db>.<label> PROPERTIES ("priority" = ...)`, `CANCEL LOAD [FROM <db>] WHERE LABEL = "<label>"` | Follow, re-prioritize or cancel load jobs. Paths are shown as stage paths, also in `information_schema.loads`. | [Load files with Broker Load](/docs/sql/load-files-with-broker-load#follow-a-load) |

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
| `CREATE CONNECTION [IF NOT EXISTS] <name> TYPE = <starrocks, postgres, snowflake, iceberg or kafka> ENDPOINT = '<address>' [SECRET = '<secret reference>'] [COMMENT = '<text>']` | Registers a source. `SECRET` names where the credentials are kept; it is never the password itself. A Kafka connection with the `PLAINTEXT` or `SSL` protocol takes no `SECRET`. Endpoints: [Iceberg](/docs/data/iceberg-catalogs#with-sql), [Kafka](/docs/sql/routine-load-from-kafka#add-a-kafka-connection). |
| `DROP CONNECTION [IF EXISTS] <name>` | Removes a connection. Asks to confirm. |
| `CREATE FOREIGN CATALOG [IF NOT EXISTS] <name> USING CONNECTION <connection> [COMMENT '<text>']` | Creates a catalog whose schemas and tables can be queried as `<catalog>.<schema>.<table>`. Not for Kafka connections. |
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
| `Unsupported connection type 'mysql'; expected one of iceberg, kafka, postgres, snowflake, starrocks` | Only these source types are supported. |
| `A kafka connection cannot back a catalog; ...` | Kafka connections feed routine loads, not catalogs. |
| `SECRET must be a secret reference: ...` | The value is not a reference such as `aws://<name>` or `nova://<connection>`. |
| `Connection '<name>' does not exist` | Check the name with `SHOW CONNECTIONS`. |

## Routine loads from Kafka

Continuous loads from a Kafka topic into a Nova table. Full guide:
[Load continuously from Kafka](/docs/sql/routine-load-from-kafka).

| Statement | What it does |
| --- | --- |
| `CREATE ROUTINE LOAD <db>.<job> ON <table> [<columns, separators, WHERE>] PROPERTIES (...) FROM KAFKA ("kafka_connection" = "<connection>", "kafka_topic" = "<topic>", ...)` | Starts a job owned by your active role. Broker addresses and passwords come from the connection; `kafka_broker_list` and security settings are refused. |
| `SHOW [ALL] ROUTINE LOAD [FOR <db>.<job>] [FROM <db>]` | Lists jobs, their state, rows loaded and errors. `ALL` includes stopped jobs. |
| `SHOW ROUTINE LOAD TASK FROM <db> WHERE JobName = "<job>"` | Lists a job's running tasks. |
| `SHOW CREATE ROUTINE LOAD <db>.<job>` | Shows the job's statement (no password). |
| `PAUSE ROUTINE LOAD FOR <db>.<job>` | Pauses the job; it keeps its place in the topic. Owner role or administrator. |
| `RESUME ROUTINE LOAD FOR <db>.<job>` | Resumes it after checking the owner's access again. |
| `ALTER ROUTINE LOAD FOR <db>.<job> ...` | Changes a paused job's properties, columns, filter or offsets. Not the topic or connection. |
| `STOP ROUTINE LOAD FOR <db>.<job>` | Ends the job for good. Asks to confirm. |

Nova pauses a running job when its owner role loses write access to the table
or the use of the connection.

## Background statements

| Statement | What it does | More |
| --- | --- | --- |
| `SUBMIT TASK [<name>] [SCHEDULE EVERY (...)] [PROPERTIES ("session.<variable>" = ...)] AS <CREATE TABLE AS \| INSERT \| CACHE SELECT>` | Creates a Nova task owned by your active role. Without a schedule it runs once now, as you, and returns the task and run IDs. | [SUBMIT TASK](/docs/tasks/reference#submit-task) |
| `CREATE [READ ONLY] REPOSITORY <name> WITH BROKER ON LOCATION "@<stage>/<folder>/"`, `BACKUP`, `RESTORE`, `SHOW BACKUP`, `SHOW RESTORE`, `SHOW SNAPSHOT`, `SHOW REPOSITORIES`, `CANCEL BACKUP`, `CANCEL RESTORE`, `DROP REPOSITORY` | Back up tables to a stage folder and restore them. `ACCOUNTADMIN` only. | [Back up and restore databases](/docs/administration/backup-and-restore) |

## Roles and access

| Statement | What it does | More |
| --- | --- | --- |
| `USE ROLE <role>` / `SET ROLE <role>` | Switches your session to another of your roles. Run it on its own. `USE ROLE DEFAULT` returns to your default role. | [Use the SQL worksheet](/docs/sql/sql-workspace#switch-role-from-sql) |
| `SHOW AVAILABLE ROLES` | Lists the roles you can switch to. | |
| `SHOW CURRENT ACCESS` | Shows who you are, your active role, and whether access policies are active. | |
| `CREATE ROLE`, `DROP ROLE`, `GRANT`, `REVOKE`, `SHOW ROLES`, `SHOW GRANTS` | Manage roles and table access through Nova's access policies. | [Roles](/docs/access-control/roles) |
| `ALTER USER <user> REQUIRE PASSWORD CHANGE [OFF]` | Makes a user set a new password at next sign-in, or turns that off. | [Users](/docs/access-control/users) |
| `SHOW AUTHENTICATION`, `SHOW ALL AUTHENTICATION`, `SHOW AUTHENTICATION FOR <user>` | Lists the sign-in method of your account, of every account, or of one account (`UserIdentity`, `Password` as `Yes`/`No`, `AuthPlugin`). It never shows a password. The engine decides which accounts your role may list. | |

## Session statements

These are standard StarRocks statements. In the worksheet they apply to the
SQL file you run them in and are kept for its later runs.

| Statement | What it does | More |
| --- | --- | --- |
| `SET [SESSION] <setting> = <value>` | Changes an engine setting, such as `time_zone` or `query_mem_limit`. `= DEFAULT` clears it. | [Session settings and variables](/docs/sql/sql-workspace#session-settings-and-variables) |
| `SET @<name> = <value> [, @<name> = <value>]` | Sets user variables you can use as `@<name>`. `= NULL` clears one. | [Use variables](/docs/sql/sql-workspace#use-variables) |
| `BEGIN` / `START TRANSACTION`, `COMMIT`, `ROLLBACK` | Groups changes so that all or none are kept, within one run. | [Use a transaction](/docs/sql/sql-workspace#use-a-transaction) |
| `<statement>\G` | Shows that statement's result one record per block. | [Show a result one record per block](/docs/sql/sql-workspace#show-a-result-one-record-per-block) |

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

## Pipes

These are described in [Load files continuously with pipes](/docs/sql/pipes):

- `CREATE [OR REPLACE] PIPE ... AS INSERT INTO <table> SELECT ... FROM FILES("path" = "@stage/...")`
  or `... FROM @stage/...`
- `ALTER PIPE ... SUSPEND`, `ALTER PIPE ... RESUME`, `ALTER PIPE ... SET (...)`,
  `ALTER PIPE ... RETRY ALL`, `ALTER PIPE ... RETRY FILE '<file>'`
- `DROP PIPE [IF EXISTS] ...`, `SHOW PIPES`, `DESC PIPE ...`
- `SELECT ... FROM information_schema.pipes` and `information_schema.pipe_files`

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
| `INSERT INTO FILES(...)`, `SELECT ... INTO OUTFILE`, or `EXPORT TABLE` to anything other than a stage folder (a storage URL or a local path) | `<form> writes only to a stage. Write the target as '@<stage>/<folder>/', ...` See [Unload data to a stage](/docs/sql/unload-data-to-stages#limits-and-errors). |
| `SELECT ... FROM FILES(...)`, `DESC FILES(...)`, or `FILES(...)` anywhere else a table can go, outside `CREATE PIPE` | `Reading with FILES() is not available: read files through a stage, for example SELECT * FROM @stage/folder/file.csv, or load them with COPY INTO or a pipe` See [Query files in a stage](/docs/sql/query-files-in-stages#limits-and-errors). |
| An unload with storage credentials, an endpoint, or broker settings | `<form> to a stage takes no storage location, credential or broker options: the stage supplies them` |
| `EXPORT TABLE ... WITH BROKER (...)` or `WITH BROKER <name>` | `EXPORT TABLE to a stage takes no broker name or broker properties: ...` |
| Any unload, `COPY INTO @stage` or `INSERT INTO @stage` from a MySQL client, or from an assistant's read-only query tool | `INTO OUTFILE exports query results to a file and is not read-only.`, `INTO FILES writes to a file path and is not read-only.`, `INTO @stage exports query results and is not read-only.` or `EXPORT TABLE writes table data to files and is not read-only.` |
| `SET sql_dialect = '<other>'` | `Nova parses StarRocks SQL only; sql_dialect must remain StarRocks` |
| `SET GLOBAL <a> = ..., <b> = ...` mixing server-wide and session settings in one `SET` | `Run SET GLOBAL on its own: one SET cannot change a server-wide setting and this worksheet's session together` |
| `LOAD LABEL` with a storage address, a broker name, broker credentials or a credential property | `LOAD LABEL reads files only from a stage. ...`, `LOAD LABEL from a stage takes no broker name or broker properties ...` or `... takes no storage location, credential or broker properties ...` See [Load files with Broker Load](/docs/sql/load-files-with-broker-load#limits-and-errors). |
| `LOAD ... WITH RESOURCE` (Spark Load), `DATA FROM TABLE` | `LOAD ... WITH RESOURCE (Spark Load) is not available in Nova ...`, `DATA FROM TABLE loads from a Hive table through Spark Load ...` |
| `CREATE REPOSITORY` on a storage address or with properties; any repository, `BACKUP` or `RESTORE` statement without `ACCOUNTADMIN` | See [Back up and restore databases](/docs/administration/backup-and-restore#limits-and-errors). |
| `CREATE ROUTINE LOAD ...`, `RESUME ROUTINE LOAD ...` | `Routine loads run in the background without your sign-in and active role. Load files with COPY INTO, or schedule a load with CREATE TASK.` |
