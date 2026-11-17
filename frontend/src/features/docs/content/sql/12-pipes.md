---
title: Load files continuously with pipes
description: Create a pipe that loads every new file in a stage folder into a table exactly once, and monitor, pause, retry and remove it.
---

## What it is

A **pipe** watches a folder in a [stage](/docs/data/stages) and loads every
new file it finds into a table. You write the load once, as an
`INSERT INTO ... SELECT ... FROM` the stage folder, and the pipe runs it for
each new file: right after you create it, and then every few minutes while
it is running.

Each file is loaded once. The pipe keeps a list of the files it has seen
(name and version), so a file that is already loaded is never loaded again,
and a run that is interrupted never loads a file twice.

A pipe uses StarRocks pipe syntax. Nova runs it, not the database engine:
it runs with the pipe's **owner role**, and the stage's storage keys are
used only while a run reads the files.

## Before you start

- The stage must exist and the files must be in it. See
  [Stages](/docs/data/stages).
- The target table must exist.
- Your **active role** becomes the pipe's owner role. It needs:
  - `INSERT` (or `ALL`) on the target table;
  - read access to the stage: `SELECT`, `INSERT` or `ALL` on the stage's
    database and schema.
- The owner role needs a **scheduling account**, the same account that runs
  [scheduled tasks](/docs/tasks/schedules). An administrator connects it to
  the role. Without it, `CREATE PIPE` stops with "The role ... has no
  scheduled-task account yet".

## Create a pipe

```sql
CREATE [OR REPLACE] PIPE [IF NOT EXISTS] [<database>.]<pipe_name>
[PROPERTIES ("<key>" = "<value>" [, ...])]
AS INSERT INTO <table> [(<column>, ...)]
SELECT ... FROM FILES("path" = "@<stage>/<folder>/<pattern>", "format" = "<format>" [, ...])
```

or, with the Nova stage shorthand:

```sql
CREATE PIPE <pipe_name>
AS INSERT INTO <table> SELECT ... FROM @<stage>/<folder>/<pattern>
```

Example: load every Parquet file in the `orders` folder of the stage
`landing` into the table `orders`, checking for new files every minute:

```sql
CREATE PIPE orders_pipe
PROPERTIES ("POLL_INTERVAL" = "60")
AS INSERT INTO orders
SELECT * FROM FILES("path" = "@landing/orders/*.parquet", "format" = "parquet");
```

The same with CSV files that have a header row:

```sql
CREATE PIPE orders_csv_pipe
AS INSERT INTO orders (id, name, amount)
SELECT id, name, amount FROM @landing/orders_csv/*.csv;
```

The shorthand reads a CSV file the way a query on `@stage/file.csv` does:
Nova detects the delimiter and uses the header row as column names. With
`FILES(...)` you set the CSV options yourself, as in StarRocks, for example
`"csv.column_separator" = ","` and `"csv.skip_header" = "1"`; the columns are
then `$1`, `$2` and so on.

What Nova checks when you create a pipe:

1. The statement is one `INSERT INTO <table> ... SELECT` that reads one stage
   path. `INSERT OVERWRITE`, `VALUES`, your own `WITH LABEL`, a storage
   address and storage keys are refused.
2. Your role can insert into the table and read the stage.
3. The `INSERT` works on the first file already in the folder (Nova runs
   `EXPLAIN` for it as you). With no files yet, Nova checks that the table
   exists.

When the pipe is created it starts loading the files already in the folder.

### The path

- `"@landing/orders/*.parquet"`: every `.parquet` file directly in `orders`.
- `"@landing/orders/2026-*/*.csv"`: CSV files in every folder whose name
  starts with `2026-`.
- `*` matches any characters inside one folder name or file name; it does not
  reach into subfolders. `?` matches one character and `[0-9]` one of a set.
- `@<schema>.<stage>` and `@<database>.<schema>.<stage>` name a stage in
  another schema or database, as in queries.

### Properties

