---
title: Load files with Broker Load
description: Load CSV, Parquet, ORC and JSON files from a stage into tables with LOAD LABEL, follow the job with SHOW LOAD, and change or cancel it.
---

## What it is

**Broker Load** loads files from a [stage](/docs/data/stages) into one or more
tables as a **load job**. You start the job with `LOAD LABEL`; the job runs in
the background, and you follow it with `SHOW LOAD`. All files of one job load
together: either every row that passes your checks is loaded, or nothing is.

Use Broker Load instead of [COPY INTO](/docs/sql/load-data-from-files) when you
want any of these:

- many files, or several tables, loaded as one all-or-nothing job;
- column mapping, computed columns (`SET`) and filters (`WHERE`) while loading;
- a tolerance for bad rows (`max_filter_ratio`) and a log of rejected rows;
- JSON files, values taken from folder names (`COLUMNS FROM PATH`), or
  loading into chosen partitions;
- a job you can start and come back to later.

Every file path is a stage path in quotes, such as
`"@compat_w4_stage/w4/input/file1.csv"`. You never type storage addresses or
keys: the stage supplies them, and Nova checks your access to every stage
before the job starts.

## Before you start

- The files are in a stage. Upload them on the stage's page, or write them
  there with an [unload](/docs/sql/unload-data-to-stages).
