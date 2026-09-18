# 11 — Katalog Query Nova & Semantik StarRocks 4.1.4

> Katalog lengkap setiap konstruksi SQL yang dapat dikirim ke engine melalui Nova, pasangan resmi StarRocks-nya, dan format yang sebenarnya diterima engine.

- **Engine pin:** StarRocks `4.1.4`, commit `4a9848edf03f5c936dac664b2d52527f48e72eb0`.
- **Tanggal akses:** 2026-09-18.
- **Kanal eksekusi:** SQL Workspace (UI) → `POST /api/v1/query/execute` (`backend/app/main.py:126`, prefix `/api/v1`); atau MySQL Proxy port 4406. **Bukan** port `9030` (itu FE MySQL native, dipakai internal).
- **Kredensial:** dokumen ini **tidak memuat** nilai kredensial; semua contoh memakai placeholder.

---

## Peta kelas statement

| Kelas | Ditangani oleh | Dikirim ke StarRocks? | Padanan resmi |
|---|---|---|---|
| Regular SQL | `QueryService.execute` → repo | Ya, apa adanya | SQL standar StarRocks |
| `@stage` reference | `parse_sql` → `translate_stage_query` → `_inject_files_params` | Ya, sebagai `FILES(...)` | `FILES()` table function |
| `CREATE ML_MODEL ... AS SELECT` | `is_create_ml_model` → `ml_engine_service.train_model` | **Tidak** | tidak ada |
| `CREATE TASK` (Nova) | `is_create_task` → lowering ke `CONFIG_TASK*` | **Tidak** (statement; SUBMIT TASK dijalankan worker) | `SUBMIT TASK` (engine) + grammar Nova |
| `AI_*` (7 fungsi) | SQL UDF di StarRocks | Ya (UDF wrapper) | `ai_query()` |
| `ML_PREDICT` | UDF placeholder | Ya (UDF placeholder) | tidak ada |
| `NOVA_SYSTEM.*` | dibaca Nova langsung / via SQL | Ya (tabel biasa) | tidak ada |

---

## 1. `@stage` — referensi file

**Bentuk yang user tulis** (semua lolos parser, `test_dialect.py:40,94-96`):
```
@stage1
@stage1/
@stage1.data.csv
@silver.stage1.folder.file.parquet
```
Aturan klasifikasi (`parser.py:244-291`): token `@name` adalah **stage** bila diawali segmen path (`.`/`/`) atau berada setelah keyword `FROM`/`JOIN`/`INTO`/`LIST`/`USING` (melewati qualifier `FILES`/`ALL`/`DISTINCT`). Di posisi ekspresi (`SELECT @x`, `1 + @n`, `SET @x = 1`) ia adalah **user variable**. `@@name` selalu bukan stage.

**Transformasi (sebelum → sesudah):**
```
-- user
SELECT * FROM @stage1.data.csv
-- engine (credentials disuntik, nilai disamarkan di sini)
SELECT * FROM FILES('path'='s3://<bucket>/<prefix>/data.csv', 'format'='csv',
                    'csv.column_separator'=',', 'csv.trim_space'='true', 'csv.skip_header'='1',
                    '<cred>'='***', ...)
```
Parameter yang mungkin disuntik (`translator.build_files_function`, `sql_pipeline._inject_files_params`, `service._detect_csv_params`):
- Kredensial: `aws.s3.access_key`, `aws.s3.secret_key`, `aws.s3.endpoint` (dari `injector.get_credential_params`).
- Translator juga dapat menambah `aws.s3.region`, `aws.s3.enable_ssl`, `aws.s3.enable_path_style_access`, `aws.s3.use_aws_sdk_default_behavior`, `aws.s3.use_instance_profile` bila `config.access_key` terisi (`translator.py:70-82`).
- CSV (auto-detect boto3, `service.py:1209-1217`): `csv.column_separator`, `csv.trim_space`, `csv.skip_header`, `csv.enclose`, `csv.escape`.