| Property | Default | What it does |
| --- | --- | --- |
| `AUTO_INGEST` | `TRUE` | `TRUE`: the pipe checks the folder every `POLL_INTERVAL` seconds and loads new or changed files. `FALSE`: it loads the files that are in the folder at its first run (right after it is created) and then stops in the `FINISHED` state. |
| `POLL_INTERVAL` | `300` | Seconds between checks for new files, from 1 to 86400. Nova's scheduler looks for due pipes every 15 seconds, so shorter intervals are checked about every 15 seconds. |
| `BATCH_SIZE` | `1GB` | The most data loaded by one `INSERT`. Write a number of bytes or a size such as `512MB`, `1GB` (`B`, `KB`, `MB`, `GB`, `TB`). A file larger than this is loaded on its own. |
| `BATCH_FILES` | `256` | The most files loaded by one `INSERT`, from 1 to 100000. |

Property names are not case-sensitive; values are quoted strings.

## How files are tracked

Each run of a pipe:

1. Checks that the owner role can still insert into the table and read the
   stage. If not, the pipe is suspended (see below) and nothing is loaded.
2. Lists the files that match the path and adds the ones it has not seen.
   A file is identified by its name and its **version**, the checksum that the
   storage keeps for it. A file replaced with new content has a new version
   and is loaded again; the same content is never loaded twice.
3. Groups the waiting files into batches of at most `BATCH_FILES` files and
   `BATCH_SIZE` bytes, and loads each batch with one `INSERT`.

Every batch has a **load label** made from the pipe and the exact files in it.
The database engine accepts a label only once. If a run is interrupted after
it sent a batch (a restart, a lost connection), the next run asks the engine
whether that label was loaded and records the result; if the batch never
arrived, it is sent again under the same label, and the engine refuses it if
it was in fact loaded. That is why a file loads exactly once.

A batch that fails because of the data in one of its files is split: each
file is loaded on its own, so the good files load and only the bad file is
marked `ERROR`.

The order in which files load is not guaranteed.

## File states

| State | Meaning |
| --- | --- |
| `UNLOADED` | Found and waiting for the next batch. |
| `LOADING` | Its batch was sent. A run settles it from the engine's record of the label. |
| `FINISHED` | Loaded. |
| `ERROR` | Its load failed. `ERROR_MSG` says why. It stays here until you retry it. |

## Pipe states

| State | Meaning |
| --- | --- |
| `RUNNING` | Loads new files. |
| `SUSPENDED` | Loads nothing until `ALTER PIPE ... RESUME`. You suspended it, or the owner role lost access; `LAST_ERROR` says which. |
| `FINISHED` | `AUTO_INGEST` is `FALSE` and every file it found has been handled. A retried file, or `"AUTO_INGEST" = "TRUE"`, starts it again. |
| `ERROR` | Not used by Nova pipes today; failed files are reported per file instead. |

## Monitor a pipe

List the pipes of your active role in the current database, or in another one:

```sql
SHOW PIPES;
SHOW PIPES FROM sales;
SHOW PIPES WHERE NAME = 'orders_pipe';
SHOW PIPES LIKE 'orders%' ;
SHOW PIPES WHERE STATE = 'SUSPENDED' ORDER BY PIPE_NAME LIMIT 10;
```

`SHOW PIPES` returns `DATABASE_NAME`, `PIPE_ID`, `PIPE_NAME`, `TABLE_NAME`,
`STATE`, `LOAD_STATUS`, `LAST_ERROR`, `CREATED_TIME` and `OWNER_ROLE`.
`LOAD_STATUS` counts the files: `loadedFiles`, `loadedBytes`, `loadingFiles`
(waiting or loading), `errorFiles`, `loadedRows` and `LastLoadedTime`.

See one pipe's definition and properties:

```sql
DESC PIPE orders_pipe;
```

Query pipes and their files with SQL. These work like the StarRocks
information schema views of the same names:

```sql
SELECT PIPE_NAME, STATE, LOAD_STATUS
FROM information_schema.pipes
WHERE DATABASE_NAME = 'sales';

SELECT FILE_NAME, LOAD_STATE, FILE_SIZE, FINISH_LOAD_TIME, ERROR_MSG
FROM information_schema.pipe_files
WHERE PIPE_NAME = 'orders_pipe'
ORDER BY STAGED_TIME;
```

