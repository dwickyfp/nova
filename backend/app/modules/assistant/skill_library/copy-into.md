---
name: copy-into
title: Load data from a stage
summary: Load, list and export stage files using the exact Nova lowering forms and current export guard.
triggers: copy into, load, ingest, export, unload, import data, muat data, ekspor
source: docs/sql_docs/08-native-starrocks-sql.md, docs/sql_docs/02-stage-queries.md
---

# Skill: copy-into

Nova replaces `@stage` with a credential-injected FILES source. Its translator
lowers LIST to a file listing and COPY INTO to INSERT SELECT. These are bounded
Nova forms, not the entire Snowflake COPY grammar.

## Templates

Load a file into a table:

```sql
COPY INTO NOVA_DEMO.orders FROM @stage1.orders.csv;
-- Equivalent load, also allowing explicit column mapping:
INSERT INTO NOVA_DEMO.orders SELECT * FROM @stage1.orders.csv;
```

Inspect source and target columns before using SELECT * for loading.

Load a specific column set:

```sql
INSERT INTO analytics.events (event_id, event_time, payload)
SELECT event_id, event_time, payload FROM @stage1.events.csv;
```

## Notes

- `LIST @stage1/;` or `LIST FILES @stage1/;` lowers to FILES with listing options.
  Use query_execute for an approved listing; no file contents are required.
- COPY INTO supports a table destination and one stage source, without Snowflake
  FILE_FORMAT/ON_ERROR options. Use INSERT SELECT for projections and filtering.
- For explicitly requested loads use query_mutate with approval. Drafts do not execute.
- Success comes from the result's explicit error status, not its shape.
- Always name the destination table with its database; the source stage uses the
  `@stage` form (never a storage path).

## Caveats

- The translator also lowers `COPY INTO @stage1.output.parquet FROM db.table`
  and `INSERT INTO @stage1.output.parquet SELECT ...`. The ordinary SQL endpoint
  and assistant tools block export by default; a dedicated authorized service
  must explicitly enable it. Distinguish syntactic support from execution access.
  Never substitute raw storage paths or credentials.
- CREATE STAGE is not implemented as Nova SQL. Stage creation uses the stage service.
- For a read-only inspection before loading, use a `SELECT * FROM @stage… LIMIT n`
  via the read-only tool.
