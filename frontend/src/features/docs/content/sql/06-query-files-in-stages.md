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
@docs_qa_sql_files                             0     1       2026-11-11 09:43:47
@docs_qa_sql_files/incoming                    0     1       ...
@docs_qa_sql_files/incoming/more_orders.csv    73    0       2026-11-11 09:43:34
@docs_qa_sql_files/new_orders.csv              131   0       2026-11-11 09:43:24
```

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
- `No files were found matching the pattern(s) or path(s): '@stage/...'` means
  the file or folder does not exist. Check the name with `LIST`.
- Writing query results into a stage (`COPY INTO @stage`, `INSERT INTO @stage`)
  is not allowed from the worksheet. See
  [Load data from files](/docs/sql/load-data-from-files#limits-and-errors).
