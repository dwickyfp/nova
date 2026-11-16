# StarRocks feature compatibility in Nova

Goal (user request, 2026-11-16): go through the StarRocks documentation page by
page, test every SQL feature in a Nova workspace, support it in Nova directly
or through an adaptation where Nova owns the concern (stages, identity,
background work), and document each feature in Nova's `/docs`, in more detail
than the StarRocks documentation.

This file is the plan and the record of what is done. Evidence lives in
`docs/reports/starrocks-compat/`.

## Method

- **Source:** the English docs of the pinned engine (StarRocks tag `4.1.4`,
  `docs/en`, 1,172 files), fetched by sparse checkout. The newest upstream docs
  describe features the pinned engine may not have.
- **Corpus:** `backend/scripts/starrocks_compat/extract.py` takes every SQL
  block (3,259) with its page, section and line.
- **Probe:** `backend/scripts/starrocks_compat/probe.py` splits blocks into
  statements, drops printed results and prompts, and runs each one through
  Nova's SQL API as `nova_admin` in a scratch database `compat_<page hash>`.
  When Nova fails, it runs the same statement directly on the engine as the
  same user, so Ranger applies the same way and only Nova's layer differs.
- **Safety on the shared development stack:**
  - Cluster-wide and account statements are not run: `ALTER SYSTEM`, `ADMIN`,
    `SET GLOBAL`, users, roles, grants, resource groups and storage volumes.
  - A write that names a database that existed before the run is not run.
  - Only databases the run created are dropped afterwards.
- **Outcomes:** `ok`, `nova_gap` (Nova fails and the engine runs it), `both_fail`
  (fixture data or an external system is missing, or the docs are wrong),
  `template` (placeholders), `signature` (a function signature, not a
  statement), `skipped`.

## Probe 1 results (2026-11-16)

4,569 statements; per page in `probe-1-pages.csv`.

| Outcome | Count |
| --- | --- |
| Ran in Nova | 1,803 |
| Real Nova gap, re-checked one by one | 39 |
| Engine error passed through, engine ran it on retry (not yet re-checked; mostly order and timing effects) | 196 |
| Both fail | 1,356 |
| Templates and signatures | 888 |
| Not run on the shared stack | 287 |

Function families run almost entirely: math, string, date-time, JSON, bitmap,
array, crypto, bit and window functions have no confirmed gap.

### Confirmed Nova gaps

| Gap | Statements | Cause | Plan |
| --- | --- | --- | --- |
| `SET <variable>`, `SET @var` in the worksheet | 34 | Each worksheet statement runs on its own pooled connection, so Nova refuses session settings rather than report a false success | Worksheet session: settings and user variables kept per worksheet and re-applied to the connection that runs each statement |
| Trailing `\G` | 2 | MySQL client terminator for vertical output | Accept `\G` at the end of a worksheet statement and show the result vertically |
| `SHOW [ALL] AUTHENTICATION [FOR user]` | 3 | No AST builder for the grammar extension that matches it | Register a builder; keep account rules |

### Deliberate refusals that need an adaptation

These run in the background without the caller's sign-in and active role, or
write to storage Nova reaches only through stages. They stay refused in native
form. Nova provides an equivalent that runs as the user and reads or writes
stages:

| StarRocks feature | Nova adaptation |
| --- | --- |
| `CREATE PIPE` over S3/HDFS/FILES | `CREATE PIPE ... AS INSERT INTO t SELECT ... FROM @stage/...`: a Nova pipe that runs as its owner role, lists new stage files, loads each file once with a label, and keeps a file ledger in PostgreSQL. `SHOW PIPES`, `DESC PIPE`, `ALTER PIPE SUSPEND/RESUME/SET`, `RETRY FILE`, `DROP PIPE`. |
| `INSERT INTO FILES(...)`, `SELECT ... INTO OUTFILE`, `EXPORT` | Unload to `@stage` paths with the same format options |
| Broker Load (`LOAD LABEL ... WITH BROKER`) | Load from `@stage` paths; credentials stay in the stage |
| `SUBMIT TASK` | Point to `CREATE TASK`, which runs as the user, with the same body |
| Routine Load (Kafka, Pulsar) | Needs a stream connection type; decide after pipes |
| `CREATE EXTERNAL CATALOG` (Hive, Iceberg, Hudi, Delta, JDBC) | Nova connections and catalogs; supported types today: StarRocks, PostgreSQL, Snowflake |
| Backup and restore repositories | Repository on a stage location; administrator only |

Security boundary for the pipe: today a task body may not contain `@stage`,
because a stage secret must not be resolved in the background. A pipe needs
this. The design: at each run, the worker checks the owner role's access to
the stage as the owner and resolves the secret only for that run, after the
check. It is never stored with the pipe. This changes `docs/arch-13` and the
task contract, so it is decided and tested before it ships.

