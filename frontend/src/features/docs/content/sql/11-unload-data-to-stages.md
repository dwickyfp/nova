---
title: Unload data to a stage
description: Write query results or whole tables to CSV, Parquet, or ORC files in a stage with INSERT INTO FILES, SELECT ... INTO OUTFILE, EXPORT TABLE, or COPY INTO.
---

## What it is

**Unloading** writes rows out of Nova into files. The files go into a
[stage](/docs/data/stages), a named folder of files that Nova manages. You
can then download them from the stage, read them back with
`SELECT * FROM @stage/...`, or load them somewhere else.

Nova has four ways to unload. All of them write only to a stage:

| Statement | Best for | File formats | Runs |
| --- | --- | --- | --- |
| `INSERT INTO FILES(...) SELECT ...` | Any query, with the most options: compression, file size, one file, folders by column value | CSV, Parquet, ORC | Now; the result appears when the files are written |
| `SELECT ... INTO OUTFILE '...'` | One query's results with a file name prefix you choose | CSV, Parquet | Now |
| `EXPORT TABLE ...` | A whole table, some partitions, or some columns | CSV | In the background; check it with `SHOW EXPORT` |
| `COPY INTO @stage ...`, `INSERT INTO @stage ...` | A quick CSV copy of a query | CSV | Now |

You write the target as a stage path in quotes, for example
`'@sales_exports/daily/'`. You never type storage addresses or keys: the stage
supplies them, and Nova checks your access before it writes.

## Before you start

