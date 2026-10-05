# SQL compatibility audit: Nova and StarRocks 4.1.4 through MySQL clients

Date: 2026-10-05. Engine: StarRocks `4.1.4` (`4a9848edf03f5c936dac664b2d52527f48e72eb0`),
stock FE/BE images from `backend/docker-compose.test.yml`, Ranger disabled. Client:
MySQL 8.0.46 CLI (`mysql:8.0`) and PyMySQL against the standalone proxy
(`python -m app.proxy`, port 4406).

This is a point-in-time measurement. It does not cover governed Ranger behavior on
the patched FE.

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
   | 12 | `BEGIN`/`COMMIT`/`ROLLBACK` refused (intended) |
   | 4 | `USE` inside a multi-statement command refused (intended) |
   | 6 | `SHOW PARTITIONS` ids and timestamps |
   | 5 | Consequences of those refusals (a rejected `ROLLBACK`, a table never created) |
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
| Row limit | Every result was truncated to 500 rows without telling the client. | `PROXY_MAX_ROWS` (default 100000). A larger result is refused with error 1235, never truncated. |
| Multi-statement | With `CLIENT_MULTI_STATEMENTS`, only the last statement's result was sent, so a client read it as the first result. | One result per statement with `SERVER_MORE_RESULTS_EXISTS`; execution stops at the first error. |
| Database selection | `COM_INIT_DB` (the mysql CLI's `use db`) was accepted without validation, so `use missing_db` succeeded and every later statement failed. `USE catalog.db` recorded the catalog name as the database. | `COM_INIT_DB` runs `USE` through the engine. `catalog.db` is kept whole. `USE 'catalog'` and `SET CATALOG` stop re-selecting the previous catalog's database. |
| Query cancel | The handshake announced a proxy-minted id (1000+), so `KILL QUERY <id>` (mysql CLI Ctrl+C) failed with "Unknown thread id", and `CONNECTION_ID()` disagreed with the client. | The handshake announces the engine connection id. |
| Error codes | Every engine failure reached clients as 1064 with a "Connection error:" prefix, including analysis errors. | Engine failures keep StarRocks' code and message (5502 unknown table, 5501 unknown database, 1317 query killed). Client-library codes 2000-2999 keep the connection wording. |
| `default` normalization | ``catalog.`default`.table`` was rewritten to `catalog.table`, which breaks Hive/Iceberg/Paimon `default` databases. | Only the unquoted UI placeholder `db.default.table` is collapsed; `DEFAULT` is reserved in StarRocks, so the bare spelling cannot be a real name. |
| Driver session commands | `SET [SESSION] TRANSACTION ISOLATION LEVEL ...` and `SET NAMES utf8mb4 COLLATE utf8mb4_*` were refused; StarRocks accepts both. | Transaction characteristics are forwarded (an engine no-op); UTF-8 collations are acknowledged. |
| Long statements | A statement over about 64 KB (a large `INSERT ... VALUES` batch) ran, then its `AUDIT_LOG` insert failed on the 65533-byte string column, so the client received `Insert has filtered data` for a statement that had executed. Other driver error classes (`InternalError`, `IntegrityError`) also leaked in raw `(code, 'message')` form. | Audit text is truncated to 60000 bytes with a `[truncated N bytes]` marker after redaction; every driver error class is translated. |
| Typed user variables | `SET @a = [1,2,3]` was stored as the string `'[1,2,3]'`, so `array_length(@a)` failed through the proxy. | Values the engine reports with the `STRING` wire type (ARRAY, STRUCT, JSON, LARGEINT) stay on the engine session, and references to them are left for the engine. |
| Dialect switch | `SET sql_dialect = 'trino'` reached the engine, which then parsed statements with its Trino parser while Nova classified them with the StarRocks grammar. | Refused with `capability_unsupported` (1235 through the proxy). |

## Intended refusals (unchanged)

These remain refused through Nova by design; each refusal is explicit:

- `SELECT ... INTO OUTFILE` and `INSERT INTO FILES(...)` (data-egress guard).
- `PREPARE`/`EXECUTE` and the binary prepared-statement protocol (drivers fall
  back to the text protocol on 1235).
- `BEGIN`/`COMMIT`/`ROLLBACK`, `START TRANSACTION`, `autocommit=0`; proxy
  sessions are not borrowed for engine transactions.
- `SET ROLE ALL`/`NONE` and role lists; exactly one named role is active.
- `SET GLOBAL ...` through the proxy.
- `USE`, `SET CATALOG` and role changes inside a multi-statement command; send
  them as separate commands (the mysql CLI already does).
- `SHOW DATABASES` hides `NOVA_SYSTEM`, `information_schema`, `sys` and
  `_statistics_`.

## Remaining limitations

- **Prefix globs** such as `@stage.sales_*.csv` are a syntax error: the
  documented glob is a whole segment (`@stage.*.csv`). Supporting partial
  segments needs a grammar change.
- **`/*` after a stage path** opens a comment. `@stage/*.csv` is stripped by the
  mysql CLI and becomes a comment when a later `*/` appears; use the dotted form.
- **Result sets are buffered** in the proxy; `PROXY_MAX_ROWS` bounds memory,
  and larger extracts need `LIMIT` paging or a higher limit. Streaming results
  would remove the limit.
- **Stage listings and storage errors show physical `s3://` paths** to the
  client (`LIST @stage`, `FILES()` errors). This audit did not change them.
- **Syntax-error messages** carry only a position, by design.
- **`ARRAY_AGG(DISTINCT x ORDER BY y)`** returns different orders on repeated
  runs in StarRocks itself; differential runs show it as a difference that is
  not Nova's.
- Governed (Ranger, patched FE) behavior was not exercised in this run.

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

Repository checks on the delivered change: backend unit suite with coverage
(6,733 passed), agent eval report (48/48 scenarios), changed-file Ruff, grammar
drift, and `pytest tests/integration -m engine` (200 passed, 14 skipped for
opt-in environments). `test_intelligence_studio_live.py::
test_bootstrap_and_review_use_production_api_with_restricted_identity` was
deselected: it did not finish within 900 s on a fresh engine on `main` as well
as on this change.
