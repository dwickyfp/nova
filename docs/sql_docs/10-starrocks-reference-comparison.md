# 10 — Perbandingan Query Nova vs Dokumentasi Resmi StarRocks 4.1.4

> Pemetaan tiap konstruksi SQL Nova ke padanan resmi StarRocks (atau bukti bahwa padanannya tidak ada), dengan sumber, tingkat keyakinan, dan daftar eksplisit yang belum terverifikasi.

- **Engine pin:** StarRocks `4.1.4`, commit `4a9848edf03f5c936dac664b2d52527f48e72eb0` (repo `backend/app/sql_dialect/grammar/`, commit bump NOVA-51 `525f624`).
- **Tanggal akses sumber:** 2026-09-18.
- **Metode:** sumber primer repo `StarRocks/starrocks` pada commit pin di atas + `docs.starrocks.io` (site version `4.1`), plus kode Nova di commit `0c55f14`.
- **Legenda keyakinan:** `Tinggi` = dibuktikan langsung dari source/skema di commit pin; `Sedang` = dari docs versi 4.1 tapi tidak diuji live; `Rendah` = inferensi. `[BELUM TERVERIFIKASI]` = tidak ada bukti memadai.

---

## Ringkasan

- Hampir semua konstruksi **Nova-native** (`@stage`, `CREATE ML_MODEL`, `AI_*`, `ML_PREDICT`, `NOVA_SYSTEM`) adalah **abstraksi Nova di atas StarRocks**, bukan sintaks engine.
- Dua di antaranya punya **padanan resmi StarRocks**: `@stage` → `FILES()` table function (dengan `DESC FILES(...)`), dan `AI_*` → `ai_query()` builtin.
- Dua lainnya **tidak ada di StarRocks 4.1.4**: `CREATE ML_MODEL` (tidak ada DDL model ML di engine) dan `ML_PREDICT` (tidak ada fungsi engine). Keduanya murni Nova.
- `NOVA_SYSTEM` adalah **database biasa milik Nova** (satu database, tabel flat ber-prefix grup), bukan `information_schema`.
- Grammar `StarRocks.g4`/`StarRocksLex.g4` yang di-vendor **byte-identik dengan upstream** di luar blok `NOVA-BEGIN/END`; hanya permukaan `CREATE TASK` (NOVA-54) yang ditambahkan.

---

## Matriks perbandingan