**Semantik StarRocks resmi.** `FILES()` adalah table function dengan signature ke-7 argumen (lihat `10-starrocks-reference-comparison.md`). Kolom CSV dapat dirujuk `$1, $2, …` atau `*`. Nova mengganti nama kolom `$1..$n` dengan header CSV yang terdeteksi lewat `result.columns[i] = col_name` (`service.py:358-362`).

**Batasan/error path:**
- Stage tidak dikenal → `ValueError("Stage '<name>' not found")` (`translator.py:115`); dicatat sebagai audit ERROR, dan `QueryResult.error` diisi (`service.py:287-313`).
- Tidak ada ekstensi file → default `csv` + warning `⚠️ No file extension for @<name>, defaulting to CSV` (`translator.py:128-129`).
- `LIST @stage` **diparse** sebagai `STAGE_BROWSE` tetapi engine **tidak punya** `LIST`; tanpa referensi stage, parser menurunkannya ke `REGULAR` dan engine yang mengembalikan syntax error (`parser.py`, `parse_sql`).
- Teks `@x` di dalam literal/komentar **bukan** stage (NOVA-29) — sejak 109-B (NOVA-126) lexer ANTLR4 menganggapnya satu token, jadi tidak pernah jadi node `stageReference` (`parse_sql`).

**Bukti uji:** `test_dialect.py` (kelas `TestParser`, `TestStageVersusVariable`, `TestTranslator`, `TestBareAndDirectoryStagesReachTranslation`), `test_sql_pipeline_files_params.py`.

**Invariant:** `@stage` tetap abstraksi first-class; SQL Nova hanya lewat SQL Workspace / `POST /api/v1/query/execute`, bukan port `9030` (lihat `AGENTS.md` dan `proxy/README.md`). Kredensial storage tidak boleh muncul di SQL contoh publik, response API, atau UI.

---

## 2. `CREATE ML_MODEL` — DDL training Nova

**Bentuk:**
```sql
CREATE ML_MODEL <name>
  TYPE = CLASSIFICATION | REGRESSION
  TARGET = <column>
  [ALGORITHM = auto|linear|logistic|decision_tree|random_forest|gradient_boost|knn|svm]
  [TEST_SIZE = 0.0..0.999]
  [FEATURES = (f1, f2, ...)]
  [HYPERPARAMETERS = JSON '{"n_estimators": 100}']
  AS SELECT ...
```
Parser: `dialect/ml_model.py` (`parse_create_ml_model`). Interception: `service.py:234` → `_execute_create_ml_model` → `ml_engine_service.train_model` (`ml_engine/service.py:116`). **Tidak pernah** dikirim ke StarRocks.

**Algoritma terdaftar** (`ml_engine/service.py:56-74`): classification = logistic/linear, decision_tree, random_forest, gradient_boost, knn, svm; regression = linear, decision_tree, random_forest, gradient_boost, knn, svm. `auto` memilih berdasarkan jumlah baris (`_pick_algorithm`, baris 77-94).

**Validasi/error path (`ml_model.py`):**
- Tidak ada `AS SELECT` → `Invalid CREATE ML_MODEL syntax...` (baris 74-78).
- Tanpa `TYPE` → `CREATE ML_MODEL requires TYPE = ...` (baris 83-84).
- Tanpa `TARGET` → `... requires TARGET = target_column` (baris 85-86).
- `TEST_SIZE` di luar `[0,1)` → error (baris 94-95).
- `HYPERPARAMETERS` bukan JSON object valid → error (baris 102-106).
- Training: minimal 10 baris valid (`service.py:201-202`); `target_column` tidak ada → error (baris 178-179).

**Semantik StarRocks:** **tidak ada padanan** — engine 4.1.4 tidak punya `CREATE ML_MODEL` (lihat `10-...md` §2). Parser ANTLR Nova pun tidak menambahkan rule ini; interception murni di layer Python.

