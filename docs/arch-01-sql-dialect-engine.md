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

**Since 109-B (NOVA-126) the parser is ANTLR4, not regex-on-text.**
`backend/app/modules/query/dialect/parser.py` lexes and parses the statement with
the vendored grammar (109-A / NOVA-125 added the `@stage` rule) and builds the
stage registry from the parse tree:

* every `#stageAtom` node (`StarRocks.g4`, NOVA-BEGIN block) becomes one
  `StageReference`; the first `stageSegment` is the stage name and the rest are
  the path segments, in the order written, whether joined by `.` or `/`;
* a numeric path segment whose leading separator the lexer fused into one token
  (`.2024` → `DECIMAL_VALUE`, `.2024_01` → `DOT_IDENTIFIER`, `.2e3` →
  `DOUBLE_VALUE`) is accepted by the Nova `fusedDecimal` rule in the separator
  position and expanded back to its segment in `parser.py` (NOVA-132). Without
  it `@stage1.2024.csv` saw a bare number where a separator was expected, dropped
  the reference, and forwarded the statement untranslated. `fusedDecimal`
  consumes the fused token only — never a following atom — so a table alias
  after a trailing numeric segment (`FROM @stage1.2024 t`) is not swallowed;
  `decimalAtom` keeps its optional absorption only inside `stageSegment`, for the
  hyphen trailing-dot form (`@stage-2.` followed by `data`). The Nova surfaces
  (`LIST` / `COPY INTO`) expand the same fused tokens in their token scan;
* a `@stage` inside a string literal or a comment is a single token to the
  lexer, so it can never become a false positive;
* `@@version` is the engine's `systemVariable` (two `AT` tokens), never a stage;
* a syntax error carries its exact `line:col` in `ParsedSQL.errors` and the
  registry stays empty, so a malformed statement is never partially rewritten.

The lexer input is wrapped in `CaseInsensitiveInputStream`: the generated Python
lexer is case-sensitive while StarRocks keywords are not, so without it every
lowercase keyword would fail to parse. Only `LA()` is folded; `getText()` keeps
the user's original casing.

**`@`-free short-circuit (AC-5 mitigation).** Building the ANTLR4 tree costs
~160× the regex it replaced, and the regression lands on the ordinary query
request path. `parse_sql` therefore returns `REGULAR` / `stage_refs=[]`
immediately when the statement contains no literal `@`, before `_parse_tree`
runs. The guard is sound, not a heuristic: every stage form the dialect acts on
carries a literal `@` (`@stage1`, `@stage1/`, `@stage1.data.csv`,
`@stage1.data/*.csv`, and the `LIST`/`COPY INTO` Nova surfaces), so a statement
with no `@` cannot contain a stage and the grammar's answer is already known.
The test is the literal character, never a regex — a pattern would reintroduce
the text-scanning source of truth 109-B removes. Statements that do carry an
`@` (including `@@version` and `@` in a literal, which are *not* stages) still
go through the grammar, so the tree remains the single source of truth for
every statement that can carry a stage.

```python
class CommandType(Enum):
    # Custom @stage commands
    STAGE_QUERY       = "stage_query"        # SELECT * FROM @stage1.file.csv
    STAGE_BROWSE      = "stage_browse"       # LIST FILES @stage
    STAGE_LOAD        = "stage_load"         # COPY INTO table FROM @stage
    STAGE_EXPORT      = "stage_export"       # COPY INTO @stage FROM table

    # Standard StarRocks (passthrough)
    REGULAR           = "regular"
```

**Detection rules:**

| Shape | CommandType |
|-------|------------|
| `SELECT`/`WITH`/`EXPLAIN`/`DESC`/`SHOW` with a `@stage` in table position | `STAGE_QUERY` |
| `LIST @stage` / `LIST FILES @stage` | `STAGE_BROWSE` |
| `COPY INTO table FROM @stage` | `STAGE_LOAD` |
| `COPY INTO @stage FROM table` | `STAGE_EXPORT` |
| Everything else | `REGULAR` |

`LIST` and `COPY INTO` are Nova surfaces the StarRocks 4.1 grammar does not
model (`LIST` does not exist; `COPY INTO` is 4.2+). They are recognised from the
statement's leading tokens and their references are scanned from the **token
stream** (`_nova_surface_stage_refs`), so the reference grammar is still the
single source of truth.

**Latency (NOVA-126 acceptance criterion 5 — the slice's D1 precondition):** the
ANTLR4 parse costs ~160× the regex it replaces, so the parser short-circuits
`@`-free SQL before building the tree (see above). Measured with
`time.perf_counter_ns`, 2000 warm-up + 20000 sampled runs per statement:

| Path | regex (before) p50 / p95 | post-swap p50 / p95 |
|------|--------------------------|---------------------|
| ordinary SQL (no `@`) | 3.1 µs / 4.3 µs | **0.3 µs / 0.4 µs** |
| stage SQL (`@` present) | 4.5 µs / 5.5 µs | 324 µs / 584 µs |

The ordinary path — the majority of traffic and the path AC-5 guards — is back
at the pre-swap order of magnitude (in fact below it: a single membership test
replaces the regex scan). Unmitigated, the same ordinary corpus is
418 µs / 2342 µs; the guard removes that cost entirely. Stage statements pay the
ANTLR4 cost, which is the price of the grammar being the single source of truth
for the one statement class the dialect rewrites. The gate **passes**.

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
| Syntax error | `StageParseError` — `"<line>:<col> <message>"`, e.g. `1:7 mismatched input 'FROM'` |
| Ambiguous reference | `Ambiguous @stage reference 'stage1'. Use @schema.stage1 or @db.schema.stage1 to disambiguate.` |

---

## Implementation Classes

```
backend/app/modules/query/
├── sql_pipeline.py            # guard → translate → inject → redact
└── dialect/
    ├── parser.py              # ANTLR4 stage registry (109-B) + CommandType
    ├── translator.py          # @stage → FILES() (Nova-owned)
    ├── injector.py            # credential injection (Nova-owned)
    ├── detector.py            # format auto-detection
    └── ml_model.py            # CREATE ML_MODEL surface

backend/app/sql_dialect/grammar/
├── StarRocks.g4               # vendored grammar + Nova @stage rule (109-A)
├── StarRocksLex.g4            # vendored lexer
└── StarRocks{Lexer,Parser,Visitor}.py  # generated Python3 target
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
