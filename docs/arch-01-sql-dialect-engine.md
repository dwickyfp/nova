# Architecture 01: SQL Dialect Engine

> Custom SQL preprocessing layer: parses, translates, and rewrites custom syntax before sending to StarRocks.

---

## Overview

Nova extends StarRocks SQL with custom commands that don't exist in StarRocks natively. The SQL Dialect Engine is a **preprocessing pipeline** that sits between the user and StarRocks:

```
User SQL → [Parser] → [Translator] → [Credential Injector] → StarRocks
```

**Key principle:** StarRocks never sees custom syntax. It only receives valid SQL.

---

## Pipeline Stages

### Stage 1: Parser

Detects whether SQL contains custom Nova commands.

```python
class CommandType(Enum):
    # Custom @stage commands
    STAGE_QUERY       = "stage_query"        # SELECT * FROM @stage1.file.csv
    STAGE_DESCRIBE    = "stage_describe"     # DESCRIBE FILE 's3://...'
    STAGE_BROWSE      = "stage_browse"       # BROWSE STAGE 'name'
    STAGE_LOAD        = "stage_load"         # LOAD table FROM STAGE(...)
    STAGE_EXPORT      = "stage_export"       # EXPORT table TO STAGE(...)
    
    # Standard StarRocks (passthrough)
    PASSTHROUGH       = "passthrough"
```

**Detection rules:**

| Pattern | CommandType |
|---------|------------|
| `@name.path.ext` anywhere in SQL | `STAGE_QUERY` |
| `BROWSE STAGE 'name'` | `STAGE_BROWSE` |
| `DESCRIBE FILE 'path'` | `STAGE_DESCRIBE` |
| `LOAD table FROM STAGE(...)` | `STAGE_LOAD` |
| `EXPORT table TO STAGE(...)` | `STAGE_EXPORT` |
| Everything else | `PASSTHROUGH` |

### Stage 2: Translator

Converts custom syntax to valid StarRocks SQL.

**@stage reference translation:**

```
@stage1.data_pembayaran.csv
     │         │         │
     │         │         └── filename + format detection (.csv → format='csv')
     │         └── sub_path (optional, dots become slashes)
     └── stage_name (bound to current database.schema)

Full path = s3://<bucket>/<database>/<schema>/<stage_name>/<sub_path>/<filename>
```

**Reference resolution rules:**

| User writes | Context | Resolved to |
|-------------|---------|-------------|
| `@stage1.file.csv` | `DATALAKE.bronze` | `FILES(... path=.../datalake/bronze/stage1/file.csv ...)` |
| `@stage1.data.file.csv` | `DATALAKE.bronze` | `FILES(... path=.../datalake/bronze/stage1/data/file.csv ...)` |
| `@silver.stage1.file.csv` | `DATALAKE.*` | `FILES(... path=.../datalake/silver/stage1/file.csv ...)` |
| `@DATALAKE.bronze.stage1.file.csv` | any | `FILES(... path=.../datalake/bronze/stage1/file.csv ...)` |

**Parsing algorithm:**

```python
def parse_ref(segments: list, context: SQLContext) -> tuple:
    """
    @a                 → (ctx.db, ctx.schema, a, [])
    @a.b               → if is_schema(a): (ctx.db, a, b, [])
                         else: (ctx.db, ctx.schema, a, [b])
    @a.b.c             → if is_database(a): (a, b, c, [])
                         else: (ctx.db, a, b, [c])
    @a.b.c.d...        → (a, b, c, [d, ...])
    """
```

### Stage 3: Credential Injector

For any `FILES()` call without credentials, injects credentials from the matching Storage Connection.

```python
def inject_creds(sql: str) -> str:
    """
    FILES(
        'path' = 's3://bucket/file.csv',
        'format' = 'csv'
    )
    →
    FILES(
        'path' = 's3://bucket/file.csv',
        'format' = 'csv',
        'aws.s3.endpoint' = 'http://minio:9000',
        'aws.s3.access_key' = '...',
        'aws.s3.secret_key' = '...',
        'aws.s3.enable_path_style_access' = 'true',
        'aws.s3.enable_ssl' = 'false'
    )
    """
```

**Credential matching:** Extract `path` from `FILES()`, find matching Storage Connection by path prefix.

---

## Complete Rewrite Examples

### Example 1: Simple stage query