| # | Query / konstruksi Nova | Padanan StarRocks native | Ada di 4.1.4? | Versi docs | Sumber | Keyakinan |
|---|---|---|---|---|---|---|
| 1 | `FROM @stage.path.file.csv` | `FILES('path'=..., 'format'=..., creds...)` table function | Ya | v3.1.0+ (FILES), v3.3+ (csv/orc), v3.4.4+ (avro) | [FILES() docs](https://docs.starrocks.io/docs/sql-reference/sql-functions/table-functions/files.md) | Tinggi |
| 2 | `DESC @stage...` / browse schema file | `DESC FILES(...)` | Ya | v3.3.4+ | [DESC docs](https://docs.starrocks.io/docs/sql-reference/sql-statements/table_bucket_part_index/DESCRIBE.md) | Tinggi |
| 3 | `LIST @stage` | **Tidak ada** `LIST` di engine | Tidak | — | Nova: `parser.py:28-38`, `test_dialect.py:359-371` | Tinggi |
| 4 | `CREATE ML_MODEL ... AS SELECT` | **Tidak ada** DDL model ML | Tidak | — | Nova-only: `ml_model.py`; tidak ada di [SQL statements index](https://docs.starrocks.io/docs/sql-reference/sql-statements/llms.txt) | Tinggi |
| 5 | `AI_COMPLETE/AI_SENTIMENT/AI_CLASSIFY/AI_SUMMARIZE/AI_EXTRACT/AI_TRANSLATE/AI_FILTER` | `ai_query(prompt, config_json)` (builtin); AI_* = SQL UDF wrapper | Ya (`ai_query`) | v4.1 | `gensrc/script/functions.py:1520`; `be/src/exprs/ai_functions.cpp:120` | Tinggi |
| 6 | `ML_PREDICT(alias, features_json)` | **Tidak ada** fungsi engine; padanan `ai_query`/UDF tidak setara | Tidak | — | Nova: `init-nova.sql:778-781`; `ml_intercept.py` tidak dipakai | Tinggi |
| 7 | `NOVA_SYSTEM.CONFIG_STAGES`, `AUDIT_LOG`, `ML_MODELS`, `ML_MODEL_VERSIONS` | **Tidak ada**; schema Nova sendiri, bukan `information_schema` | n/a | — | `docker/init-nova.sql:60-456`; `nova_system.py` | Tinggi |
| 8 | Grammar `StarRocks.g4` vendored | Upstream `fe-grammar/.../StarRocks.g4` + `StarRocksLex.g4` | Ya, byte-identik di luar marker | 4.1.4 | `backend/app/sql_dialect/grammar/upstream/`; `check_grammar_drift.py` | Tinggi |
| 9 | `db.default.table` (editor placeholder) | **Tidak ada**; StarRocks `db.table` (catalog.database.table), tanpa segmen `default` | Tidak | — | Nova: `service.py:67-71` `_DEFAULT_SCHEMA_TABLE_REF` | Tinggi |

---

## Detail per item

### 1. `@stage` vs `FILES()` table function & `DESCRIBE FILE`

**Nova.** `@stage1.data.csv` diklasifikasi oleh `parse_sql` (`parser.py`) sebagai `StageReference`, lalu `translate_stage_query` menulis ulang menjadi `FILES('path'=..., 'format'=...)`, dan `sql_pipeline._inject_files_params` menambahkan parameter CSV/credential per key (`sql_pipeline.py:147-178`). Bentuk yang diterima engine **tidak pernah** memuat `@stage`.

**StarRocks resmi.** `FILES(data_location, data_format, schema_detect, StorageCredentialParams, columns_from_path, list_files_only, list_recursively)` — table function untuk membaca/menulis file remote (HDFS/S3/GCS/S3-compatible/Azure/NFS). Format: parquet, orc (v3.3+), csv (v3.3+), avro (v3.4.4+, load only). Parameter kredensial S3-compatible/MinIO: `aws.s3.access_key`, `aws.s3.secret_key`, `aws.s3.endpoint`, `aws.s3.enable_ssl`, `aws.s3.enable_path_style_access`.

**Yang penting untuk paritas.** Nova memakai `'format'='csv'` dan `'csv.column_separator'` — sesuai docs. Nova **tidak** memakai `'csv.row_delimiter'` di kode saat ini (arch-01 menyebutnya, tetapi `_detect_csv_params` hanya mengisi `column_separator`, `trim_space`, `skip_header`, `enclose`, `escape` — lihat `service.py:1209-1217`). Ini perbedaan antara dokumen arsitektur lama dan kode nyata; kode yang berlaku.

**`DESCRIBE FILE`.** Docs resmi menegaskan abstraksinya `DESC FILES(...)` (v3.3.4+), bukan `DESCRIBE FILE 'path'`. Nova mengarsipkan `DESCRIBE FILE` sebagai `STAGE_DESCRIBE` di `arch-01` tetapi enum `CommandType` aktual (`parser.py:19-25`) hanya punya `STAGE_QUERY/BROWSE/LOAD/EXPORT/REGULAR` — **`STAGE_DESCRIBE` tidak ada di kode**.

**Padanan kanonik** (dokumentasi resmi, contoh):
```sql
DESC FILES(
  "path" = "s3://bucket/lineorder.parquet",
  "format" = "parquet",
  "aws.s3.access_key" = "<placeholder>",
  "aws.s3.secret_key" = "<placeholder>",
  "aws.s3.region" = "us-west-2"
);
```

### 2. `CREATE ML_MODEL` — dikonfirmasi tidak ada di StarRocks

Sumber: `[SQL statements index resmi](https://docs.starrocks.io/docs/sql-reference/sql-statements/llms.txt)` tidak memuat `CREATE ML_MODEL` maupun `CREATE MODEL`. Grammar upstream yang di-vendor (`upstream/StarRocks.g4`, sha256 `48c33cc7…`, dan `upstream/StarRocksLex.g4`, sha256 `9823ca3a…`) **tidak memuat** token/rule `ML_MODEL`/`ML_PREDICT`. Konfirmasi tambahan dari `git show 525f624`.

Nova menanganinya **sebelum** engine: `QueryService.execute` memeriksa `is_create_ml_model(normalized_sql)` lalu merutekan ke `ml_engine_service.train_model` via Python/sklearn (`service.py:234`). Engine tidak pernah melihat statement ini. **Keyakinan: Tinggi.**

### 3. `ai_query()` sebagai basis `AI_*`

Bukti langsung dari commit pin:
- Registrasi: `gensrc/script/functions.py:1520` → `[200000, 'ai_query', True, False, 'VARCHAR', ['VARCHAR', 'JSON'], "AiFunctions::ai_query"]`. Artinya **signature resmi: `ai_query(VARCHAR prompt, JSON config) -> VARCHAR`**.
- Implementasi: `be/src/exprs/ai_functions.cpp:120` — `ai_query(prompt, config)`; config di-parse dari JSON dengan field: `model` (wajib), `api_key` (wajib), `endpoint`, `temperature` (0–2), `max_tokens` (>0), `top_p` (0–1), `timeout_ms` (>0). API key boleh `env.<VAR>` untuk dibaca dari environment.

**Nova.** `AI_COMPLETE` dll. adalah **SQL UDF** (`CREATE GLOBAL FUNCTION ... RETURNS ai_query(CONCAT(...), '{config}')`) yang didaftarkan `llm_functions/service.py` (template di `UDF_TEMPLATES`, baris 32-75). Config JSON dibangun di `_register_single_udf` (`service.py:456-490`): `model`, `api_key` (di-dekripsi), `endpoint`. Tempat pendaftarannya ada di `init-nova.sql:743-781` (placeholder) dan di-overwrite backend saat startup.

**Catatan penting (akurasi):** kode Nova menyebut field `endpoint` (bukan `endpoint_url`) dan menambahkan path `/chat/completions`. File `workspace/ai_functions_samples.sql` masih memakai `"endpoint_url"` di komentar contoh — itu **tidak sesuai** dengan config `ai_query()` yang sebenarnya (`endpoint`) maupun kode Nova (`config["endpoint"]`). Ini bagian "belum sinkron" yang perlu ditandai, bukan dijadikan acuan.

### 4. `ML_PREDICT` vs `ai_query`/UDF

**StarRocks resmi:** tidak ada `ML_PREDICT` di `functions.py` (grep pada file commit pin) maupun di index docs. Tidak ada padanan langsung.

**Nova:** `ML_PREDICT` didefinisikan sebagai **UDF placeholder** (`init-nova.sql:778-781`) yang hanya mengembalikan string instruksi API. Ada file `backend/app/common/ml_intercept.py` dengan `detect_ml_predict()`/`rewrite_ml_predict_sql()`, **tetapi tidak ada satu pun import terhadap modul itu** di tree `backend/app` (hanya definisi di file itu sendiri). Artinya jalur "intercept `ml_predict()` di SQL" **belum terhubung**; prediksi nyata berjalan lewat REST `POST /api/v1/ml/predict` dan `/predict/batch` (`ml_engine/router.py:69,84`).

**Implikasi paritas:** `ML_PREDICT` belum setara `ai_query` (yang benar-benar dipanggil engine). Status: **permukaan UDF saja, bukan eksekusi in-engine**. Keyakinan: Tinggi (dari ketiadaan import + definisi UDF).

### 5. `NOVA_SYSTEM` vs `information_schema`

`NOVA_SYSTEM` adalah database **milik Nova**, dibuat `docker/init-nova.sql:50`, dengan konvensi `<group>_<table>` (tanpa schema bertingkat; StarRocks hanya punya `catalog.database.table`). Contoh: `CONFIG_STAGES`, `CONFIG_TASK*`, `CONFIG_MODEL_ALIASES`, `ML_MODELS`, `ML_MODEL_VERSIONS`, `ML_MODEL_ALIASES`, `AUDIT_LOG`, `STAGE_FILE_MANIFEST`, `LINEAGE_LOAD_HISTORY`, `QUALITY_TABLE_STATS`, `USAGE_QUERY_STATS`.

**Padanan resmi:** tidak ada — ini schema aplikasi, bukan `information_schema` (yang di StarRocks adalah database read-only berisi metadata katalog). Nova **memang membaca** `information_schema` untuk hal lain: `information_schema.columns` (`nova_system.py:133`), `information_schema.tasks`/`task_runs` (`tasks/service.py:111,216`), `information_schema.applicable_roles` (`auth/service.py:51`), `information_schema.tables_config`/`tables`/`views` (`explorer/repository.py`).

**Invariant:** tabel `NOVA_SYSTEM` **tidak boleh** memuat kredensial. `init-nova.sql` dan `nova_system.py` menyatakan ini; redaksi saat keluar proses dijaga `sql_guard.redact_sql_credentials`.

### 6. Grammar `StarRocks.g4` yang di-vendor

- Lokasi: `backend/app/sql_dialect/grammar/StarRocks.g4` (+ `StarRocksLex.g4`), generated Python (`StarRocksParser.py`, dll.) di-commit.
- Provenance header tiap `.g4`: `tag: 4.1.4`, `commit: 4a9848edf03f5c936dac664b2d52527f48e72eb0`.
- **Upstream tersedia** di `backend/app/sql_dialect/grammar/upstream/StarRocks.g4` dan `upstream/StarRocksLex.g4` (jawaban atas pertanyaan lead: **ya, ada**).
- Diverifikasi mandiri: sha256 upstream `StarRocks.g4` = `48c33cc741e82f838ebbad09b83ffa8011e690368cbfa2112ecf5d0c810abe3b` (cocok dengan header), `StarRocksLex.g4` = `9823ca3a363cab28f2feb4d41cd2aa64dd711a75693e6bef43405fc434666456` (cocok).
- Guard: `backend/scripts/check_grammar_drift.py` — (1) pin commit + sha256 header vs upstream, (2) "marked-changes-only": di luar `NOVA-BEGIN/END`, file harus byte-identik upstream (hanya boleh *deletion*, tidak boleh insert/replace). Test semantik di `test_grammar_drift_check.py`.
- **Modifikasi Nova pada grammar upstream** (dari `check_grammar_drift.py`, 2026-09-18): `StarRocks.g4` 6 region (717-724, 736-746, 757-803, 3385-3389, 3393-3395, 3406-3408) — semuanya permukaan `CREATE TASK` (NOVA-54 / 9b). `StarRocksLex.g4` 5 region (32-47, 138-142, 216-219, 281-284, 581-588) — token `FINALIZE`/`CRON`/`OVERLAP_POLICY` (NOVA-54) plus blok `@members`/action Java→Python (NOVA-17). Tidak ada perubahan terkait `@stage`, `ML_MODEL`, atau AI.
- `@stage` diparse **di luar** grammar ANTLR (regex `_AT_TOKEN` di `parser.py`), sehingga `@` tetap token upstream biasa (`userVariable: AT identifierOrString`) dan Nova tidak perlu memodifikasi grammar untuknya.

---

## Yang secara eksplisit BELUM TERVERIFIKASI

- [BELUM TERVERIFIKASI] Perilaku runtime `ai_query()` (latensi, caching LRU, perilaku timeout) di stack Nova — docs publik `docs.starrocks.io` **tidak memuat halaman `ai_query`** saat akses 2026-09-18 (dicari via `sitemap.xml` dan index `llms.txt`; hasil 404). Klaim fungsional di atas berasal dari **source code**, bukan docs.
- [BELUM TERVERIFIKASI] Apakah `ai_query()` hadir pada image `starrocks/fe-ubuntu:4.1.4` yang dipin Nova (bukan hanya di tree git commit). Tidak ada probe live yang dijalankan dalam tugas ini.
- [BELUM TERVERIFIKASI] Semantik `csv.trim_space` yang dipakai `_detect_csv_params` — tidak ditemukan padanannya di daftar parameter CSV pada halaman `FILES()` yang diakses. Perlu dikonfirmasi ke docs/engine.
- [BELUM TERVERIFIKASI] Apakah `agent`/proxy path (port 4406) benar-benar melakukan rewrite `@stage` yang identik dengan `QueryService`; sesi proxy memang memanggil `parse_sql` (`proxy/session.py`), tetapi ekuivalensi penuh jalur translation belum diuji di sini.
- [BELUM TERVERIFIKASI] Nomor versi StarRocks di mana `ai_query` pertama muncul. Tidak ada release note yang dibaca untuk ini.
- [BELUM TERVERIFIKASI] Sintaks & perilaku opsi `COPY INTO` (load/export) di 4.1.4 — tidak ada halaman/source yang diperiksa untuk ini di tugas ini. Dipindahkan dari daftar terbuka `08-native-starrocks-sql.md`.
- [BELUM TERVERIFIKASI] Semantik `PRIMARY KEY` / `DUPLICATE KEY` / partitioning / `PROPERTIES` di 4.1.4 — tidak diperiksa di tugas ini. Dipindahkan dari daftar terbuka `08-native-starrocks-sql.md`.
- [BELUM TERVERIFIKASI] Bentuk output `EXPLAIN` (kolom/format) di 4.1.4 — tidak diperiksa di tugas ini. Dipindahkan dari daftar terbuka `08-native-starrocks-sql.md`.

---

## Bukti verifikasi yang dijalankan

Semua klaim source-level di atas dan di `11-query-catalog.md` diverifikasi pada commit `0c55f14` dengan perintah berikut (venv terisolasi, Python 3.12, `antlr4-python3-runtime==4.13.2`):

```
$ python backend/scripts/check_grammar_drift.py
OK  StarRocks.g4: pin 4a9848edf03f5c936dac664b2d52527f48e72eb0 (6 marked region(s): 717-724, 736-746, 757-803, 3385-3389, 3393-3395, 3406-3408)
OK  StarRocksLex.g4: pin 4a9848edf03f5c936dac664b2d52527f48e72eb0 (5 marked region(s): 32-47, 138-142, 216-219, 281-284, 581-588)
Grammar drift check passed (2 file(s)).

$ python -m pytest tests/unit/test_dialect.py tests/unit/test_grammar_drift_check.py \
    tests/unit/test_sql_pipeline_files_params.py tests/unit/test_ml_model_ddl.py \
    tests/unit/test_sql_dialect_grammar.py -q
192 passed

$ python -m pytest tests/unit/test_sql_guard.py tests/unit/test_sql_guard_bypass.py \
    tests/unit/test_sql_guard_revoke_hardening.py tests/unit/test_ml_engine_training_sql.py \
    tests/unit/test_ml_engine_batch_predict.py tests/unit/test_credential_leaks.py -q
201 passed
```

sha256 upstream diverifikasi ulang dari repo GitHub pada commit pin:
`StarRocks.g4 = 48c33cc741e82f838ebbad09b83ffa8011e690368cbfa2112ecf5d0c810abe3b`,
`StarRocksLex.g4 = 9823ca3a363cab28f2feb4d41cd2aa64dd711a75693e6bef43405fc434666456`.

---

## Keputusan yang dibutuhkan dari team lead

1. **Status docs `ai_query`:** karena docs publik tidak punya halaman `ai_query`, apakah catatan sumber-kode di `11-query-catalog.md` cukup sebagai rujukan, atau perlu probe live ke engine 4.1.4 untuk mengangkat keyakinan ke Tinggi?
2. **`ML_PREDICT`:** apakah perlu diimplementasikan jalur intercept (`ml_intercept.py` saat ini mati) agar klaim "ML via SQL" benar-benar in-engine, atau cukup dinyatakan sebagai UDF placeholder + REST?
3. **Sinkronisasi contoh:** contoh di `workspace/ai_functions_samples.sql` memakai `endpoint_url`; apakah tim akan memperbaiki contoh itu di PR dokumentasi ini (di luar scope `docs/sql_docs/`) atau meninggalkannya dengan catatan?
