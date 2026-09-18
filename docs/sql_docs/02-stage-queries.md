# 02 — Stage Queries (`@stage` dialect)

> `@stage_name[.path][/][.file.ext]` is Nova's file-access abstraction. The user writes a stage reference; Nova rewrites it to a StarRocks `FILES()` call with the real path, the detected format, and injected credentials.

Sources: `backend/app/modules/query/dialect/parser.py`, `translator.py`, `detector.py`, `injector.py`.

---

## Concept

A **stage** is a named pointer to a storage location, stored in `NOVA_SYSTEM.CONFIG_STAGES` (`name`, `database_name`, `schema_name`, `storage_connection`, `base_prefix`). The user never sees the bucket, endpoint, or credentials — only the stage name. This is the storage-agnostic rule from `AGENTS.md`.

```
@stage1.data.csv
   │  stage name: stage1
   │  path parts: ['data']      (the last dotted part 'csv' is a known file extension)
   │  file name:  data.csv
   ▼
FILES('path'='s3://bucket/db/schema/stage1/data.csv', 'format'='csv', creds…)
```

---

## Syntax

```
@name                          bare stage — names the prefix itself
@name/                         directory form (trailing slash)
@name.path.file.ext            file form — last dotted part is a known extension
@name.folder.file.ext          nested file form
@silver.stage1.folder.file.parquet
```

- `name` starts with a letter or underscore and may contain letters, digits, underscores, and hyphens.
- The path may mix `.` and `/` separators, contain letters, digits, underscores, and hyphens, and include a `*` glob segment.
- A trailing `/` marks a directory.

### `@stage` is not `@@variable`

`@@name` is a MySQL system variable. Since 109-B (NOVA-126) the parser builds the stage registry from the ANTLR4 parse tree, and `@@version_comment` lexes as two `AT` tokens beginning a `systemVariable` — the grammar never reaches the `stageReference` rule for it, so it can never be read as a stage named `version_comment`. This matters in practice: every MySQL client asks for `@@version_comment` on connect.

---

## Stage vs user variable — decided by the grammar's position

A stage and a user variable are spelled identically: `@x` in `SELECT @x` and `@stage1` in `SELECT * FROM @stage1`. The grammar decides by **position**: its `stageReference` rule sits under `relationPrimary`, so a stage is only recognised where a table may stand. The bare and directory forms (`@stage1`, `@stage1/`) are admitted there too.

| Position | Interpretation |
|----------|----------------|
| In a table position (`FROM` / `JOIN` / subquery / CTE / `INSERT … SELECT`) | Stage |
| Anywhere else — a select list, `WHERE`, a function argument | User variable |

Because the decision is the grammar's, there is no comma-subquery or `ON`-clause heuristic to get wrong: `FROM (SELECT @x, @y FROM t), @stage2` recognises only `@stage2`, and `SELECT @x, @y FROM @stage1, @stage2` recognises both stages.

### Literals and comments are data

A reference inside a string literal or a comment is not a stage: the lexer emits it as a single hidden/string token, so the parser never builds a `stageReference` for it. This is not cosmetic: an earlier regex revision read the `FROM` in `'FROM @x'` as the live keyword and rewrote the user's literal into `FILES(...)` with credentials injected into it (`NOVA-29`).

---

## Command types

`detect_command_type` classifies the statement from the parsed tree plus the leading tokens:

| `CommandType` | Trigger |
|---------------|---------|
| `STAGE_QUERY` | A `SELECT` / `WITH` / `EXPLAIN` / `DESCRIBE` / `SHOW` statement carrying a stage |
| `STAGE_BROWSE` | `LIST @stage` |
| `STAGE_LOAD` | `COPY INTO <table> FROM @stage` |
| `STAGE_EXPORT` | `COPY INTO @stage FROM <table>` |
| `REGULAR` | Anything else, **or** any statement with no stage reference |

`LIST` and `COPY INTO` are Nova surfaces the StarRocks 4.1 grammar does not model, so their references are scanned from the token stream (`_nova_surface_stage_refs`). `LIST` is a special case: Nova parses `LIST @stage1` as `STAGE_BROWSE`, but **nothing implements `LIST` and StarRocks has no `LIST` statement**, so any `LIST` that reaches execution fails at the engine. A `LIST` carrying a stage is reported as `STAGE_BROWSE`; a bare `LIST` with no stage falls back to `REGULAR` and travels untouched, failing visibly with the engine's own syntax error.

