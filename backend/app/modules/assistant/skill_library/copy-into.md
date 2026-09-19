---
name: copy-into
title: Load and export with COPY INTO
summary: Move data between a stage and a table with COPY INTO, with @stage rewritten both directions.
triggers: copy into, load, ingest, export, unload, import data, muat data, ekspor
source: docs/sql_docs/08-native-starrocks-sql.md, docs/sql_docs/02-stage-queries.md
---

# Skill: copy-into

`COPY INTO` moves data between a stage and a table. Nova rewrites `@stage` in
**both** directions and passes the rest through as StarRocks SQL.

## Templates

Load a file into a table:

```sql
COPY INTO NOVA_DEMO.orders FROM @stage1.orders.csv
```

Export a table to a stage:

```sql
COPY INTO @stage1.exports FROM NOVA_DEMO.orders
```

Load a specific column set:

```sql
COPY INTO analytics.events (event_id, event_time, payload)
FROM @stage1.events.csv
```

## Notes

- Command type is classified as `STAGE_LOAD` / `STAGE_EXPORT`; the translation
  step rewrites the stage reference (format detection + credential injection) as
  for a `SELECT … FROM @stage`.
- `COPY INTO` returns no result columns, so success is derived from the absence
  of an error marker, not from result shape.
- Always name the destination table with its database; the source stage uses the
  `@stage` form (never a storage path).

## Caveats

- Exact `COPY INTO` option syntax (properties, column mapping, `ENCLOSE`, etc.)
  is upstream-defined and **unverified** in Nova's docs. Offer only the forms
  shown here unless the user supplies the option they want.
- For a read-only inspection before loading, use a `SELECT * FROM @stage… LIMIT n`
  via the read-only tool.
