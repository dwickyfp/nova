---
title: Query files in a stage
description: List the files in a stage and read CSV, TSV, and Parquet files with SELECT, as if they were tables.
---

## What it is

A **stage** is a named folder of files that Nova can read, such as CSV
exports. You refer to a stage in SQL with `@` followed by its name, for
example `@docs_qa_sql_files`. A file inside it is written as a path after the
name:

| You write | Means |
| --- | --- |
| `@docs_qa_sql_files` | The whole stage. |
| `@docs_qa_sql_files/new_orders.csv` | One file, slash form. |
| `@docs_qa_sql_files.new_orders.csv` | The same file, dot form. |
| `@docs_qa_sql_files/incoming/more_orders.csv` | A file in a folder. |
| `@docs_qa_sql_files.*.csv` | Every `.csv` file at the top of the stage (a **wildcard**). |
| `@docs_qa_sql_files.incoming.*.csv` | Every `.csv` file in the `incoming` folder. |

To create a stage and upload files, see [Stages](/docs/data/stages).

> **Warning:** Nova no longer runs queries that read files with the
> `FILES(...)` table function, such as
> `SELECT * FROM FILES("path" = "<storage address>", ...)`. Read the files through a
> stage instead. See [Moving from FILES()](#moving-from-files).

## Before you start

- The stage must exist, and your active role needs access to its database.
  Nova checks your access before it opens any file.
- You do not need storage passwords or keys. The stage holds the connection;
  you only use its name.

## Prepare the sample files

Create `docs_qa_sql_files` in the example database, then download and upload
these files through [Stages](/docs/data/stages#upload-files):

| Download | Upload location |
| --- | --- |
| [new_orders.csv](/examples/docs/new_orders.csv) | The stage's top level |
| [more_orders.csv](/examples/docs/more_orders.csv) | Set **Folder** to `incoming` |
| [prices.parquet](/examples/docs/prices.parquet) | The stage's top level |
| [stock.tsv](/examples/docs/stock.tsv) | The stage's top level |

Select that database in the worksheet. For the join example, create and fill
`customers` using [Add rows](/docs/sql/changing-data#insert-rows).
The files are small, invented examples; replace them with your own files for
real work.

## List files

```sql
LIST @docs_qa_sql_files;
```

```text
PATH                                           SIZE  IS_DIR  MODIFICATION_TIME
@docs_qa_sql_files                             0     1       NULL
@docs_qa_sql_files/incoming                    0     1       NULL
@docs_qa_sql_files/incoming/more_orders.csv    73    0       2026-11-11 09:43:34
@docs_qa_sql_files/new_orders.csv              131   0       2026-11-11 09:43:24
```

Folders have no modification time, so `MODIFICATION_TIME` is `NULL` for them.

List one folder (`FILES` is optional):

```sql
LIST FILES @docs_qa_sql_files/incoming/;
```

## Read a file

```sql
SELECT * FROM @docs_qa_sql_files/new_orders.csv;
```

```text
order_id  customer_id  order_date  amount  status
201       1            2026-06-01  55.0    new
202       2            2026-06-02  18.75   new
203       3            2026-06-02  99.9    shipped
```

Nova reads the header line of a CSV file and uses it for the column names.

Read several files at once with a wildcard:

```sql
SELECT COUNT(*) AS n FROM @docs_qa_sql_files.*.csv;
```

Join a file to a table:

```sql
SELECT o.order_id, c.name
FROM @docs_qa_sql_files/new_orders.csv AS o
JOIN customers c ON c.customer_id = o.customer_id
ORDER BY o.order_id;
```

```text
order_id  name
201       Ana
202       Budi
203       Citra
```

## File types

Nova picks the format from the file's extension:

| Extension | Read as |
| --- | --- |
| `.csv` | Comma-separated text with a header line |
| `.tsv` | Tab-separated text with a header line |
| `.parquet` | Parquet |

```sql
SELECT * FROM @docs_qa_sql_files/prices.parquet;
SELECT * FROM @docs_qa_sql_files/stock.tsv;
```

## Moving from FILES()

Earlier versions of Nova let you read files with the `FILES(...)` table
function and a storage address, endpoint or keys. Nova now refuses those
queries in every form: on their own, in a join or subquery, in `INSERT`,
`CREATE TABLE ... AS`, a view, `EXPLAIN`, `DESC FILES(...)` and task bodies.
The error is:

```text
Reading with FILES() is not available: read files through a stage, for example SELECT * FROM @stage/folder/file.csv, or load them with COPY INTO or a pipe
```

To move a query:

1. Ask your administrator for a stage that points at the folder, or use an
   existing one. See [Stages](/docs/data/stages).
2. Replace `FILES(...)` with the stage path. Nova picks the format from the
   file extension, and the stage supplies the location and credentials.

| Before | After |
| --- | --- |
| `SELECT * FROM FILES("path" = "<storage address>/orders/a.csv", "format" = "csv", ...)` | `SELECT * FROM @landing/orders/a.csv` |
| `INSERT INTO orders SELECT * FROM FILES(...)` | `INSERT INTO orders SELECT * FROM @landing/orders/a.csv`, or [`COPY INTO`](/docs/sql/load-data-from-files) |
| A scheduled load from `FILES(...)` | A [pipe](/docs/sql/pipes) with `FILES("path" = "@landing/orders/*.csv", ...)` |

These still work: [pipes](/docs/sql/pipes) keep their
`FILES("path" = "@stage/...")` source, and
[`INSERT INTO FILES(...)`](/docs/sql/unload-data-to-stages) still writes
query results to a stage folder.

## Limits and errors

- **JSON files cannot be read.** A query on a `.json` file fails with
  `not supported format: json`. Convert the file to CSV or Parquet first.
- **Wildcards in scripts:** the slash form of a wildcard (`@stage/*.csv`)
  contains `/*`, which SQL also uses to start a comment. It works when it is
  the only statement you run, but in a script with more statements after it,
  the rest of the script is swallowed and you get
  `Expected exactly one SQL statement`. Use the dot form instead
  (`@stage.*.csv`, `@stage.folder.*.csv`), which works everywhere.
- A comment written directly after a stage path, such as
  `@stage/x.csv/* note */`, is refused with `A comment starts directly after a
  stage path`. Put a space before the comment.
- Read files only through a stage. Writing the `FILES(...)` table function
  yourself, with a storage address, an endpoint or keys, fails with
  `Reading with FILES() is not available: read files through a stage, ...`.
  Use `SELECT * FROM @stage/folder/file.csv` instead; the stage supplies the
  location and the credentials, and your active role needs access to it.
  [Pipes](/docs/sql/pipes) keep their `FILES("path" = "@stage/...")` form.
- `No files were found matching the pattern(s) or path(s): '@stage/...'` means
  the file or folder does not exist. Check the name with `LIST`.
- To write query results into a stage instead of reading from it, see
  [Unload data to a stage](/docs/sql/unload-data-to-stages).
