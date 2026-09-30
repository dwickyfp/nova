# Architecture 01: SQL Dialect Engine

> Stage lowering and secret injection within Nova's SQL frontend.

## Architecture

The production compiler is described in [Architecture 13: SQL Frontend and
Planner](arch-13-sql-frontend.md). It parses every statement through the central
ANTLR parser, builds typed nodes, analyzes effects, selects a registered planner
and applies ordered rules before execution.

```text
QueryService / MySQL proxy
  → hard guard → central parser → typed AST → semantic analysis
  → planner registry + capabilities → ordered rules → execution plan
  → executor → stage resolution + authorization → secret resolution
  → existing stage translator / injector → StarRocks
  → redacted QueryResult + audit
```

Stage references come from the central tree. `@stage` is a relation or command
reference by grammar position; `@x` in an expression remains a user variable.
Strings, comments, aliases, numeric paths and glob segments keep their source
spans. Original and normalized SQL are separate; executable fragments are sliced
from normalized source rather than assembled from tree text.

## Implementation

`app/sql_frontend/stages.py` creates the existing `ParsedSQL` translator view
without reparsing. `StageReferenceRule` attaches the stage-lowering requirement to
an engine plan. `app/sql_frontend/execution/stages.py` binds scoped stage names,
checks access and resolves storage credentials. It also owns CSV header and
parameter detection. The translator and injector remain in
`app/modules/query/dialect/`.

Reference offsets identify individual configurations and CSV parameters. Two
references to the same stage name can therefore resolve independently across
scopes or connections. Authorization runs for all selected stages before any
storage secret is read. The late execution step produces the credential-bearing
SQL sent to StarRocks; plans, responses and audit rows remain credential-free.

Supported stage forms include:

```sql
SELECT * FROM @stage1.folder.data.csv;
SELECT * FROM @silver.stage1.data.parquet;
LIST FILES @stage1/;
COPY INTO target_table FROM @stage1.data.csv;
COPY INTO @stage1.output.parquet FROM source_table;
INSERT INTO @stage1.output.parquet SELECT * FROM source_table;
```

Export operations retain the existing export guard. Unsupported COPY options are
rejected. Native FILES() statements retain their existing behavior. Task bodies
cannot persist stage references or populated credentials.

## Integration Points

QueryService and its EXPLAIN path use the central executor. ML training and
streaming SQL use `sql_frontend/preparation.py` and the same stage runtime.
The proxy uses central stage spans during variable substitution and reuses an
unchanged tree within its request. Assistant syntax validation has no catalog
or secret access.

The legacy `parse_sql`, `stage_reference_at` and compact feature parser APIs stay
available for existing callers. `_parse_tree` and `_lex` delegate construction to
the central parser/tokenizer. Their compatibility shortcuts and fallback scans
are not used for production SQL classification.

## Configuration

Stages remain schema-bound metadata in NOVA_SYSTEM; storage connections and
credentials remain in nova.yaml and environment/secret providers. The frontend
requires no new persistent schema. Parser regeneration uses the existing pinned
ANTLR 4.13.2 generator at build time, with committed Python artifacts.

For extension registration, effects, binding, capability profiles, composite
semantics and validation results, see [the frontend architecture](arch-13-sql-frontend.md)
and [migration report](benchmarks/sql-frontend-migration.md).