A syntax error does not raise from `parse_sql`: it is recorded in `ParsedSQL.errors` with the exact `line:col`, and the registry stays empty so a malformed statement is never partially rewritten.

> The `CommandType` enum and the docs mention `LIST`, but there is no implementation behind it. Treat `LIST` as documented-but-unimplemented.

---

## Translation

`translate_stage_query(parsed, stage_configs, format_overrides=None)` (`translator.py:87`):

1. For each reference, look up the stage in `stage_configs`. Missing → `ValueError("Stage '<name>' not found")`.
2. Build the S3 path (`build_s3_path`):

   ```
   parts = [base_prefix] + path_parts + [file_name?]
   → s3://{bucket}/{joined}
   ```

   With `base_prefix='datalake/bronze/stage1'` and `@stage1.folder.file.parquet`:
   `s3://nova-stages/datalake/bronze/stage1/folder/file.parquet`.

3. Determine the format:
   - `format_overrides[stage]` if supplied;
   - else from the file name extension;
   - else `csv` + a warning (`"⚠️ No file extension for @<stage>, defaulting to CSV"`).
4. Replace the reference text with a `FILES(...)` call. Every reference in the statement is rewritten — a comma list of two stages becomes two `FILES()` calls.
5. Append `"Resolved @<stage> reference for execution"` to warnings.

### Format detection

`detect_format_from_filename` (`translator.py:143`) handles compound extensions:

| Input | Output |
|-------|--------|
| `data.csv` | `csv` |
| `events.parquet` | `parquet` |
| `config.json` | `json` |
| `data.csv.gz` | `csv` (compression stripped) |
| `events.parquet.snappy` | `parquet` |
| `data` (no extension) | `csv` |

Mapping: `csv`/`tsv`/`txt`/`xlsx`/`xls`/`log`/`sql` → `csv`; `json`/`jsonl`/`ndjson`/`xml` → `json`; `parquet` → `parquet`; `orc` → `orc`; `avro` → `avro`. Compression extensions `gz`/`bz2`/`snappy`/`zstd`/`lzo` are stripped first.

The content-based detector (`detector.py`) provides `detect_format_from_content` (magic bytes: `PAR1` → parquet, `ORC` → orc, gzip → csv, `{`/`[` → json) and `detect_format_from_listing` (dominant format among sampled keys). These are used when a reference has no extension; **no extension also produces the CSV-default warning**.

---

## CSV auto-detection

CSV and TSV files are pre-read so the engine receives the right parser. This is I/O and lives in the caller (`QueryService._detect_csv_params`, `service.py:1104`), not in the pure pipeline.

1. Only the **first** stage reference is inspected.
2. Only for `csv`/`tsv` extensions.
3. A `boto3` range read of the first 8 KB.
4. Delimiter detected by counting `,` `;` `\t` `|` in the first line; the most frequent wins, default `,`.
5. Enclosure detected if the first line starts and ends with `"`.
6. Header detection: if the first and second lines have the same field count and most first-line fields are non-numeric, the first line is a header.
7. Produces `FILES()` params: `csv.column_separator`, `csv.trim_space=true`, optionally `csv.enclose` + `csv.escape`, and `csv.skip_header=1`.
8. Header names are captured and **rename the result columns** in the response (the CSV parser yields `$1`, `$2`, …).

Failure to pre-read (unreachable endpoint, missing credential, <2 lines) falls back to defaults and logs a warning; the query still runs, just untuned.

---

## Credential injection

`get_credential_params("s3")` (`injector.py:26`) reads the storage connection from `nova.yaml`/env via `app.core.config`. There is **no hardcoded fallback** — when nothing is configured the parameter set is empty and the `FILES()` call fails loudly instead of authenticating with a well-known default.

For S3/MinIO the injected params are:

```
'aws.s3.access_key'='***'
'aws.s3.secret_key'='***'
'aws.s3.endpoint'=…          (if configured)
```