```sql
-- User writes:
SELECT * FROM @stage1.data_pembayaran.csv LIMIT 10;

-- After Parser: STAGE_QUERY
-- After Translator:
SELECT * FROM FILES(
    'path' = 's3://nova-stages/datalake/bronze/stage1/data_pembayaran.csv',
    'format' = 'csv'
) LIMIT 10;

-- After Credential Injector:
SELECT * FROM FILES(
    'path' = 's3://nova-stages/datalake/bronze/stage1/data_pembayaran.csv',
    'format' = 'csv',
    'csv.column_separator' = ',',
    'csv.row_delimiter' = '\n',
    'aws.s3.endpoint' = 'http://minio:9000',
    'aws.s3.access_key' = 'AKIAIO...MPLE',
    'aws.s3.secret_key' = 'wJalrX...EKEY',
    'aws.s3.enable_path_style_access' = 'true',
    'aws.s3.enable_ssl' = 'false'
) LIMIT 10;

-- StarRocks receives this ↑ (valid SQL, no custom syntax)
```

### Example 2: CTAS from stage

```sql
-- User writes:
CREATE TABLE payments AS
SELECT * FROM @stage1.data_pembayaran.csv;

-- Rewritten:
CREATE TABLE payments AS
SELECT * FROM FILES(
    'path' = 's3://nova-stages/datalake/bronze/stage1/data_pembayaran.csv',
    'format' = 'csv',
    <credentials>
);
```

### Example 3: Join stage + table

```sql
-- User writes:
SELECT a.*, b.name
FROM @stage1.transactions.csv a
JOIN dim_customers b ON a.cust_id = b.id;

-- Rewritten:
SELECT a.*, b.name
FROM FILES(
    'path' = 's3://nova-stages/datalake/bronze/stage1/transactions.csv',
    'format' = 'csv',
    <credentials>
) a
JOIN dim_customers b ON a.cust_id = b.id;
```

### Example 4: Export to stage

```sql
-- User writes:
INSERT INTO @stage1.exports.backup.parquet
SELECT * FROM orders WHERE dt >= '2026-01-01';

-- Rewritten:
INSERT INTO FILES(
    'path' = 's3://nova-stages/datalake/bronze/stage1/exports/backup.parquet',
    'format' = 'parquet',
    'compression' = 'zstd',
    <credentials>
)
SELECT * FROM orders WHERE dt >= '2026-01-01';
```

### Example 5: Standard SQL (passthrough)

```sql
-- User writes:
SELECT * FROM orders WHERE amount > 100000;

-- No @stage, no FILES() → PASSTHROUGH
-- StarRocks receives: SELECT * FROM orders WHERE amount > 100000;
```

---

## Format Auto-Detection

| Extension | Format | Extra Params |
|-----------|--------|-------------|
| `.csv` | csv | `csv.column_separator=\',\'`, `csv.row_delimiter=\'\n\'` |
| `.tsv` | csv | `csv.column_separator=\'\t\'` |
| `.json` | json | — |
| `.jsonl` | json | — |
| `.parquet` / `.pq` | parquet | — |
| `.orc` | orc | — |
| `.avro` | avro | — |
| unknown | parquet | fallback |

---

## Error Handling

| Scenario | Error |
|----------|-------|
| Stage not found | `Stage 'stage1' not found in DATALAKE.bronze. Create it first.` |
| No storage connection for path | `No storage connection matches s3://bucket/path/. Configure in Admin > Storage.` |
| Ambiguous reference | `Ambiguous @stage reference 'stage1'. Use @schema.stage1 or @db.schema.stage1 to disambiguate.` |

---

## Implementation Classes

```
sql_dialect/
├── __init__.py
├── parser.py          # DialectParser — detect custom commands
├── translator.py      # DialectTranslator — @stage → FILES()
├── credential_injector.py  # SQLCredentialInjector — inject creds into FILES()
├── pipeline.py        # SQLPipeline — orchestrates parser → translator → injector
├── format_detector.py # auto-detect format from file extension
└── commands/
    ├── __init__.py
    ├── stage_query.py
    ├── stage_browse.py
    ├── stage_load.py
    └── stage_export.py
```

---

## Integration with FastAPI

```python
# api/v1/endpoints/sql.py

pipeline = SQLPipeline()

@router.post("/execute")
async def execute_sql(req: SQLRequest) -> SQLResponse:
    # 1. Build context from request (database, schema)
    context = SQLContext(database=req.database, schema=req.schema)
    
    # 2. Run through dialect pipeline
    result = await pipeline.execute(req.sql, context)
    
    return SQLResponse(
        success=True,
        columns=result.columns,
        rows=result.rows,
        executed_sql=result.executed_sql,  # rewritten SQL (for transparency)
        original_sql=req.sql,               # what user wrote
        warnings=result.warnings            # ["✅ @stage1.data_pembayaran.csv"]
    )
```