- Your **active role** needs **read access** to every stage the job reads
  (`SELECT` on the stage's database and schema), and `INSERT` on every target
  table. Write access to the stage is not needed.
- Create the target tables first.
- A load needs no confirmation. A load with `NEGATIVE` (see
  [Undo a load](#undo-a-load-with-negative)) changes existing values, so the
  worksheet asks you to confirm it.
- Run `LOAD LABEL` in the SQL worksheet.

The examples on this page were run in Nova in the database `compat_w4_load`
with the stage `compat_w4_stage`, and each loaded table was compared with the
expected rows. A label can be used once, so the test run added a suffix to
each label.

## Start a load

```sql
LOAD LABEL compat_w4_load.label1
(
    DATA INFILE("@compat_w4_stage/w4/input/file1.csv")
    INTO TABLE table1
    COLUMNS TERMINATED BY ","
    (id, name, score)
    ,
    DATA INFILE("@compat_w4_stage/w4/input/file2.csv")
    INTO TABLE table2
    COLUMNS TERMINATED BY ","
    (id, city)
)
WITH BROKER
PROPERTIES
(
    "timeout" = "3600"
);
```

The statement returns at once with no rows: the job has been accepted. The
**SQL** shown for the run names your stage paths, never storage addresses, and
any key appears as `***`.

The parts of the statement:

| Part | Required | What it is |
| --- | --- | --- |
| `LOAD LABEL [<database>.]<label>` | Yes | The job's name. It must be unique in the database: a label whose job **FINISHED** cannot be used again, so running the same statement twice never loads the same files twice. A label of a **CANCELLED** job can be used again. Without a database, the worksheet's database is used. |
| `( <data description> [, ...] )` | Yes | One or more descriptions of files and their target table, separated by commas. |
| `WITH BROKER` | No | Kept for compatibility with StarRocks. Write it alone or leave it out; Nova adds the stage's credentials itself. |
| `PROPERTIES (...)` | No | Job settings. See [Job properties](#job-properties). |

## Data description clauses

Write the clauses of one data description in this order. Only `DATA INFILE`
and `INTO TABLE` are required.

| Clause | Default | What it does |
| --- | --- | --- |
| `DATA INFILE ("<path>" [, "<path>" ...])` | (required) | The files to load: stage paths, separated by commas. See [Write file paths](#write-file-paths). |
| `NEGATIVE` | Off | Subtracts the files' values instead of adding them. Only for aggregate tables whose value columns all use `SUM`. See [Undo a load](#undo-a-load-with-negative). |
| `INTO TABLE <table>` | (required) | The target table, in the job's database. |
| `PARTITION (<p1>, <p2>, ...)` | All partitions | Loads only into these partitions. Rows that belong to another partition are not loaded and count as unselected. |
| `TEMPORARY PARTITION (<tp1>, ...)` | None | Loads into these temporary partitions instead. |
| `COLUMNS TERMINATED BY "<separator>"` | `\t` (tab) | The CSV column separator, up to 50 bytes. Write a tab as `"\t"` and an invisible character as `"\\x01"`. |
| `ROWS TERMINATED BY "<separator>"` | `\n` | The CSV row separator. |
| `FORMAT AS "<format>"` | From the file name: `.csv`, `.parquet`, `.orc`; otherwise CSV | `CSV`, `Parquet`, `ORC` or `JSON`. Set it when the file name has no extension or a different one. |
| `(<option> = <value> ...)` after `FORMAT AS "CSV"` | See [CSV options](#csv-options) | CSV reading options, separated by spaces, not commas. |
| `(<column>, <column>, ...)` | The table's columns in order | Names the file's columns in file order. A name that matches a table column loads into it; any other name is a temporary name you can use in `SET` and `WHERE`. |
| `COLUMNS FROM PATH AS (<field>, ...)` | None | Takes values from folder names such as `city=beijing/`. See [Values from folder names](#values-from-folder-names). |
| `SET (<column> = <expression>, ...)` | None | Computes table columns from file columns with any SQL function. |
| `WHERE <condition>` | None | Loads only rows that meet the condition. Rows left out count as unselected, not as errors. |

### CSV options

| Option | Default | What it does |
| --- | --- | --- |
| `skip_header = <n>` | `0` | Skips the first `n` rows of each file, for example a header row. |
| `trim_space = TRUE` | `FALSE` | Removes spaces before and after each separator, but not inside an enclosed value. |
| `enclose = "<char>"` | None | The character that wraps a value, usually `"`. Separators inside are part of the value. A doubled enclose character inside a value stands for one. |
| `escape = "<char>"` | None | The escape character, usually `\` written as `"\\"`. The next character is taken literally, inside and outside enclosed values. |

## Job properties

| Property | Default | What it does |
| --- | --- | --- |
| `timeout` | `14400` (4 hours) | Seconds before an unfinished job is cancelled. |
| `max_filter_ratio` | `0` | The share of rows (0 to 1) that may be rejected for bad data before the job fails. `0` fails the job on the first bad row. Rows left out by `WHERE` do not count. |
| `log_rejected_record_num` | `0` | How many rejected rows to keep for inspection: `0` none, `-1` all, `n` up to `n` per server. |
| `load_mem_limit` | 2 GB | Memory limit of the job, in bytes. |
| `strict_mode` | `false` | `true` rejects a row when a value cannot be converted to the column type, instead of loading `NULL`. |
| `timezone` | The server's time zone | The time zone for time functions in `SET` and `WHERE`. |
| `priority` | `NORMAL` | `LOWEST`, `LOW`, `NORMAL`, `HIGH` or `HIGHEST`: the order in which waiting jobs start. Change it later with `ALTER LOAD`. |
| `partial_update` | `false` | `true` updates only the columns you load in a primary key table and keeps the others. |
| `partial_update_mode` | `row` | `row` or `column`: how a partial update is applied. `column` suits many rows and few columns. |
| `merge_condition` | None | A column of a primary key table: a row replaces the existing one only if its value in this column is greater or equal. |
| `jsonpaths` | None | For JSON: which keys to load, in order, as a JSON array of paths, for example `"[\"$.category\",\"$.price\"]"`. |
| `strip_outer_array` | `false` | For JSON: `true` treats each element of an outer `[ ... ]` array as one row. |
| `json_root` | Whole document | For JSON: the path of the element that holds the rows, for example `"$.RECORDS"`. |
| `warehouse` | `default_warehouse` | The warehouse that runs the job. |

Other properties are refused. Properties that name storage credentials,
endpoints, users or brokers are refused without repeating their values.

## Write file paths

| You write | Nova reads |
| --- | --- |
| `"@compat_w4_stage/w4/input/file1.csv"` | One file. |
| `"@compat_w4_stage/w4/all/*"` | Every file in the folder `w4/all`. |
| `"@compat_w4_stage/w4/dir/city=beijing/*/*"` | Every file one folder below `city=beijing`. |
| `"@compat_w4_stage/w4/{a,b}/[0-9]?.csv"` | Glob patterns: `*`, `?`, `[ ]`, `{ , }` and `^`. |
| `"@db.schema.stage/folder/file.csv"` | A stage in another database, written in full. |

- Put a `/` and a file or pattern after the stage name.
- Folder and file names may use letters, digits, `_`, `-`, `.`, `=`, `%`,
  spaces and the glob characters above. Empty folders (`//`), `.` and `..` are
  refused, so a job never reads outside the stage.
- All files of one job must be in stages on the same storage connection, so
  that one set of credentials reads them all.

## Examples

### One file, every file in a folder

```sql
LOAD LABEL label_7
(
    DATA INFILE("@compat_w4_stage/w4/one/file1.csv")
    INTO TABLE table1
    COLUMNS TERMINATED BY ","
    FORMAT AS "CSV"
)
WITH BROKER;

LOAD LABEL label_8
(
    DATA INFILE("@compat_w4_stage/w4/all/*")
    INTO TABLE table1
    COLUMNS TERMINATED BY ","
    FORMAT AS "CSV"
);
```

### Allow some bad rows

Ten good rows and one row with too few columns: with `max_filter_ratio` 0.1
the job finishes and loads the ten good rows. With the default `0` it fails.

```sql
LOAD LABEL label2
(
    DATA INFILE("@compat_w4_stage/w4/data/input/example2.csv")
    INTO TABLE table2
)
PROPERTIES ("max_filter_ratio" = "0.1");
```

### Separators

```sql
LOAD LABEL label3
(
    DATA INFILE("@compat_w4_stage/w4/data/input3/*")
    INTO TABLE table3
    COLUMNS TERMINATED BY "\\x01"
)
WITH BROKER;

LOAD LABEL label5
(
    DATA INFILE("@compat_w4_stage/w4/data/input/example5.csv")
    INTO TABLE table5
    COLUMNS TERMINATED BY "\t"
)
WITH BROKER;
```

### Undo a load with NEGATIVE

`table6` is an aggregate table whose value column `col3` uses `SUM`. Loading
the same file again with `NEGATIVE` subtracts its values, so after both loads
`col3` is `0` for every row the file touched. Nova asks you to confirm.

```sql
CREATE TABLE table6 (col1 INT, col2 STRING, col3 INT SUM)
AGGREGATE KEY(col1, col2) DISTRIBUTED BY HASH(col1);

LOAD LABEL label6
(
    DATA INFILE("@compat_w4_stage/w4/data/input/example6.csv")
    NEGATIVE
    INTO TABLE table6
    COLUMNS TERMINATED BY "\t"
)
WITH BROKER;
```

### Load into chosen partitions

```sql
LOAD LABEL label7
(
    DATA INFILE("@compat_w4_stage/w4/data/input/example7.csv")
    INTO TABLE table7
    PARTITION (p1, p2)
    COLUMNS TERMINATED BY ","
)
WITH BROKER;

ALTER TABLE table7t ADD TEMPORARY PARTITION tp1 VALUES LESS THAN ('10');

LOAD LABEL label7t
(
    DATA INFILE("@compat_w4_stage/w4/data/input/example7t.csv")
    INTO TABLE table7t
    TEMPORARY PARTITION (tp1)
    COLUMNS TERMINATED BY ","
);
```

### Map, compute and filter columns

The file's columns are in the order `col2, col1, col3`:

```sql
LOAD LABEL label8
(
    DATA INFILE("@compat_w4_stage/w4/data/input/example8.csv")
    INTO TABLE table8
    COLUMNS TERMINATED BY ","
    (col2, col1, col3)
)
WITH BROKER;
```

Only rows whose first column is greater than `20180601`:

```sql
LOAD LABEL label9
(
    DATA INFILE("@compat_w4_stage/w4/data/input/example9.csv")
    INTO TABLE table9
    (col1, col2, col3)
    WHERE col1 > 20180601
)
WITH BROKER;
```

HLL columns computed with `SET`:

```sql
LOAD LABEL label10
(
    DATA INFILE("@compat_w4_stage/w4/data/input/example10.csv")
    INTO TABLE table10
    COLUMNS TERMINATED BY ","
    (id, temp1, temp2)
    SET (col1 = hll_hash(temp1), col2 = hll_hash(temp2), col3 = hll_empty())
)
WITH BROKER;
```

The StarRocks guide writes `empty_hll()`; the function is `hll_empty()`.

### Values from folder names

Files in `w4/dir/city=beijing/utc_date=2019-06-26/` get `city` and
`utc_date` from their folder names:

```sql
LOAD LABEL label11
(
    DATA INFILE("@compat_w4_stage/w4/dir/city=beijing/*/*")
    INTO TABLE table11
    COLUMNS TERMINATED BY ","
    FORMAT AS "csv"
    (col1, col2, col3)
    COLUMNS FROM PATH AS (city, utc_date)
    SET (uniq_id = md5sum(col1, city))
)
WITH BROKER;
```

A folder name may hold `%3A` for `:`. Convert it with `SET`:

```sql
LOAD LABEL label12
(
    DATA INFILE("@compat_w4_stage/w4/data12/*/example12.csv")
    INTO TABLE table12
    COLUMNS TERMINATED BY ","
    FORMAT AS "csv"
    (col1, col2)
    COLUMNS FROM PATH AS (data_time)
    SET (data_time = str_to_date(data_time, '%Y-%m-%d %H%%3A%i%%3A%s'))
)
WITH BROKER;
```

### Header rows, spaces and quotes

Skip two header rows, trim spaces, and read `"a, b"` and `"say \"hi\""` as
one value each:

```sql
LOAD LABEL label13
(
    DATA INFILE("@compat_w4_stage/w4/d13/*/example13.csv")
    INTO TABLE table13
    COLUMNS TERMINATED BY ","
    FORMAT AS "CSV"
    (
        skip_header = 2
        trim_space = TRUE
        enclose = "\""
        escape = "\\"
    )
    (col2, col1, col3)
)
WITH BROKER
PROPERTIES ("timeout" = "3600");
```

### Parquet and ORC

Columns are matched by name.

```sql
LOAD LABEL label13p
(
    DATA INFILE("@compat_w4_stage/w4/data/input/example13.parquet")
    INTO TABLE table13p
    FORMAT AS "parquet"
    (col1, col2, col3)
)
WITH BROKER;

LOAD LABEL label14
(
    DATA INFILE("@compat_w4_stage/w4/data/input/example14.orc")
    INTO TABLE table14
    FORMAT AS "orc"
    (col1, col2, col3)
)
WITH BROKER;
```

### JSON

Keys that match the table's column names load by name:

```sql
LOAD LABEL label15
(
    DATA INFILE("@compat_w4_stage/w4/data/input/example1.json")
    INTO TABLE tbl1
    FORMAT AS "json"
)
WITH BROKER;
```

Choose keys with `jsonpaths`, read each element of an outer array as a row,
and start at a nested element with `json_root`:

```sql
LOAD LABEL label16
(
    DATA INFILE("@compat_w4_stage/w4/data/input/example2.json")
    INTO TABLE tbl1
    FORMAT AS "json"
    (category, price, author)
)
WITH BROKER
PROPERTIES
(
    "strip_outer_array" = "true",
    "jsonpaths" = "[\"$.category\",\"$.price\",\"$.author\"]"
);

LOAD LABEL label17
(
    DATA INFILE("@compat_w4_stage/w4/data/input/example3.json")
    INTO TABLE tbl1
    FORMAT AS "json"
    (category, price, author)
)
WITH BROKER
PROPERTIES
(
    "json_root" = "$.RECORDS",
    "strip_outer_array" = "true",
    "jsonpaths" = "[\"$.category\",\"$.price\",\"$.author\"]"
);
```

A key missing from a record loads `NULL`.

### Update a primary key table

```sql
LOAD LABEL props
(
    DATA INFILE("@compat_w4_stage/w4/props/pk.csv")
    INTO TABLE pk
    COLUMNS TERMINATED BY ","
    (id, v, ts)
)
PROPERTIES
(
    "strict_mode" = "true",
    "timezone" = "UTC",
    "priority" = "HIGH",
    "load_mem_limit" = "1073741824",
    "log_rejected_record_num" = "10",
    "merge_condition" = "ts"
);

LOAD LABEL partial
(
    DATA INFILE("@compat_w4_stage/w4/props/pk2.csv")
    INTO TABLE pk2
    COLUMNS TERMINATED BY ","
    (id, a)
)
PROPERTIES ("partial_update" = "true", "partial_update_mode" = "row");
```

With `merge_condition` = `ts`, a file row replaces a table row only when its
`ts` is greater or equal. With `partial_update`, column `b` of `pk2` keeps its
value.

## Follow a load

```sql
SHOW LOAD WHERE LABEL = "label1";
SHOW LOAD FROM compat_w4_load WHERE STATE = "CANCELLED";
SELECT LABEL, STATE, ERROR_MSG FROM information_schema.loads WHERE DB_NAME = "compat_w4_load";
```

`SHOW LOAD` lists the jobs of the worksheet's database, or of the database
after `FROM`, that your role may see. `information_schema.loads` holds the same
jobs as a table. The columns you will use most:

| Column (`SHOW LOAD` / `loads`) | Meaning |
| --- | --- |
| `State` / `STATE` | `PENDING` and `QUEUEING` (waiting), `LOADING`, `FINISHED`, or `CANCELLED` (failed or cancelled). |
| `Progress` / `PROGRESS` | For example `ETL:100%; LOAD:100%`. |
| `ScanRows`, `SinkRows`, `FilteredRows`, `UnselectedRows` | Rows read, loaded, rejected for bad data, and left out by `WHERE` or `PARTITION`. |
| `ErrorMsg` / `ERROR_MSG` | Why the job was cancelled. File paths are shown as stage paths. |
| `TrackingSQL` / `TRACKING_SQL` | A query that returns the rejected rows, when the job kept any. |
| `JobDetails` | File count and bytes read, and the servers that ran the job. |

Paths in these results name the stage, for example
`'@compat_w4_stage/w4/nope/*.csv'`. A path in a stage your role cannot read
shows as `<external location>`. Rejected rows are listed by the query in
`TrackingSQL`, for example:

```sql
SELECT tracking_log FROM information_schema.load_tracking_logs WHERE label = "probe3";
```

## Change or cancel a load

Raise the priority of a job that is waiting or loading:

```sql
ALTER LOAD FOR compat_w4_load.managed PROPERTIES ("priority" = "HIGHEST");
```

Cancel a job that has not finished. Its rows are not loaded:

```sql
CANCEL LOAD FROM compat_w4_load WHERE LABEL = "managed";
```

A job that already finished cannot be cancelled or changed; the engine says
so and nothing changes.

## Limits and errors

| Message | Meaning |
| --- | --- |
| `LOAD LABEL reads files only from a stage. Write each path as "@<stage>/<folder>/<file>" ...` | A path was a storage address or a local path. Use a stage path. |
| `LOAD LABEL from a stage takes no broker name or broker properties ...` | `WITH BROKER` had a name or settings such as keys. Write `WITH BROKER` alone. |
| `LOAD LABEL from a stage takes no storage location, credential or broker properties ...` | A property named a key, endpoint, user or broker. |
| `LOAD LABEL does not support the property '<name>'` | The property is not in [Job properties](#job-properties). |
| `LOAD LABEL: a stage path may use letters, digits, ...` | The path had `..`, `.`, an empty folder or another character. |
| `LOAD ... WITH RESOURCE (Spark Load) is not available in Nova` | Spark Load is not supported. Use `WITH BROKER`. |
| `DATA FROM TABLE loads from a Hive table through Spark Load ...` | Not supported. Load files with `DATA INFILE`. |
| `All files of one LOAD must be in stages on the same storage connection` | Split the job, one per storage connection. |
| `Stage access denied` | Your active role cannot read one of the stages. Nothing was started. |
| `Label [...] has already been used` | Choose a new label. |
| `No files were found matching the pattern(s) or path(s): '@...'` | The job was cancelled because the path matched no file. Check it with `LIST @stage`. |
| `type:ETL_QUALITY_UNSATISFIED; msg:quality issues with ingested data ...` | More rows were rejected than `max_filter_ratio` allows. Run the query in `TrackingSQL` to see them. |

- Stage reads use the stage's own credentials; you cannot pass your own.
- HDFS name-node, Kerberos and cloud credential settings from the StarRocks
  guide do not apply: the stage's storage connection decides how files are read.
- `SHOW LOAD` and `information_schema.loads` show only jobs your role may see.
  `REJECTED_RECORD_PATH` names a location on a database server, not in a stage.

## See also

- [Load data from files](/docs/sql/load-data-from-files): `COPY INTO` and `INSERT ... SELECT`.
- [Pipes](/docs/sql/pipes): load new files as they arrive.
- [Unload data to a stage](/docs/sql/unload-data-to-stages).