**Output kolom** (`service.py:546-556`): `model_id, model_name, model_type, algorithm, version, status, training_rows, feature_columns, metrics`.

**Bukti uji:** `test_ml_model_ddl.py` (parse + routing `test_query_service_routes_create_ml_model_to_ml_engine`).

**Penyimpanan:** `NOVA_SYSTEM.ML_MODELS` (metadata + `training_sql` yang sudah diredaksi) dan `NOVA_SYSTEM.ML_MODEL_VERSIONS` (`model_binary` base64 joblib, `metrics`). `training_sql` disimpan dalam bentuk redacted (`_redacted_user_sql`, `service.py:152`).

---

## 3. `AI_*` — 7 fungsi LLM

Daftar: `AI_COMPLETE`, `AI_SENTIMENT`, `AI_CLASSIFY`, `AI_SUMMARIZE`, `AI_EXTRACT`, `AI_TRANSLATE`, `AI_FILTER` (`sql_guard.py:46-55`).

**Signature UDF** (`llm_functions/service.py:32-75`):

| Fungsi | Parameter | Body inti |
|---|---|---|
| `AI_COMPLETE` | `prompt STRING` | `ai_query(prompt, '{config}')` |
| `AI_SENTIMENT` | `txt STRING` | prompt tetap + JSON sentiment |
| `AI_CLASSIFY` | `txt, categories STRING` | pilih satu kategori |
| `AI_SUMMARIZE` | `txt STRING` | ringkas 2–3 kalimat |
| `AI_EXTRACT` | `txt, json_schema STRING` | JSON sesuai schema |
| `AI_TRANSLATE` | `txt, target_lang STRING` | terjemah |
| `AI_FILTER` | `txt, criteria STRING` | `"true"`/`"false"` |

**Semantik StarRocks resmi.** Semua adalah **SQL UDF** yang membungkus builtin `ai_query(VARCHAR, JSON) -> VARCHAR`. Registrasi engine pada commit pin: `gensrc/script/functions.py:1520` → `[200000, 'ai_query', True, False, 'VARCHAR', ['VARCHAR', 'JSON'], "AiFunctions::ai_query"]`. Field config yang valid (dari source engine, bukan docs): `model` (wajib), `api_key` (wajib), `endpoint` (opsional), `temperature`, `max_tokens`, `top_p`, `timeout_ms`. Nova mengisi `model`, `api_key`, `endpoint` (`service.py:456-490`) dan menambahkan `default_params` alias bila ada.

**Registrasi & grant.** `init-nova.sql:743-781` membuat placeholder; `llm_functions/service.py:_register_single_udf` (baris 422) DROP lalu CREATE ulang; `_grant_udf_privileges` (baris 361) memberi `GRANT USAGE ON GLOBAL FUNCTION ...` ke role `root`, `db_admin`, `cluster_admin`, `user_admin`, `ACCOUNTADMIN` dengan tiga varian signature (`STRING`, `VARCHAR`, `VARCHAR(65533)`).

**Error path bila belum dikonfigurasi.** UDF placeholder mengembalikan `ERROR: <fn> not configured. Set up an alias in AI Providers > Functions tab. Input was: ...` (baris 546-586) sehingga `SELECT` tidak gagal "function not found".

**Keamanan:** `api_key` disimpan terenkripsi (`common/crypto.encrypt`, `ai_ml/service.py:148`) dan **tidak pernah** dikembalikan plaintext ke API (`_mask_api_key`, `list_providers`). UDF yang diregistrasi memuat api_key di dalam body SQL-nya — ini ada di dalam engine, bukan di response Nova.

**Catatan sinkronisasi:** contoh `workspace/ai_functions_samples.sql` masih menyebut `endpoint_url`; kode dan engine memakai `endpoint`. Gunakan `endpoint`.

**Bukti uji:** `test_functions_udf_databases.py`; guard `test_sql_guard.py` (tidak bisa DROP UDF builtin).

---

## 4. `ML_PREDICT` — prediksi ML