The translator can also inject credentials directly when it is given a `StorageConfig` with `access_key`, in which case it additionally emits `aws.s3.region`, and for non-HTTPS endpoints `aws.s3.enable_ssl=false`, `aws.s3.enable_path_style_access=true`, `aws.s3.use_aws_sdk_default_behavior=false`, `aws.s3.use_instance_profile=false`. Both paths are per-key idempotent, so no parameter is duplicated.

> This section documents *that* injection happens and what the resulting parameter names are. It intentionally does not document how to cause a value to leak or how to read one back.

Azure and GCS credential injection are not yet implemented (`injector.py:46`); the functions return an empty dict for those types.

---

## Examples that pass the pipeline

### Simple file query

```sql
-- user writes
SELECT * FROM @stage1.data.csv LIMIT 5

-- engine receives (credentials shown as placeholders)
SELECT * FROM FILES('path'='s3://bucket/datalake/bronze/stage1/data.csv',
  'format'='csv', 'aws.s3.access_key'='K', 'aws.s3.secret_key'='S') LIMIT 5
```

### Nested path with parquet

```sql
SELECT * FROM @silver.stage1.folder.events.parquet
```

→ `s3://{bucket}/{silver-prefix}/folder/events.parquet`, format `parquet`.

### Directory form

```sql
SELECT * FROM @stage1/
```

→ format defaults to `csv` with the "No file extension" warning.

### Two stages in one statement

```sql
SELECT * FROM @stage1.a.csv JOIN @stage2.b.csv ON a.id = b.id
```

→ two `FILES()` calls, both injected.

### A variable and a stage together

```sql
SELECT @x, * FROM @stage1.data.csv
```

→ `@x` stays a variable; only `@stage1` is rewritten.

### Literal that names a stage

```sql
SELECT * FROM t WHERE name = 'FROM @stage1'
```

→ passes through byte-for-byte. No `FILES()`, no credentials, no warning.

---

## Limitations and error paths

| Situation | Behaviour |
|-----------|-----------|
| `@x` in an expression position | Treated as a user variable, not a stage. |
| `@@var` | Never a stage (lookbehind). |
| Reference inside a literal/comment | Data; ignored. |
| Unknown stage name | `ValueError("Stage '<name>' not found")`; the workspace returns it as the statement error and audits `ERROR`. |
| No file extension | Defaults to `csv` + warning. |
| CSV pre-read fails | Defaults; query runs, logs the cause. |
| `LIST @stage` | Translated, then rejected by the engine (no native `LIST`). |
| `@stage` with a `/` in the dotted path | Only a single trailing `/` is supported; path segments use dots. |

### `db.default.table` normalization (related)

Before parsing, `QueryService.execute` normalizes the UI's `<db>.default.<table>` placeholder to `<db>.<table>` (`service.py:67`). The rewrite is anchored to a table position (`FROM`/`JOIN`/`INTO`/`UPDATE`/`TABLE`/comma), so `config.default.value` is untouched. `@stage` paths are masked before this normalization so a stage path containing `default` is never rewritten.

---

## Verification

| Claim | Test |
|-------|------|
| Dotted, bare, and directory forms all parse as stages | `test_dialect.py::TestStageVersusVariable::test_bare_and_directory_stage_forms_are_stages` |
| Expression operands are variables | `test_dialect.py::TestStageVersusVariable::test_expression_operand_is_a_variable` |
| `@@` system variables are not stages | `test_dialect.py::TestStageVersusVariable::test_system_variables_are_not_stages` |
| A comma in a FROM list adds a stage; in an expression list does not | `test_dialect.py::TestStageVersusVariable::test_a_comma_in_a_from_list_introduces_another_stage` / `...expression_list_does_not_create_stages` |
| Literals/comments are data | `test_dialect.py::TestStageVersusVariable::test_a_stage_keyword_inside_a_literal_or_comment_is_not_a_stage` |
| Literal naming a stage passes through untouched | `test_dialect.py::TestTranslator::test_translation_leaves_a_literal_naming_a_stage_untouched` |
| Bare/directory stages reach translation | `test_dialect.py::TestBareAndDirectoryStagesReachTranslation` |
| Format detection incl. compound extensions | `test_dialect.py::TestTranslator::test_detect_format_*` |
| Unknown stage raises | `test_dialect.py::TestTranslator::test_translate_missing_stage_raises` |
| CSV params + credentials coexist | `test_sql_pipeline_files_params.py` |
