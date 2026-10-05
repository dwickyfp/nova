# SQL compatibility audit: Nova and StarRocks 4.1.4 through MySQL clients

Date: 2026-10-05. Engine: StarRocks `4.1.4` (`4a9848edf03f5c936dac664b2d52527f48e72eb0`),
stock FE/BE images from `backend/docker-compose.test.yml`, Ranger disabled. Clients:
MySQL 8.0.46 CLI (`mysql:8.0`), PyMySQL, mysql-connector-python, Go
`go-sql-driver/mysql` and MySQL Connector/J against the standalone proxy
(`python -m app.proxy`, port 4406).

The audit ran in two passes. The first found and fixed the defects in the first
table below and left a list of refusals and limitations. The second pass removed
most of that list: transactions, prepared statements, streaming, script context
changes, partial-segment globs and physical-path hiding.

This is a point-in-time measurement. Governed behavior on the patched FE was
checked with the Ranger acceptance script only (see Reproduction), not with the
corpus or the differential run.

## Method

1. **Grammar provenance.** The vendored `upstream/StarRocks*.g4` files were compared
   byte for byte with the files at the pinned commit on GitHub, and the `4.1.4`
   tag was confirmed to peel to that commit. `scripts/check_grammar_drift.py`
   confirmed that Nova edits sit only inside `NOVA-BEGIN`/`NOVA-END` regions, and
   every new Nova keyword is listed in `nonReserved`.
2. **Upstream corpus, offline.** Statements and expected outcomes were extracted from the
   StarRocks SQL-tester result files at the pinned commit (`test/sql/*/R/*`,
   1,178 files, 38,859 statements). Tester directives (`shell:`, `function:`) were
   dropped and `${var}` placeholders replaced. Each of the remaining 38,561
   statements went through the proxy's statement classification, the default
   schema normalizer, the guard, the central parser, the AST builder and
   preflight validation, without an engine.

   Of the 38,434 statements StarRocks itself parses, no StarRocks-dialect
   statement was rejected by the Nova parser. The 119 parse failures were 37
   Trino-dialect statements (`SET sql_dialect='trino'` tests), 3 statements the
   upstream results also mark as syntax errors (in the `[REGEX]` form the
   extractor did not read), and 79 test-runner artifacts (`var=SELECT ...`
   assignments, comments wrapping directives, substituted placeholders).
   Planner preflight raised no errors and no statement crashed or timed out.
   Median parse time was 1.6 ms (p99 79 ms); the slowest, a 204 KB predicate
   list, took 8.2 s.
3. **Differential execution.** Self-contained SQL-tester sections (no `shell:`/`function:`
   directives, external storage, catalogs, users or cluster-wide settings) were
   sampled and run whole, in a fresh database, once directly on the engine and
   once through the proxy, comparing each statement's success, error and sorted
   rows.

   The first run (before the fixes, 331 sections, 5,000 statements) exposed the
   destructive-SQL refusal, the long-statement audit failure and the typed user
   variable defect. The final run on the fixed code, with the same 331 sections
   and 5,000 statements, produced 40 differences, all explained:

   | Count | Cause |
   | ---: | --- |
   | 13 | `ARRAY_AGG(DISTINCT ... ORDER BY other_column)` is nondeterministic in StarRocks itself |
   | 12 | `BEGIN`/`COMMIT`/`ROLLBACK` refused (then intended) |
   | 4 | `USE` inside a multi-statement command refused (then intended) |
   | 6 | `SHOW PARTITIONS` ids and timestamps |
   | 5 | Consequences of those refusals (a rejected `ROLLBACK`, a table never created) |

   After the second pass, the same 331 sections and 5,000 statements produced 14
   differences, none from Nova: 8 from the nondeterministic `ARRAY_AGG` order
   and 6 from `SHOW PARTITIONS` ids and timestamps. The transaction and `USE`
   differences are gone.