## Phases

1. **Gaps:** the worksheet session, `\G` and `SHOW AUTHENTICATION`. Tests, and
   the probe re-run on the affected pages.
2. **Fixtures:** for the 774 "missing fixture" statements, per page,
   create the tables and rows the example needs, then re-run them. Re-check
   the 196 unconfirmed gaps the same way.
3. **Adaptations:** in this order: unload to stage, pipe over stage, broker
   load from stage, `SUBMIT TASK` guidance, backup/restore, catalogs, routine load.
4. **Docs:** one Nova page per feature area, every example run in Nova, with
   what Nova adds or changes, limits, errors and permissions:
   - functions by family;
   - query syntax (joins, subqueries, CTEs, window functions, set operations);
   - table design;
   - loading and unloading;
   - pipes;
   - materialized views;
   - transactions;
   - system variables;
   - administration a Nova administrator can do.

## Implementation plan (2026-11-16)

Each workstream lands with unit and real-engine tests, a `/docs` page whose
examples are run in Nova, and the probe re-run on the affected pages.
Workstreams that touch the same files run one after another.

| # | Workstream | Owner area | Done when |
| --- | --- | --- | --- |
| W1 | Worksheet session: `SET <var>`, `SET @var`, `SET time_zone`, `SET sql_mode` and `BEGIN/COMMIT/ROLLBACK` in one run on one held connection with the caller's role and security context. Settings kept per worksheet (Redis) and re-applied at the next run. `RESET`/`SET ... DEFAULT` clear them. A trailing `\G` shows one record per block. `SHOW [ALL] AUTHENTICATION [FOR user]` runs. | `modules/query`, `sql_frontend`, worksheet UI | All 39 confirmed gaps run; settings never leak to another worksheet, user or role; a role switch clears them |
| W2 | Unload to a stage: `INSERT INTO FILES("path" = "@stage/...")`, `SELECT ... INTO OUTFILE "@stage/..."`, `COPY INTO @stage FROM (query)` and `EXPORT TABLE ... TO "@stage/..."`. Allowed when the caller's role may write to that stage scope; confirmation required; credentials injected at execution only. | `common/sql_guard`, stage translator/injector, `modules/stages` | Files appear in the stage in every documented format and option; refused without stage write access; no secret in results or audit |
| W3 | Pipes over stages: `CREATE [OR REPLACE] PIPE ... AS INSERT INTO t SELECT ... FROM FILES(@stage/...)` (or `FROM @stage/...`), `ALTER PIPE SUSPEND/RESUME/SET`, `DROP PIPE`, `SHOW PIPES`, `DESC PIPE`, `RETRY FILE`, pipe file history. PostgreSQL definition and file ledger; runs as the owner role through the task runtime; label per batch for exactly-once loads; stage secret resolved per run after the owner's access check. | new `modules/pipes`, worker, metadata migration | New files load once; failed files are retried or skipped as documented; suspend, resume and replace work; access revoked from the owner stops loads |
| W4 | Broker Load from a stage: `LOAD LABEL ... (DATA INFILE("@stage/...") INTO TABLE ...)` with all documented options; `SHOW LOAD`, `CANCEL LOAD`. `SUBMIT TASK AS <sql>` runs once as a Nova task as the user. | stage translator, `modules/tasks` | Documented broker load examples load from a stage |
| W5 | Backup and restore with a repository on a stage location, for administrators. | stage translator, admin | Back up and restore a database through a stage |
| W6 | Fixtures for the 774 statements that need tables, and a re-check of the 196 unconfirmed gaps. | probe tools | Every remaining failure has a recorded reason |
| W7 | `/docs`: function families, query syntax, table design, loading and unloading, pipes, materialized views, transactions, system variables. | docs | Every example run in Nova |

Decision (user, 2026-11-16): add Kafka and an Iceberg REST catalog (data in the
existing MinIO) as new services in the existing `docker` Compose project,
leaving existing data untouched. This adds:

| # | Workstream | Done when |
| --- | --- | --- |
| W8 | Routine Load from Kafka, adapted: a Kafka connection kept as a Nova connection (secret encrypted); `CREATE ROUTINE LOAD` runs only while its owner's role still has access, which Nova checks and pauses on revocation; `SHOW/PAUSE/RESUME/STOP ROUTINE LOAD`. | Documented CSV and JSON routine loads consume a Kafka topic into a table |
| W9 | Iceberg catalog through Nova connections: create an Iceberg REST connection, query, insert, create table, time travel and snapshots as documented. | Documented Iceberg examples run against the REST catalog |

## Status

- [x] Corpus and probe tools; probe 1 run and recorded.
- [ ] Phase 1
- [ ] Phase 2
- [ ] Phase 3
- [ ] Phase 4