- The stage must exist. See [Create a stage](/docs/data/stages#create-a-stage).
- Your **active role** needs **write access** to the stage: `INSERT` (or `ALL`)
  on the stage's database and schema. This is the same permission that lets
  you upload files to the stage. Reading a stage needs only `SELECT`, so a role
  that can read a stage cannot always write to it.
- Your active role needs `SELECT` on the tables you unload. `EXPORT TABLE`
  also needs the `EXPORT` privilege on the table.
- Run unloads in the **SQL worksheet**. Nova asks you to confirm each one
  (see [Confirm the unload](#confirm-the-unload)). MySQL clients and the
  assistant's read-only query tool refuse unloads.

The examples use a database and a stage named `compat_unload_w2` and this
table, from the StarRocks unloading guide:

```sql
CREATE TABLE sales_records(
    record_id     BIGINT,
    seller        STRING,
    store_id      INT,
    sales_time    DATETIME,
    sales_amt     DOUBLE
)
DUPLICATE KEY(record_id)
PARTITION BY date_trunc('day', sales_time)
DISTRIBUTED BY HASH(record_id);

INSERT INTO sales_records VALUES
    (220313001,'Amy',1,'2022-03-13 12:00:00',8573.25),
    (220314002,'Bob',2,'2022-03-14 12:00:00',6948.99),
    (220314003,'Amy',1,'2022-03-14 12:00:00',4319.01),
    (220315004,'Carl',3,'2022-03-15 12:00:00',8734.26),
    (220316005,'Carl',3,'2022-03-16 12:00:00',4212.69),
    (220317006,'Bob',2,'2022-03-17 12:00:00',9515.88);
```

Every example on this page was run in Nova, and the files it wrote were read
back and compared with the table row by row.

## Write the stage path

| You write | Files go to |
| --- | --- |
| `'@compat_unload_w2/daily/'` | The folder `daily` in the stage. Ending with `/` means "a folder". |
| `'@compat_unload_w2/daily/sales_'` | The folder `daily`, with file names starting `sales_`. |
| `'@compat_unload_w2/'` | The top of the stage. |
| `'@compat_unload_w2.compat_unload_w2.compat_unload_w2/daily/'` | The same stage written in full as `@<database>.<schema>.<stage>`, when another database is selected. |

Rules:

- Put a `/` and at least a folder or a name after the stage name.
  `'@compat_unload_w2'` alone is refused, so files can never land outside the
  stage's folder.
- Folder names may use letters, digits, `_`, `-`, `.` and `=`. Empty folders
  (`//`), `.` and `..` are refused. Wildcards such as `*` are for reading only.
- Single or double quotes both work: `'@s/a/'` and `"@s/a/"` are the same.
- Unloading into a folder that already has files leaves them there and adds
  the new ones. A file with the same name is replaced: `INTO OUTFILE` always
  starts at `<prefix>0`, so running the same `INTO OUTFILE` twice replaces
  `sales_0.csv`. `INSERT INTO FILES` and `EXPORT TABLE` put the query ID in
  their file names, so their files do not collide. Use a new folder per run if
  you will read a folder back as one data set.

## Confirm the unload

An unload copies data out of the governed tables into files, so the worksheet
treats it like a delete or a drop: when you click **Run**, it shows
**Run destructive query?**. Review the SQL and click **Run query** to write
the files, or cancel to write nothing. A script that contains an unload asks
once for the whole script.

## INSERT INTO FILES

```sql
INSERT INTO FILES(
    "path" = "@compat_unload_w2/daily/",
    "format" = "parquet",
    "compression" = "uncompressed"
)
SELECT * FROM sales_records;
```

The `SELECT` can be any query: filter, join, aggregate, or read from another
stage. Columns keep their names and types in Parquet and ORC files.

### Options

Write each option as `"name" = "value"` inside `FILES(...)`, separated by commas.
`path` is required. Option names are not case sensitive.

| Option | Default | What it does |
| --- | --- | --- |
| `path` | (required) | The stage folder, or folder plus file name prefix, to write to. |
| `format` | (required) | `csv`, `parquet`, or `orc`. |
| `compression` | The engine's default for the format | `uncompressed`, `gzip`, `snappy`, `zstd`, or `lz4`. The StarRocks guide asks you to always set it; Nova's engine also accepts Parquet and ORC unloads without it. CSV files cannot be compressed: use `uncompressed` or leave it out. |
| `target_max_file_size` | `1073741824` (1 GB) | The size in bytes at which Nova starts a new file. A best effort: a file can be slightly larger. Use hundreds of MB or more for real data. |
| `single` | `false` | `true` writes everything into one file, whatever its size. |
| `partition_by` | none | One or more column names, separated by commas. Rows go into subfolders named `<column>=<value>`, for example `store_id=1/`. The column stays in the files too. |
| `csv.column_separator` | `\t` (tab) | The text between values in CSV files, up to 50 bytes, such as `,` or `|`. |
| `csv.row_delimiter` | `\n` | The text between rows in CSV files. |
| `csv.include_header` | `false` | `true` writes the column names as the first line of each CSV file. |
| `csv.enclose` | none | One ASCII character, usually `"`, put around every value that is not NULL. |
| `csv.escape` | none | One ASCII character used to escape the enclose character inside a value. `"` gives doubled quotes (`""`), the common CSV convention. |
| `parquet.version` | `2.6` | The Parquet format version: `1.0`, `2.4`, or `2.6`. Use `1.0` for old readers. |
| `parquet.use_legacy_encoding` | `false` | `true` writes DATETIME as INT96 and DECIMAL as fixed-length bytes, for older tools. |

Any other option is refused, with the list above in the message. Storage
addresses, keys, endpoints, regions and user names are never accepted: the
stage supplies them.

### Where the files go

Without `single`, Nova names each file after the query that wrote it, for
example `@compat_unload_w2/daily/01a1e4b7-d396-7da7-867c-40b45d5f844d_0_0_0.parquet`.
A path that ends in a name instead of `/`, such as `"@compat_unload_w2/daily/data1"`,
creates a folder with that name and writes into it. Run `LIST` to see what was written:

```sql
LIST @compat_unload_w2/daily;
```

### Files of a chosen size

```sql
INSERT INTO FILES(
    "path" = "@compat_unload_w2/multi/data1",
    "format" = "parquet",
    "compression" = "uncompressed",
    "target_max_file_size" = "1024" -- 1 KB
)
SELECT * FROM sales_records;
```

The size is checked as rows are written, so a file is closed only after it
passes the limit. With the six example rows this still writes one file of
about 1.3 KB, in the folder `multi/data1/`; a larger result is split into many
files.

### Folders by column value

```sql
INSERT INTO FILES(
    "path" = "@compat_unload_w2/partitioned/",
    "format" = "parquet",
    "compression" = "lz4",
    "partition_by" = "store_id"
)
SELECT * FROM sales_records;
```

```text
PATH                                                    SIZE  IS_DIR
@compat_unload_w2/partitioned/store_id=1                0     1
@compat_unload_w2/partitioned/store_id=1/01a1e4c4-..._0_6_0.parquet  1269  0
@compat_unload_w2/partitioned/store_id=2                0     1
...
```

Read all of them back with a wildcard per folder level:

```sql
SELECT * FROM @compat_unload_w2.partitioned.*.*.parquet ORDER BY record_id;
```

### One file

```sql
INSERT INTO FILES(
    "path" = "@compat_unload_w2/single/data2",
    "format" = "parquet",
    "compression" = "zstd",
    "single" = "true"
)
SELECT * FROM sales_records;
```

### CSV files

```sql
INSERT INTO FILES(
    'path' = '@compat_unload_w2/csv/',
    'format' = 'csv',
    'compression' = 'uncompressed',
    'csv.column_separator' = ',',
    'csv.row_delimiter' = '\n'
)
SELECT * FROM sales_records;
```

With a header line and values in quotes, the way spreadsheets expect:

```sql
INSERT INTO FILES(
    'path' = '@compat_unload_w2/csv_enclosed/',
    'format' = 'csv',
    'compression' = 'uncompressed',
    'csv.column_separator' = ',',
    'csv.row_delimiter' = '\n',
    'csv.enclose' = '"',
    'csv.escape' = '"',
    'csv.include_header' = 'true'
)
SELECT * FROM sales_records;
```

```text
"record_id","seller","store_id","sales_time","sales_amt"
"220315004","Carl","3","2022-03-15 12:00:00","8734.26"
"220316005","Carl","3","2022-03-16 12:00:00","4212.69"
```

Rows are written in the order the query returns them; add `ORDER BY` if the
order matters. A quote inside a value is doubled, and a NULL value is written
as `\N` without quotes. This table of notes, unloaded with
`'csv.column_separator' = '|'`, `'csv.enclose' = '"'` and `'csv.escape' = '"'`:

```text
"1"|"plain"
"2"|"has, comma"
"3"|"say ""hi"""
"4"|\N
```

### Parquet for older readers, and ORC

```sql
INSERT INTO FILES(
    'path' = '@compat_unload_w2/parquet_v1/',
    'format' = 'parquet',
    'compression' = 'snappy',
    'parquet.version' = '1.0',
    'parquet.use_legacy_encoding' = 'true'
)
SELECT * FROM sales_records;

INSERT INTO FILES(
    'path' = '@compat_unload_w2/orc/',
    'format' = 'orc',
    'compression' = 'zstd'
)
SELECT * FROM sales_records;
```

## SELECT INTO OUTFILE

Add `INTO OUTFILE` after a query. The path ends with the file name prefix;
Nova adds a number and the extension.

```sql
SELECT * FROM sales_records
INTO OUTFILE '@compat_unload_w2/outfile_csv/sales_'
FORMAT AS CSV
PROPERTIES ('column_separator' = ',', 'line_delimiter' = '\n');
```

This writes `@compat_unload_w2/outfile_csv/sales_0.csv`. A larger result
continues with `sales_1.csv`, and so on.

```sql
SELECT * FROM sales_records
INTO OUTFILE '@compat_unload_w2/outfile_parquet/sales_'
FORMAT AS PARQUET
PROPERTIES ('compression_type' = 'zstd', 'use_dictionary' = 'false');
```

| Option | Default | What it does |
| --- | --- | --- |
| `FORMAT AS` | `CSV` | `CSV` or `PARQUET`. ORC is refused: `Only support CSV and PARQUET format.` |
| `column_separator` | `\t` | CSV only. The text between values. |
| `line_delimiter` | `\n` | CSV only. The text between rows. |
| `max_file_size` | `1073741824` (1 GB) | The largest file in bytes, between 5 MB and 2 GB. Larger results continue in the next file. |
| `compression_type` | `snappy` | Parquet only: `snappy`, `gzip`, `brotli`, `zstd`, `lz4`, `zlib`, `uncompressed` or `none`. |
| `use_dictionary` | `true` | Parquet only. `false` turns off dictionary encoding. |
| `max_row_group_bytes` | `134217728` (128 MB) | Parquet only. The size of each row group in bytes. |

`PROPERTIES` is optional. Other options, and every storage or broker option,
are refused.

## EXPORT TABLE

`EXPORT TABLE` copies a table, or part of it, into CSV files. It starts a
background job and returns at once; the files appear when the job finishes.

```sql
EXPORT TABLE sales_records
TO "@compat_unload_w2/export_all/"
WITH BROKER;
```

Choose partitions, columns, and a file name prefix:

```sql
EXPORT TABLE compat_unload_w2.testTbl
PARTITION (p1, p2)
(col1, col3)
TO "@compat_unload_w2/export_cols/lineorder_"
PROPERTIES (
    "column_separator" = ",",
    "load_mem_limit" = "2147483648",
    "timeout" = "3600"
)
WITH BROKER;
```

- `PARTITION (...)` limits the export to those partitions. Without it, every
  partition is exported.
- `(col1, col3)` limits and orders the columns. Without it, every column is
  exported in table order.
- `WITH BROKER` is accepted for compatibility and can be left out. Do not add
  a broker name or properties after it; the stage supplies the connection.
- `WITH SYNC MODE` makes the statement wait for the job to finish and
  return its `SHOW EXPORT` row, with the stage path:

```sql
EXPORT TABLE sales_records TO '@compat_unload_w2/tmp_sync/' WITH SYNC MODE;
```

```text
JobId    QueryId                               State     Progress  Path
3112268  01a1e50f-6a11-7e54-a055-2152d21207ee  FINISHED  100%      @compat_unload_w2/tmp_sync/
```

| Option | Default | What it does |
| --- | --- | --- |
| `column_separator` | `\t` | The text between values. Write `\\x01` for the Hive separator. |
| `line_delimiter` | `\n` | The text between rows. |
| `load_mem_limit` | `2147483648` (2 GB) | The memory one export task may use per compute node, in bytes. Raise it if a big export fails for memory. |
| `timeout` | `86400` (1 day; the server may cap it lower) | Seconds before the job is cancelled. |
| `include_query_id` | `true` | `false` leaves the job's query ID out of the file names, for example `data_0_0.csv`. |

### Export file names

Files are named `<prefix><query ID>_<task>_<part>.csv`. The prefix is the text
after the last `/` in the path, or `data_` when the path ends with `/`, for
example `@compat_unload_w2/export_all/data_01a1e4b8-39c0-77af-9605-3197e41f40a6_0_0.csv`.
While a job runs, its files are written to a temporary folder named
`__starrocks_export_tmp_<id>` in the same folder and moved into place at the end.

### Check or cancel an export

```sql
SHOW EXPORT ORDER BY CreateTime DESC LIMIT 5;
```

```text
JobId    QueryId                               State     Progress  Path
3111433  01a1e4b8-39c0-77af-9605-3197e41f40a6  FINISHED  100%      @compat_unload_w2/x1/
```

`State` is `PENDING`, `EXPORTING`, `FINISHED`, or `CANCELLED`. A cancelled or
failed job explains why in `ErrorMsg`. `Path` shows the stage path. Exports to
a location that is not a stage you can read, such as jobs started outside Nova,
show `<external location>`.

Filter and sort like this:

```sql
SHOW EXPORT WHERE QUERYID = "01a1e4b8-39c0-77af-9605-3197e41f40a6";
SHOW EXPORT FROM compat_unload_w2 WHERE STATE = "FINISHED" ORDER BY StartTime ASC;
```

Cancel a job that has not finished:

```sql
CANCEL EXPORT WHERE queryid = "01a1e4b8-39c0-77af-9605-3197e41f40a6";
```

A finished or already cancelled job cannot be cancelled. Files a cancelled
job had already moved into place stay in the stage.

## COPY INTO and INSERT INTO a stage

Nova's short forms write CSV files with a comma between values:

```sql
COPY INTO @compat_unload_w2/copy_into/ FROM (SELECT * FROM sales_records);
COPY INTO @compat_unload_w2/copy_table/ FROM sales_records;
INSERT INTO @compat_unload_w2/insert_stage/ SELECT * FROM sales_records;
```

Here the stage path is not in quotes. Use `INSERT INTO FILES` when you need
another format or any option.

## Read the files back

```sql
SELECT * FROM @compat_unload_w2.daily.*.parquet ORDER BY record_id;
SELECT * FROM @compat_unload_w2.outfile_csv.*.csv;
```

See [Query files in a stage](/docs/sql/query-files-in-stages). You can also
download the files from the stage in **Database Explorer**.

## Limits and errors

| Message | What it means |
| --- | --- |
| `Destructive SQL requires confirmation before execution.` | The unload was not confirmed. In the worksheet, click **Run query** in the **Run destructive query?** dialog. |
| `INSERT INTO FILES writes only to a stage. Write the target as '@<stage>/<folder>/', ...` | The path is a storage address, a local path, or plain text. Use a stage path. The same message names `SELECT ... INTO OUTFILE` or `EXPORT TABLE`. |
| `INSERT INTO FILES needs a folder or file name prefix after the stage name, ...` | Add `/` and a folder after the stage name. |
| `INSERT INTO FILES: the stage path may use letters, digits, ...` | The path has an empty folder, `.`, `..`, a space or a wildcard. |
| `INSERT INTO FILES to a stage takes no storage location, credential or broker options: the stage supplies them` | Remove keys, endpoints, regions, user names and broker settings. |
| `INSERT INTO FILES does not support the option 'csv.row_delimitor'. Supported options: ...` | An unknown or misspelled option. This one is a typo in some StarRocks examples: write `csv.row_delimiter`. |
| `INSERT INTO FILES sets the option 'format' more than once` | Each option may appear once. |
| `INSERT INTO FILES needs a 'path' that names a stage folder, ...` | Add `"path" = "@<stage>/<folder>/"`. |
| `EXPORT TABLE to a stage takes no broker name or broker properties: ...` | Write `WITH BROKER` alone, or leave it out. |
| `Stage access denied` | Your active role may not write to this stage. Switch to a role with `INSERT` on the stage's database and schema, or ask for access. |
| `Stage '<name>' not found in <database>` | The stage is in another database. Select that database or write `@<database>.<schema>.<stage>/...`. |
| `Only support CSV and PARQUET format.` | `INTO OUTFILE` cannot write ORC. Use `INSERT INTO FILES` with `'format' = 'orc'`. |
| `... is not read-only.` | The statement was sent from a MySQL client or a read-only assistant tool, which never unload. Run it in the worksheet. |

- An unload is not undone by cancelling the query. Delete unwanted files from
  the stage in **Database Explorer**.
- `EXPORT TABLE` exports only tables, not views or materialized views.
- `SHOW EXPORT` lists the export jobs of the database, including jobs other
  users started. A path shows as `@stage/...` only for stages your active role
  may read; anything else shows as `<external location>`.
- Reading CSV files back with `SELECT * FROM @stage/...` uses Nova's CSV
  detection: it guesses the separator and whether the first line is a header.
  Write CSV files with `'csv.include_header' = 'true'` and a comma separator
  when you will read them back in Nova. A file whose first line is data can be
  read with that line taken as column names, and a NULL written as `\N` inside
  quoted CSV can come back as the text `N`. The files themselves are correct;
  download them to check.
- `SELECT LAST_QUERY_ID()` in a later statement does not return an export's
  ID, because each worksheet statement may use its own connection. Use
  `SHOW EXPORT ORDER BY CreateTime DESC LIMIT 1` instead.