4. **Client probes.** The MySQL 8.0 CLI and PyMySQL were run against both
   endpoints for connection-time and introspection statements that drivers and
   GUI tools send (`@@` variable batches, `SHOW FULL COLUMNS`, `SHOW TABLE
   STATUS`, `information_schema`, `SHOW ENGINES`/`COLLATION`/`CHARACTER SET`,
   `SET NAMES ... COLLATE`, `SET TRANSACTION ...`), syntax edge cases
   (optimizer hints, `/*!` executable comments, quoted semicolons and escapes,
   Unicode identifiers, lambdas, `QUALIFY`, map/struct/JSON access, bit and hex
   literals, `TABLE(generate_series())`, `unnest`), destructive DML, row limits,
   `use`/`COM_INIT_DB`, catalog switching, `KILL QUERY`, multi-statement
   results, typed user variables and a 70 KB statement. After the fixes below,
   results match the direct connection except for the intended refusals listed
   later and syntax-error wording (Nova reports a position without echoing
   tokens).

## Defects found and fixed

| Area | Defect | Fix |
| --- | --- | --- |
| Proxy splitting | The proxy split `COM_QUERY` text on `;` inside double-quoted strings, backquoted names and `\'`-escaped literals, then re-joined the pieces with `"; "`. `SELECT "a;b"` returned `a; b`, and `WHERE name = "a;b"` matched nothing. | The proxy uses the guard's quote-aware splitter, so its boundaries are the ones `QueryService` and the engine use, and re-joins with `\n;\n` so a trailing `--` comment cannot swallow the next statement. |
| Destructive SQL | `UPDATE`, `DELETE`, `TRUNCATE`, `DROP` and `ALTER ... DROP` always failed through the proxy with "requires confirmation", and the MySQL protocol has no way to confirm. | The proxy submits client statements as confirmed. The guard, engine authorization and audit still apply. |
| Row limit | Every result was truncated to 500 rows without telling the client. | `PROXY_MAX_ROWS`. A larger result is refused with error 1235, never truncated. Since the second pass the default is `0` (no limit), because results stream. |
| Multi-statement | With `CLIENT_MULTI_STATEMENTS`, only the last statement's result was sent, so a client read it as the first result. | One result per statement with `SERVER_MORE_RESULTS_EXISTS`; execution stops at the first error. |
| Database selection | `COM_INIT_DB` (the mysql CLI's `use db`) was accepted without validation, so `use missing_db` succeeded and every later statement failed. `USE catalog.db` recorded the catalog name as the database. | `COM_INIT_DB` runs `USE` through the engine. `catalog.db` is kept whole. `USE 'catalog'` and `SET CATALOG` stop re-selecting the previous catalog's database. |
| Query cancel | The handshake announced a proxy-minted id (1000+), so `KILL QUERY <id>` (mysql CLI Ctrl+C) failed with "Unknown thread id", and `CONNECTION_ID()` disagreed with the client. | The handshake announces the engine connection id. |
| Error codes | Every engine failure reached clients as 1064 with a "Connection error:" prefix, including analysis errors. | Engine failures keep StarRocks' code and message (5502 unknown table, 5501 unknown database, 1317 query killed). Client-library codes 2000-2999 keep the connection wording. |
| `default` normalization | ``catalog.`default`.table`` was rewritten to `catalog.table`, which breaks Hive/Iceberg/Paimon `default` databases. | Only the unquoted UI placeholder `db.default.table` is collapsed; `DEFAULT` is reserved in StarRocks, so the bare spelling cannot be a real name. |
| Driver session commands | `SET [SESSION] TRANSACTION ISOLATION LEVEL ...` and `SET NAMES utf8mb4 COLLATE utf8mb4_*` were refused; StarRocks accepts both. | Transaction characteristics are forwarded (an engine no-op); UTF-8 collations are acknowledged. |
| Long statements | A statement over about 64 KB (a large `INSERT ... VALUES` batch) ran, then its `AUDIT_LOG` insert failed on the 65533-byte string column, so the client received `Insert has filtered data` for a statement that had executed. Other driver error classes (`InternalError`, `IntegrityError`) also leaked in raw `(code, 'message')` form. | Audit text is truncated to 60000 bytes with a `[truncated N bytes]` marker after redaction; every driver error class is translated. |
| Typed user variables | `SET @a = [1,2,3]` was stored as the string `'[1,2,3]'`, so `array_length(@a)` failed through the proxy. | Values the engine reports with the `STRING` wire type (ARRAY, STRUCT, JSON, LARGEINT) stay on the engine session, and references to them are left for the engine. |
| Dialect switch | `SET sql_dialect = 'trino'` reached the engine, which then parsed statements with its Trino parser while Nova classified them with the StarRocks grammar. | Refused with `capability_unsupported` (1235 through the proxy). |

## Second pass: refusals and limitations removed

| Area | Before | Now |
| --- | --- | --- |
| Transactions | `BEGIN`/`COMMIT`/`ROLLBACK`, `START TRANSACTION` and `autocommit=0` were refused, which broke drivers that send `SET autocommit=0` on connect (mysql-connector-python, JDBC with `autocommit=false`). | Forwarded to the client's engine session. While a transaction is open, statements run on that session without re-selecting role and database, because StarRocks refuses `SET ROLE` inside a transaction; the proxy refuses a client `SET ROLE` there too. Verified live: `ROLLBACK` discards an insert, `COMMIT` keeps it. `autocommit=0` behaves as on a direct connection: StarRocks accepts it and keeps committing each statement. |
| Prepared statements | `PREPARE`/`EXECUTE` and `COM_STMT_PREPARE` were refused with 1235. | SQL `PREPARE`/`EXECUTE ... USING`/`DEALLOCATE` and the binary protocol (`COM_STMT_PREPARE`/`EXECUTE`/`SEND_LONG_DATA`/`CLOSE`/`RESET`) are handled by the proxy: parameters are bound as literals and the statement takes the normal `QueryService` path, rows return in the binary format. The engine's prepared protocol is not used because StarRocks 4.1.4 rejects `INSERT` (1295) and unsigned parameter types through it. Verified with mysql-connector-python, Go and Connector/J (`useServerPrepStmts=true` and `false`), including temporal, decimal, binary and NULL parameters. |
| Large results | Results were buffered and capped by `PROXY_MAX_ROWS`. | Results stream from an unbuffered engine cursor in batches of 1000 rows. 5 million rows passed with flat proxy memory. |
| Large packets | Packets over 16 MB were neither split nor reassembled. | Outgoing payloads are framed into 16 MB packets and incoming continuation frames are reassembled: a 17 MB statement and a 20 MB row round-trip with identical checksums. |
| Script context | `USE`, `SET CATALOG`, `SET ROLE` and user variables inside a multi-statement command were refused. | They apply to the statements after them. The whole script is checked (syntax, guard, policy) before its first statement runs. |
| `COM_FIELD_LIST` | Unsupported. | Answered from `SHOW COLUMNS`. |
| `SHOW DATABASES LIKE/WHERE` | Hidden-database filtering covered only the bare form. | Every form over the internal catalog is filtered. |
| Partial-segment globs | `@stage.sales_*.csv` was a syntax error. | Accepted: glob characters may join a segment when the tokens touch. `@stage1.2024.csv` keeps its numeric reading. |
| Comment after a stage path | `@stage/*.csv /* note */` silently read the whole stage. | Refused with a message pointing to the dotted spelling. |
| Physical paths | `LIST @stage` and storage errors showed `s3://bucket/prefix` and the endpoint. | Shown as `@stage/...` and `<storage endpoint>`, for streamed and buffered rows and for errors. |
| Empty globs | A CSV glob that matched no object hung the statement: header detection raised `StopIteration` in a worker thread, which never resolves the awaiting future. | The engine's own "No files were found" error is returned. |
| Syntax errors | Only a position. | Position, plus the keyword or punctuation found there; identifiers and literals are still never echoed. |
| Unmapped engine statements | Statements without an explicit effect mapping were classified as having no effects. | Treated as writing metadata unless mapped; administrative forms whose names mislead (for example `BACKUP`, `RESTORE`, `INSTALL PLUGIN`) have explicit effects. |
| Parser speed | Every statement used full LL prediction. | SLL first, LL on failure. The offline corpus produced identical trees in both modes (0 mismatches). |
| Plan advisor | `ALTER PLAN ADVISOR` had a duplicate Nova rule next to the upstream one. | The duplicate was removed, so the upstream rule and its effects apply. |

## Intended refusals

These remain refused through Nova by design; each refusal is explicit:

- `SELECT ... INTO OUTFILE` and `INSERT INTO FILES(...)` (data-egress guard).
- `SET ROLE ALL`/`NONE` and role lists; exactly one named role is active.
- `SET ROLE` inside a transaction (StarRocks refuses it as well).
- `SET GLOBAL ...` through the proxy.
- `SET sql_dialect` to anything other than StarRocks.
- `COM_STMT_FETCH` (server-side cursors); prepared results are sent in full.
- `SHOW DATABASES` hides `NOVA_SYSTEM`, `information_schema`, `sys` and
  `_statistics_`.

## Remaining limitations

- **`@stage/*.csv`** is still stripped by the mysql CLI before it reaches
  Nova, because `/*` opens a comment there; write `@stage.*.csv`.
- **`autocommit=0`** does not open an implicit transaction; StarRocks itself
  ignores it. Use `BEGIN`.
- **Syntax-error messages** never echo identifiers or literals, by design.
- **`ARRAY_AGG(DISTINCT x ORDER BY y)`** returns different orders on repeated
  runs in StarRocks itself; differential runs show it as a difference that is
  not Nova's.
- The corpus and differential runs used the stock FE without Ranger. Governed
  behavior was checked with `backend/scripts/verify_ranger_e2e.py` and
  prepared-statement probes on the patched FE, not statement by statement.

## Reproduction

Stack: `docker compose -f backend/docker-compose.test.yml up -d --wait`, then
`docker/init-nova.sql` and `backend/tests/integration/seed_engine.sh`. The
sandbox used for this run could not pull from ghcr.io, so MinIO came from
`bitnamilegacy/minio`, and the BE needed `min_file_descriptor_number` lowered to
start under a 20000 file-descriptor limit. Neither change is committed.

Corpus: StarRocks `test/sql` at the pinned commit
(`git clone --filter=blob:none --sparse`, then `git sparse-checkout set test/sql`).
The extraction and differential scripts were run locally from a scratch
directory and are not part of the repository.

Repository checks on the delivered change (second pass, after merging `main`):

- Backend unit suite with branch coverage: 7,158 passed.
- Agent eval report: 48/48 scenarios, 165/165 checks.
- Changed-file Ruff, grammar drift, and parser regeneration (byte-identical).
- `pytest tests/integration -m engine` on a freshly created and seeded stack:
  210 passed, 16 skipped (opt-in environments: patched-FE Ranger fixtures,
  shared-data, disposable Streams FEs, UDF/TASK-enabled engines and live
  providers), 0 failed.
  `test_intelligence_studio_live.py::
  test_bootstrap_and_review_use_production_api_with_restricted_identity` is
  included; it did not hang, it needs about 16 minutes.
- Functional SQL matrix, `core` suite through the proxy with the MySQL 8.0 CLI
  checks: 161/161 passed.
- Ranger acceptance (`backend/scripts/verify_ranger_e2e.py`) against the patched
  4.1.4 FE built from `docker/ranger/starrocks-fe.Dockerfile`, Ranger Admin 2.9.0
  and the policy bridge: passed, including the client-transaction scenario.
  Prepared statements (binary protocol and SQL `PREPARE`) returned the same
  row-filtered and masked values.

The sandbox needed local-only workarounds that are not committed. ghcr.io is
unreachable, so MinIO images came from `bitnamilegacy`. The session disk
allowance makes the BE report about 95% usage, so the BE flood stage and FE
storage limits were raised. Container `nofile`/`nproc` limits were lowered to
what the sandbox allows. The image builds trusted the session's egress proxy
CA, with TLS verification kept on. The acceptance script's
`/proc/1/environ` helper was stubbed on the host, because the configuration
was already in the environment.