**Bentuk UDF placeholder** (`init-nova.sql:778-781`):
```sql
CREATE GLOBAL FUNCTION ML_PREDICT(model_alias STRING, features_json STRING)
RETURNS CONCAT('Use POST /api/v1/ml/predict with {"model_alias":"', model_alias, '","features":', features_json, '} to get prediction');
```

**Status nyata:** UDF hanya mengembalikan instruksi API. `common/ml_intercept.py` mendefinisikan `detect_ml_predict`/`rewrite_ml_predict_sql`, tetapi **tidak ada import** ke modul itu di seluruh `backend/app` (dicari dengan `rg "ml_intercept" backend/app backend/tests` — hanya definisi di file itu sendiri) — jalur intercept SQL **belum aktif**. Prediksi sesungguhnya via REST: `POST /api/v1/ml/predict` (single) dan `POST /api/v1/ml/predict/batch` (`ml_engine/router.py:69,84`), dijalankan `ml_engine_service.predict`/`batch_predict`.

**Semantik StarRocks:** tidak ada `ML_PREDICT` di engine (lihat `10-...md` §4). Keyakinan: Tinggi.

**Bukti uji:** `test_ml_engine_batch_predict.py`, `test_ml_engine_training_sql.py`.

**Invariant:** `ML_PREDICT` tidak boleh dipresentasikan sebagai paralel `ai_query`; ia permukaan UDF + API.

---

## 5. `NOVA_SYSTEM` — tabel sistem Nova

**Daftar tabel (dari `docker/init-nova.sql`):**

| Grup | Tabel | Jenis |
|---|---|---|
| CONFIG | `CONFIG_STAGES`, `CONFIG_PINNED_QUERIES`, `CONFIG_USER_PREFERENCES`, `CONFIG_WORKSPACE_ENTRIES`, `CONFIG_AI_PROVIDERS`, `CONFIG_AI_MODELS`, `CONFIG_OBJECT_TAGS`, `CONFIG_DASHBOARDS`, `CONFIG_DASHBOARD_WIDGETS`, `CONFIG_MODEL_ALIASES` | Primary Key |
| CONFIG (tasks) | `CONFIG_TASKS`, `CONFIG_TASK_EDGES`, `CONFIG_TASK_GRAPH_RUNS`, `CONFIG_TASK_RUNS` | Primary Key |
| ML | `ML_MODELS`, `ML_MODEL_VERSIONS`, `ML_MODEL_ALIASES` | Duplicate Key |
| AUDIT | `AUDIT_LOG` | Duplicate Key, partisi bulanan |
| STAGE | `STAGE_FILE_MANIFEST` | Duplicate Key |
| LINEAGE | `LINEAGE_LOAD_HISTORY` | Duplicate Key, partisi bulanan |
| QUALITY | `QUALITY_TABLE_STATS` | Duplicate Key |
| USAGE | `USAGE_QUERY_STATS` | Duplicate Key, partisi bulanan |

**Semantik StarRocks:** `NOVA_SYSTEM` adalah database biasa berisi tabel Primary/Duplicate Key biasa. Padanan `information_schema` **tidak ada** — `information_schema` di StarRocks adalah database metadata read-only terpisah. Nova membacanya untuk: `information_schema.columns`, `information_schema.tasks`/`task_runs`, `information_schema.applicable_roles`, `information_schema.tables_config`/`tables`/`views`.

**Contoh query yang valid (aman, tanpa kredensial):**
```sql
SELECT model_id, model_name, model_type, created_at
FROM NOVA_SYSTEM.ML_MODELS ORDER BY created_at DESC;

SELECT m.model_name, v.version, v.status, v.training_rows, v.metrics
FROM NOVA_SYSTEM.ML_MODELS m
JOIN NOVA_SYSTEM.ML_MODEL_VERSIONS v ON m.model_id = v.model_id
ORDER BY m.model_name, v.version DESC;

SELECT event_time, user_name, action, status, duration_ms
FROM NOVA_SYSTEM.AUDIT_LOG
WHERE event_type = 'query' ORDER BY event_time DESC LIMIT 20;
```

