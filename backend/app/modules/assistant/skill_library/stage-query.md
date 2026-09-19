---
name: stage-query
title: Query files through @stage
summary: Read and load files with Nova's @stage abstraction, which rewrites to FILES() with credentials injected.
triggers: stage, @stage, file, csv, parquet, load file, read file, copy into, s3, bucket, upload, export
source: docs/sql_docs/02-stage-queries.md, docs/sql_docs/01-dialect-pipeline.md
---

# Skill: stage-query

`@stage` is Nova's file-access abstraction and the **required** way to read files.
Never write a storage path (S3/MinIO/Azure/GCS) or a credential in SQL; `@stage`
exists so the user never types one. Nova rewrites `@stage` to a `FILES(...)` call
and injects credentials before StarRocks sees it.

## Forms

```
@name                          -- the stage itself
@name/                         -- a directory
@name.file.csv                 -- a file (extension sets the format)
@silver.stage1.folder.file.parquet   -- cross-schema (schema.stage.path…)
```

## Stage vs. user variable (position decides)

- A token with a dotted path or a trailing `/`, or one that follows
  `FROM`/`JOIN`/`INTO`/`LIST`/`FILES`/`USING`, is a **stage**.
- In an expression (`SELECT @x`, `1 + @n`, `SET @x = 1`) it is a **user variable**.
- `@@name` is never a stage.
- `@x` inside a string literal or a comment is data, not a stage.

## Templates

Read a CSV file:

```sql
SELECT * FROM @stage1.data.csv
```

Cross-schema read:

```sql
SELECT * FROM @silver.stage1.folder.file.parquet
```

Load into a table:

```sql
COPY INTO NOVA_DEMO.orders FROM @stage1.orders.csv
```

Export from a table:

```sql
COPY INTO @stage1.exports FROM NOVA_DEMO.orders
```

## What Nova injects (do not write these yourself)

`FILES('path'=…, 'format'=…)` plus CSV properties
(`csv.column_separator`, `csv.trim_space`, `csv.skip_header`, `csv.enclose`,
`csv.escape`) and storage credentials
(`aws.s3.access_key`, `aws.s3.secret_key`, `aws.s3.endpoint`, …). These are added
on the translated statement and redacted (`***`) before audit/return.

## Errors and limits

- Unknown stage → `Stage '…' not found` (audited as ERROR).
- No file extension → defaults to CSV with a warning.
- `LIST @stage` parses but the engine has no `LIST`; do not promise it works.
- Exact `COPY INTO` option syntax beyond the examples is upstream-defined; do not
  invent options.

## Caveats

- `csv.trim_space` is not in the official `FILES()` parameter list — mention it
  only as a caveat if accuracy matters.