`information_schema.pipe_files` returns `DATABASE_NAME`, `PIPE_ID`,
`PIPE_NAME`, `FILE_NAME`, `FILE_VERSION`, `FILE_SIZE`, `LAST_MODIFIED`,
`LOAD_STATE`, `STAGED_TIME`, `START_LOAD_TIME`, `FINISH_LOAD_TIME` and
`ERROR_MSG`. `FILE_NAME` is the stage path, for example
`@sales.sales.landing/orders/2026-11-16.parquet`.

In the **Database Explorer**, a database's **Pipes** list shows each pipe of
your role with its state and file counts; select one to see its definition.

## Pause, resume and change a pipe

```sql
ALTER PIPE orders_pipe SUSPEND;
ALTER PIPE orders_pipe RESUME;
ALTER PIPE orders_pipe SET ("POLL_INTERVAL" = "30", "BATCH_FILES" = "50");
```

- `SUSPEND` stops new loads. A batch already sent finishes.
- `RESUME` checks the role's access again, then loads the files that arrived
  while the pipe was suspended.
- `SET` changes properties; the definition (`INSERT ... SELECT`) cannot be
  changed. To change it, use `CREATE OR REPLACE PIPE`.

> **Warning:** `CREATE OR REPLACE PIPE` starts a new pipe with an empty file
> list, so files the old pipe loaded load again. Remove those files from the
> folder first, or point the new pipe at a new folder.

## Retry failed files

Fix the file (upload a corrected copy with the same name), then:

```sql
ALTER PIPE orders_pipe RETRY FILE '@sales.sales.landing/orders/bad.parquet';
ALTER PIPE orders_pipe RETRY ALL;
```

`RETRY FILE` takes the `FILE_NAME` shown by `information_schema.pipe_files`,
or the path inside the stage (`'orders/bad.parquet'`). Only files in `ERROR`
are retried. A corrected file uploaded with new content is also found on the
next check as a new version, without a retry.

## Remove a pipe

```sql
DROP PIPE orders_pipe;
DROP PIPE IF EXISTS orders_pipe;
```

The pipe and its file list are removed. Rows it loaded stay in the table.

## Permissions and access changes

- Only the pipe's owner role sees it in `SHOW PIPES` and the information
  schema views. Administrators (`ACCOUNTADMIN`) see every pipe.
- Changing, resuming and retrying need the owner role active. Suspending and
  dropping are also allowed to administrators.
- Every run checks the owner role again. If the role can no longer insert
  into the table or read the stage, the pipe is set to `SUSPENDED` with
  `LAST_ERROR` explaining what changed, and nothing is loaded. After access is
  given back, run `ALTER PIPE ... RESUME`.
- Row filters and masking on the target table apply to the owner role, as for
  any `INSERT` it runs.

## Limits and errors

- A pipe reads stages on S3-compatible storage, one stage path per pipe.
- Supported formats: Parquet, ORC, CSV, JSON and Avro.
- One run lists at most 100,000 matching files.
- A pipe loads only with `INSERT INTO`. It cannot overwrite, update or delete.

| Message | What to do |
| --- | --- |
| `A pipe reads files from a stage: FILES("path" = "@stage/folder/*.parquet", ...)` | Use a stage path, not a storage address. |
| `Remove '<key>': Nova supplies the stage's storage location and credentials at each run` | Remove storage keys and endpoints from `FILES(...)`. |
| `Remove WITH LABEL: the pipe labels each batch so that it loads once` | Remove `WITH LABEL`. |
| `Name the files with their extension, for example @stage/folder/*.csv, ...` | Add the extension to the shorthand path, or use `FILES(...)` with `"format"`. |
| `The role <role> has no scheduled-task account yet, ...` | Ask an administrator to connect a scheduling account to the role. |
| `The role <role> cannot insert into <table>` / `cannot read the stage ...` | Ask for access, or switch to a role that has it. |
| `The pipe's INSERT does not run: ...` | The `INSERT` fails on the first file; the engine's message follows. |
| `Pipe <name> already exists` | Use `CREATE OR REPLACE PIPE` or `IF NOT EXISTS`, or another name. |
| `Only the owner role <role> can ... this pipe` | Activate the owner role. |
| `File <name> does not belong to pipe ...` / `... has not failed` | Use a `FILE_NAME` from `information_schema.pipe_files` that is in `ERROR`. |