**Error/visibility path:** `NOVA_SYSTEM` termasuk `HIDDEN_DATABASES` di proxy (`proxy/session.py:509`), bersama `information_schema`, `sys`, `_statistics_` — artinya tidak muncul di listing katalog user.

**Bukti uji:** `test_audit_credential_redaction.py`, `test_credential_leaks.py`, `test_query_pre_engine_audit.py`, `test_task_orchestration_*`.

**Invariant:** tidak ada kolom kredensial di `NOVA_SYSTEM` (dinyatakan `nova_system.py:154-156`); `init-nova.sql` juga credential-free.

---

## 6. Grammar vendored (`StarRocks.g4`)

- `backend/app/sql_dialect/grammar/StarRocks.g4` + `StarRocksLex.g4`, upstream di `grammar/upstream/`.
- Pin: tag `4.1.4`, commit `4a9848edf03f5c936dac664b2d52527f48e72eb0`; sha256 upstream cocok dengan header.
- Generated Python di-commit: `StarRocksParser.py`, `StarRocksLexer.py`, `StarRocksVisitor.py`, `*.tokens`.
- Modifikasi Nova hanya `CREATE TASK` (NOVA-54 / 9b): `submitTaskStatement` menerima `CREATE`; clause tambahan `AFTER`/`FINALIZE`/`WHEN`/`SCHEDULE`/`OVERLAP_POLICY`; token baru `FINALIZE`, `CRON`, `OVERLAP_POLICY` (semua ditambahkan ke `nonReserved`).
- Guard: `backend/scripts/check_grammar_drift.py` + `test_grammar_drift_check.py`. Pin commit diuji di `test_engine_pin_is_the_4_1_4_commit`.
- `@stage` **tidak** ada di grammar; diparse regex di Python, sehingga token `AT` upstream tak perlu diubah.

**Batasan:** `SUBMIT TASK` (engine) hanya menerima penjadwalan terbatas; `CREATE TASK` Nova dengan cron memperluas permukaan dan **tidak dikirim** ke engine sebagai DDL (lihat `10-...md` dan `README.md` decision log).

---

## 7. Grammar & semantik yang sengaja TIDAK diubah Nova

- Tidak ada rule upstream untuk `@stage`, `CREATE ML_MODEL`, `ML_PREDICT`.
- Tidak ada override `ai_query`.
- Tidak ada perubahan `FILES()`.

---

## Yang belum terverifikasi

- [BELUM TERVERIFIKASI] Ekuivalensi jalur translate `@stage` pada MySQL proxy (4406) vs `QueryService`. Proxy memanggil `parse_sql`, tetapi seluruh rantai translate+inject belum diuji di dokumen ini.
- [BELUM TERVERIFIKASI] Parameter `csv.trim_space` terhadap dokumentasi `FILES()`.
- [BELUM TERVERIFIKASI] Ketersediaan `ai_query()` pada image `starrocks/fe-ubuntu:4.1.4` (hanya dibuktikan di tree git commit pin, bukan probe live).
- [BELUM TERVERIFIKASI] Semua contoh `NOVA_SYSTEM.*` di atas belum dieksekusi terhadap engine hidup dalam tugas ini; validitas grammar/kolom berasal dari DDL `init-nova.sql`.

---

## Keputusan yang dibutuhkan dari team lead

1. Apakah katalog ini disatukan ke `00-index.md` sebagai tabel tunggal, atau dibiarkan sebagai dokumen rujukan terpisah?
2. Perlu tidaknya menjalankan probe live (engine 4.1.4) untuk menaikkan keyakinan item `ai_query` dan `FILES()` dari source-level ke runtime-verified.
3. Apakah `ML_PREDICT` diberi catatan "roadmap" eksplisit (jalur intercept belum tersambung) di `00-index.md` agar tidak diklaim setara `ai_query`.
