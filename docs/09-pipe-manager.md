# Module 09: Pipe Manager

Nova administers StarRocks file-ingestion pipes through the shared SQL pipeline.
This describes the API and engine behavior; the archived pipe UI is not an
active Console route.

## Authorization and availability

Every API operation executes as the authenticated user, preserving the active
role, session ID, and security-context version. The engine enforces native pipe
privileges. Pipe administration does not open a root or system connection.
Source statements and errors pass through the shared credential redactor before
they appear in responses or audit records.

Nova rejects `CREATE PIPE` and execution-enabling
`ALTER PIPE` operations (`RESUME`, `RETRY`, and `SET`) during SQL planning. The
StarRocks 4.1.4 background pipe task builder does not carry the initiating
principal and active role required by Nova's Ranger contract. This gate applies
to the API, SQL endpoint, proxy, and assistant through their shared frontend.
Inspection, `SUSPEND`, and `DROP` remain available subject to authorization.
Use caller-scoped `COPY` or scheduled Nova tasks for governed ingestion until
background pipe execution preserves the security context end to end.

Before upgrading a deployment that already has pipes, suspend them. The planning
gate does not stop tasks already running in the engine.

## SQL operations

Configure storage through Nova connections and stages. User SQL refers to stage
names, rather than entering storage credentials into statements.

```sql
CREATE PIPE my_database.orders_pipe
PROPERTIES (
    "AUTO_INGEST" = "TRUE",
    "POLL_INTERVAL" = "300",
    "BATCH_SIZE" = "1GB",
    "BATCH_FILES" = "256"
)
AS INSERT INTO my_database.orders
SELECT * FROM @incoming.orders/*.parquet;

SHOW PIPES FROM my_database;
SHOW PIPES FROM my_database WHERE NAME = 'orders_pipe';
DESCRIBE PIPE my_database.orders_pipe;
ALTER PIPE my_database.orders_pipe SUSPEND;
ALTER PIPE my_database.orders_pipe RESUME;
DROP PIPE my_database.orders_pipe;
```

The shared stage binder authorizes and resolves `@stage` access. Continuous
loading, polling, batching, and file bookkeeping are engine responsibilities.
File identity tracking is not a promise of end-to-end exactly-once delivery
across arbitrary rewrites, failures, or external producers.

## REST API

All routes use `/api/v1/pipes` and require an authenticated session.

| Method and suffix | Behavior |
| --- | --- |
| `GET /?database=db` | List visible pipes |
| `GET /{name}?database=db` | Read metadata and redacted source SQL; 404 when absent or invisible |
| `POST /` | Create a pipe from validated properties and source SQL |
| `PATCH /{name}/suspend?database=db` | Suspend a pipe |
| `PATCH /{name}/resume?database=db` | Resume a pipe when execution is available |
| `DELETE /{name}?database=db` | Explicitly drop a pipe |
| `GET /{name}/files?database=db` | Read file status after checking pipe visibility |

Create fields are `name`, `database`, `sql`, `auto_ingest`, `poll_interval`,
`batch_size`, and `batch_files`. API names use simple identifiers. The API
validates property values before constructing DDL; source SQL still passes
through Nova parsing, semantic analysis, policy, and execution routing.

| Property | API default | API validation |
| --- | --- | --- |
| `auto_ingest` | `true` | Boolean |
| `poll_interval` | `300` seconds | Integer from 1 to 3600, matching the pinned engine |
| `batch_size` | `1GB` | Positive integer with optional `B`, `KB`, `MB`, `GB`, `TB`, or `PB` suffix |
| `batch_files` | `256` | Positive integer |

## Engine metadata mapping

`SHOW PIPES` supplies `DATABASE_NAME`, `PIPE_NAME`, `STATE`, and `CREATED_TIME`.
`DESCRIBE PIPE` supplies the source statement in `INSERT_SQL` and properties.
The API maps these to its stable response fields.

File status uses `information_schema.pipe_files`: `FILE_NAME`, `LOAD_STATE`,
`FILE_SIZE`, `ERROR_MSG`, and `FINISH_LOAD_TIME`, ordered by `STAGED_TIME`.
`loaded_at` means the engine's finish-load timestamp, not source-file
modification time. Results are scoped to the pipe's database; an inaccessible
pipe does not trigger a file-metadata query.

## Validation

Unit coverage: [test_pipes.py](../backend/tests/unit/test_pipes.py).
Seeded real-engine API coverage:
[test_pipes_live.py](../backend/tests/integration/test_pipes_live.py).
The live lifecycle test covers administration, metadata, credential redaction,
and refusal of an unprivileged caller. It does not assert ingestion throughput
or long-running background delivery guarantees.

Engine references:
[SHOW PIPES](https://docs.starrocks.io/docs/sql-reference/sql-statements/loading_unloading/pipe/SHOW_PIPES/),
[pipe_files](https://docs.starrocks.io/docs/sql-reference/information_schema/pipe_files/),
and the pinned
[4.1.4 pipe task builder](https://github.com/StarRocks/starrocks/blob/4.1.4/fe/fe-core/src/main/java/com/starrocks/scheduler/TaskBuilder.java)
and [pipe property limits](https://github.com/StarRocks/starrocks/blob/4.1.4/fe/fe-core/src/main/java/com/starrocks/load/pipe/Pipe.java).
