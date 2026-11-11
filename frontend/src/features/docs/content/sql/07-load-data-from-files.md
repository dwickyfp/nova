---
title: Load data from files
description: Copy rows from files in a stage into a table with COPY INTO or INSERT ... SELECT, and get results out of Nova.
---

## What it is

**Loading** means copying rows from files into a table, so you can query them
quickly and change them later. In Nova the files first go into a
[stage](/docs/data/stages); then one SQL statement copies them into a table.

## Before you start

- Upload the [sample files](/docs/sql/query-files-in-stages#prepare-the-sample-files) to a stage, or substitute files with the same columns.
- Create the target table. Its columns should match the file's columns.
- Your active role needs access to the stage's database and permission to
  insert into the table.

## Load a whole file with COPY INTO

`COPY INTO` copies every column of the file into the table, **in order**: the
file's first column goes into the table's first column, and so on.

The file `new_orders.csv` has the same columns, in the same order, as the
`orders` table:

```text
order_id,customer_id,order_date,amount,status
201,1,2026-06-01,55.00,new
202,2,2026-06-02,18.75,new
203,3,2026-06-02,99.90,shipped
```

```sql
COPY INTO orders FROM @docs_qa_sql_files/new_orders.csv;
```

The result shows `Affected rows: 3`.

Load several files at once with a wildcard:

```sql
COPY INTO orders FROM @docs_qa_sql_files.incoming.*.csv;
```

## Pick and convert columns with INSERT ... SELECT

When the file's columns are in a different order, or you want only some of
them, use `INSERT ... SELECT` and name the columns:

```sql
INSERT INTO orders (order_id, customer_id, order_date, amount)
SELECT order_id, customer_id, order_date, amount
FROM @docs_qa_sql_files/incoming/more_orders.csv;
```

You can filter, join, and transform in the `SELECT` like any other query.

## Load into a new table

Create the table and fill it from a file in one statement:

```sql
CREATE TABLE june_orders AS
SELECT * FROM @docs_qa_sql_files/new_orders.csv;
```

Nova guesses each column's type from the file. In this example `order_date`
and `status` became long text columns. When types matter, create the table
first with the types you want and load it with `COPY INTO` or
`INSERT ... SELECT`.

## Check what was loaded

```sql
SELECT order_id, amount FROM orders WHERE order_id >= 200 ORDER BY order_id;
```

Loads you run in the worksheet appear in its **History** tab like any other
query.

## Get results out of Nova

- In the worksheet, click **Export** above a result to download it as an
  Excel file.
- From a MySQL-compatible tool, run the query and save the output there. See
  [Connect with a MySQL client](/docs/sql/mysql-clients).

## Limits and errors

- **Writing to a stage is blocked in the worksheet.** `COPY INTO @stage ...`,
  `INSERT INTO @stage ...`, `INTO OUTFILE`, and `INSERT INTO FILES(...)` are
  refused with messages such as
  `INTO @stage exports query results and is not read-only.`
- `COPY INTO` matches columns by position, not by name. If the file has extra
  columns or a different order, use `INSERT ... SELECT` with column names.
- JSON files cannot be read. See
  [Query files in a stage](/docs/sql/query-files-in-stages#limits-and-errors).
- `No files were found matching the pattern(s) or path(s)` means the path is
  wrong or the folder is empty. Check it with `LIST @stage`.
