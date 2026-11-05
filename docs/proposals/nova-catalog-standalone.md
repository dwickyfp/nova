# Proposal: Nova Catalog sebagai catalog mandiri (federated)

> **Status:** Fase 1 diimplementasikan (lihat "Status Fase 1" di §6). Fase 2 ke
> atas masih proposal dan non-normatif sampai diimplementasikan dan lolos tes.
> **Tanggal riset:** 2026-11-05. Engine pin: StarRocks `4.1.4` (`4a9848ed`).
> **Tujuan:** Nova Catalog berdiri sendiri seperti Unity Catalog, sehingga
> database lain (PostgreSQL, MySQL, Snowflake, dan seterusnya) bisa didaftarkan,
> dijelajahi, diberi metadata bisnis, dan di-govern di satu tempat.

Penanda keyakinan: **[kode]** sudah dicek di repo ini, **[docs]** dari dokumentasi
resmi upstream, **[belum]** belum terverifikasi dan harus di-spike.

---

## 1. Ringkasan

- Hari ini "Nova Catalog" hanyalah label UI untuk `default_catalog` StarRocks.
  Tidak ada catalog milik Nova: setiap halaman Explorer membaca `SHOW` /
  `information_schema` langsung dari engine. Tidak ada cache, indeks pencarian,
  maupun registry koneksi. [kode]
- Federasi yang ada terbatas pada StarRocks external catalog tipe Hive dan
  Iceberg. JDBC (Postgres/MySQL) didukung engine tetapi tidak dibuka di Nova.
  Snowflake tidak punya jalur sama sekali. [kode]
- Rekomendasi: **bangun Nova Catalog sebagai modul catalog sendiri di atas
  `NOVA_SYSTEM` dan Ranger**, dengan *connector SPI*. Jangan menanam Gravitino,
  Polaris, atau Unity Catalog sebagai catalog of record, karena ketiganya membawa
  store metadata dan engine otorisasi kedua, dua hal yang dilarang `AGENTS.md`.
  Pinjam model objeknya (Connection → Foreign Catalog → Schema → Table) dan pola
  sinkronisasinya.
- Setiap sumber eksternal diwujudkan sebagai StarRocks external catalog: JDBC
  untuk Postgres/MySQL/Oracle/SQL Server, JDBC dengan patch FE untuk Snowflake
  (§6.2), dan Iceberg REST untuk Polaris/UC/Snowflake Horizon. Query, semantic
  view, dan agen memakai jalur SQL Nova yang sudah ada, dan Ranger tetap
  satu-satunya penegak. Nova Catalog menambahkan metadata yang dipanen (tag,
  komentar, glossary, lineage, pencarian) di `NOVA_SYSTEM`, tapi visibilitasnya
  selalu mengikuti apa yang boleh dilihat caller di engine.
- Fase 0 sudah dijalankan (§6.1, §6.2). Row filter dan masking Ranger
  **berlaku** pada tabel JDBC (Postgres) dan Iceberg di FE yang di-patch.
  Snowflake butuh patch FE kecil karena JDBC catalog 4.1.4 hanya mengenal enam
  driver. Mode metadata-only tanpa catalog engine dihapus dari desain, karena
  mengotorisasinya butuh evaluator policy kedua.
- Explorer kini membaca metadata sebagai caller, sehingga Ranger menyaring apa
  yang dilihat setiap pengguna (langkah pertama Fase 1).

---

## 2. Kondisi sekarang (bukti dari kode)

| Area | Fakta | Lokasi |
|---|---|---|
| Explorer | Membaca metadata live lewat **admin system pool**, bukan koneksi caller. Tabel ditampilkan dengan visibilitas root; hanya stage yang difilter per user. | `backend/app/modules/explorer/repository.py:24`, `service.py:44-97` |
| Label "Nova Catalog" | Hanya nama tampilan untuk catalog internal. | `frontend/src/features/database-explorer/index.tsx:536` |
| External catalogs | Enum hanya `HIVE` dan `ICEBERG`; metastore `HMS`/`REST`. DDL dijalankan sebagai caller. | `backend/app/modules/external_catalogs/schemas.py:20-31`, `service.py:124-175` |
| Mirror catalog | `CONFIG_EXTERNAL_CATALOGS` dibuat saat runtime; catalog yang dibuat lewat SQL mentah tidak tercatat di mirror. | `external_catalogs/repository.py:15-30`, `service.py:340` |
| Kredensial | Koneksi storage ada di `nova.yaml` (`${ENV}` / `secret_ref`). `SecretProvider` (AWS SM) reference-only, fail-closed. Fernet hanya untuk API key provider AI dan password sesi. | `core/config.py:319-488`, `storage/secrets.py:87-354`, `common/crypto.py` |
| Abstraksi metadata | `CatalogProvider` Protocol di binder SQL, satu implementasi (`StarRocksCatalogProvider`). Cache per statement. | `sql_frontend/binding/catalog.py:8-20`, `binding/starrocks.py:23-80` |
| Nama 3 bagian | Binder sudah memetakan `catalog.db.table`. | `sql_frontend/binding/relations.py:22-31` |
| GRANT/REVOKE SQL | **Di-hardcode ke `default_catalog`.** | `access_control/statement_router.py:108,123`, `sql_frontend/security.py:130` |
| Ranger | Resource `catalog/database/table/column`, satu service; bootstrap hanya untuk `default_catalog`. `effective_access` membaca daftar policy, bukan evaluator Ranger penuh. | `integrations/ranger/compiler.py:31-80`, `access_control/service.py:596-626`, `docker/ranger/bootstrap.py:227-320` |
| Metadata bisnis | `CONFIG_ENTITIES` dan semantic view sudah dikunci per `catalog/database/schema/name`. `CONFIG_OBJECT_TAGS` ada di schema tapi tidak dipakai kode mana pun. Tidak ada tabel glossary, klasifikasi, atau komentar kolom. | `docker/init-nova.sql:179,237`, `intelligence/semantic_views.py:52,213-225` |
| Koneksi ke sistem lain | Migration connector hanya StarRocks→StarRocks via protokol MySQL, berjalan di `nova-worker`, password lewat `secret_ref`. | `modules/migration/source.py:40-115` |

Utang dokumentasi yang ketemu di jalan: `docs/10-external-catalogs.md:157-161`
masih menyebut RBAC StarRocks-native (sebelum Ranger), dan
`docs/arch-02-storage-provider-layer.md` menjelaskan `StorageProvider` ABC yang
tidak ada di kode.

---

## 3. Riset: bagaimana catalog lain melakukannya

| | Unity Catalog OSS | Apache Polaris | Apache Gravitino | DataHub / OpenMetadata |
|---|---|---|---|---|
| Model | catalog.schema.{table, volume, function, model, view} | catalog → namespace → Iceberg/generic table | metalake.catalog.schema.{table, fileset, topic, model} | graf entitas |
| API | UC REST, UC Delta, Iceberg REST (tulis sejak 0.7) | Iceberg REST + management API | REST sendiri, Iceberg REST server, MCP | GraphQL/REST + ingestion |
| Federasi Postgres/Snowflake | Hanya di Databricks (Connection → Foreign Catalog). Belum ada di OSS **[belum]** | Tidak (hanya HMS, Glue, BigQuery Metastore, IRC lain) | JDBC: MySQL, PG, Doris, StarRocks, ClickHouse, dll. Snowflake **tidak** | Harvest saja, ±80 konektor termasuk Snowflake |
| Otorisasi | Grant UC, tanpa RLS/masking | RBAC + OPA, tanpa RLS | RBAC sendiri, pushdown ke Ranger/grant native | Advisory |
| Kematangan | 0.7.x, API masih bergerak | ASF TLP (2026), 1.6 | ASF TLP, 1.3.1 | Matang |
| Konektor StarRocks | via Iceberg REST | via Iceberg REST | via Iceberg REST; plugin authz StarRocks masih PR | n/a |

Pola yang relevan:

1. **Connection + Foreign Catalog (Databricks UC).** Koneksi menyimpan endpoint
   dan referensi kredensial; foreign catalog menunjuk ke koneksi. Metadata
   disegarkan malas saat query, plus `REFRESH FOREIGN CATALOG|SCHEMA|TABLE`
   manual atau terjadwal. Grant atas objek foreign adalah grant catalog. [docs]
2. **Catalog of catalogs (Gravitino).** Setiap catalog didukung konektor ke
   sistem asli; DDL lewat catalog diteruskan ke sumber. [docs]
3. **Harvest (DataHub/OpenMetadata).** Crawl `information_schema`, query log, dan
   statistik secara terjadwal. Cocok untuk discovery dan lineage, tapi tidak
   menegakkan policy. [docs]
4. **Iceberg REST sebagai lapisan interop.** Data di format terbuka, catalog
   mem-vend kredensial sementara, engine membaca file langsung. Hanya untuk
   tabel lakehouse, bukan database operasional. [docs]

Fakta StarRocks yang membatasi desain:

- JDBC catalog: MySQL, PostgreSQL, Oracle, SQL Server, ClickHouse (eksperimental).
  Masih berlabel **Beta**. Tidak ada Snowflake/BigQuery. [docs]
- Pushdown JDBC: filter perbandingan sederhana didorong ke sumber; agregasi,
  join, `ORDER BY ... LIMIT`, dan fungsi pada kolom tidak. Query agregat atas
  tabel Postgres besar akan full scan. Terukur dengan `EXPLAIN` di 4.1.4 (§6.1).
- Snowflake hanya bisa dibaca StarRocks lewat tabel Iceberg yang dikelola
  Snowflake dan diekspos Horizon/Open Catalog (Iceberg REST, GA Feb 2026), atau
  lewat ETL. [docs]
- Service `ranger-starrocks` mencakup resource catalog/database/table untuk
  external catalog. Dokumentasi upstream tidak menyebut apakah row filter dan
  masking berlaku untuk tabel JDBC/Iceberg; spike di FE yang di-patch
  menunjukkan berlaku (§6.1).
- Iceberg REST dengan `iceberg.catalog.security=jwt` butuh user StarRocks yang
  diautentikasi JWT; belum tentu cocok dengan sesi password Nova. **[belum]**

Sumber utama: dokumentasi StarRocks (catalog overview, JDBC catalog, Iceberg REST
security, Ranger plugin), Databricks query/catalog federation dan
`REFRESH FOREIGN`, rilis Unity Catalog, Polaris, dan Gravitino di GitHub,
pengumuman graduasi Polaris di ASF, release note Snowflake Horizon 2026-02-06.

---

## 4. Opsi arsitektur

| Opsi | Inti | Kelebihan | Kendala utama | Verdict |
|---|---|---|---|---|
| **A. Tanam Gravitino** sebagai catalog of record | Nova memanggil Gravitino; StarRocks lewat Iceberg REST | Konektor JDBC sudah ada, model "catalog of catalogs" | Store metadata kedua (JVM + DB sendiri), RBAC kedua, tanpa konektor StarRocks native, tanpa Snowflake. Melanggar invariant `NOVA_SYSTEM` dan Ranger | Tolak sebagai pusat; boleh jadi *sumber* yang di-harvest |
| **B. Tanam Unity Catalog OSS / Polaris** | Catalog Iceberg/Delta, StarRocks via REST | Standar terbuka, credential vending | Tidak menyelesaikan Postgres/Snowflake; authz kedua tanpa RLS | Tolak sebagai pusat; dukung sebagai *sumber* Iceberg REST |
| **C. Harvest saja** (gaya DataHub) | Nova memanen metadata dari semua sumber | Mendukung semua sumber, termasuk Snowflake | Tidak bisa di-query; policy hanya advisory | Bagian dari solusi, bukan seluruhnya |
| **D. Nova Catalog native + connector SPI** (rekomendasi) | Catalog of record di `NOVA_SYSTEM`; tiap sumber punya konektor dan diwujudkan sebagai StarRocks external catalog | Satu store, satu authz (Ranger), satu jalur SQL; Snowflake masuk bertahap | Butuh kerja konektor dan sinkronisasi sendiri; kualitas pushdown bergantung StarRocks | **Rekomendasi** |

"Mandiri" di opsi D berarti: Nova Catalog punya model objek, API, dan siklus
hidup sendiri, tidak lagi sekadar cermin `SHOW CATALOGS`. Ia tetap memakai
`NOVA_SYSTEM`, Redis, `nova-worker`, `SecretProvider`, dan Ranger yang sudah ada.
Memisahkannya menjadi proses `nova-catalog` tersendiri adalah keputusan deployment
yang bisa diambil belakangan tanpa mengubah model (lihat §7, keputusan 1).

---

## 5. Desain yang diusulkan (opsi D)

### 5.1 Model objek

```
Connection  (endpoint + secret_ref, tanpa nilai rahasia)
  └── Catalog  kind = native | foreign_queryable | foreign_metadata
        └── Schema (database)
              ├── Table / View / Materialized View
              │     └── Column
              ├── Function, Stage, Semantic View, Entity   (objek Nova yang sudah ada)
```

- `native`: `default_catalog` StarRocks.
- `foreign_queryable`: punya pasangan StarRocks external catalog dengan nama
  yang sama. Query `catalog.schema.table` berjalan lewat jalur SQL Nova biasa.
- `foreign_metadata`: status sementara saat koneksi sudah terdaftar tapi catalog
  engine belum dibuat. Objeknya tidak ditampilkan dan query terhadapnya ditolak
  dengan pesan jelas oleh `sql_frontend` (fail-closed, §6.2).

Setiap objek punya **securable id** stabil dan nama terkualifikasi penuh, supaya
tag, komentar, owner, semantic view, dan lineage menempel ke id, bukan ke string
nama yang bisa berubah.

### 5.2 Penyimpanan di `NOVA_SYSTEM`

Nama datar mengikuti gaya `init-nova.sql`. Draf (dicek ulang terhadap
`docker/init-nova.sql` saat implementasi):

| Tabel | Isi |
|---|---|
| `CATALOG_CONNECTIONS` | id, name, connector_type (`postgres`, `mysql`, `snowflake`, `iceberg_rest`, `hive`, ...), endpoint metadata, `secret_ref`, owner, comment, created/updated |
| `CATALOG_CATALOGS` | id, name, kind, connection_id, engine_catalog_name, sync_policy, last_sync_at, status. Menggantikan `CONFIG_EXTERNAL_CATALOGS` (migrasi data) |
| `CATALOG_OBJECTS` | securable id, catalog_id, schema_name, object_name, object_type, source_fingerprint, comment, owner, sumber metadata (engine/harvest), first_seen, last_seen, dropped_at |
| `CATALOG_COLUMNS` | object_id, ordinal, name, source_type, nova_type, nullable, comment |
| `CATALOG_SYNC_RUNS` | run id, catalog_id, scope, started/finished, counts, error (diredaksi) |
| `CONFIG_OBJECT_TAGS` | dipakai sungguhan, dikunci ke securable id |

Tabel memakai primary-key table StarRocks agar upsert hasil harvest murah.
Redis memegang lock sinkronisasi dan cache baca; bukan sumber kebenaran.

### 5.3 Connector SPI

Satu Protocol di modul catalog, implementasi per sumber:

```python
class CatalogConnector(Protocol):
    connector_type: str
    capabilities: ConnectorCapabilities  # queryable, harvest, lineage, usage, ...

    async def test(self, conn: ResolvedConnection) -> ConnectionCheck: ...
    async def list_schemas(self, conn) -> list[SchemaInfo]: ...
    async def list_objects(self, conn, schema: str) -> list[ObjectInfo]: ...
    async def describe(self, conn, schema: str, name: str) -> ObjectDetail: ...
    # hanya untuk foreign_queryable:
    def engine_catalog_properties(self, conn) -> EngineCatalogSpec: ...
```

- `ResolvedConnection` hanya hidup di worker saat eksekusi; kredensial
  di-resolve lewat `SecretProvider` setelah otorisasi, tidak pernah ditulis ke
  plan, log, atau respons.
- `engine_catalog_properties` menghasilkan spesifikasi
  `CREATE EXTERNAL CATALOG` (JDBC/Iceberg/Hive). Builder DDL di
  `external_catalogs/service.py:124-175` dipindahkan ke sini.
- `StarRocksCatalogProvider` di binder tetap sumber kebenaran untuk binding
  query; catalog store tidak menggantikan validasi engine saat eksekusi.

Konektor awal: `starrocks_native`, `iceberg_rest`, `hive`, `postgres`, `mysql`,
lalu `snowflake` (metadata), `sqlserver`, `oracle`. `gravitino` dan
`unity_catalog` bisa jadi konektor harvest kalau pelanggan sudah punya.

### 5.4 Sinkronisasi metadata

- **Native dan queryable**: sumbernya engine. Worker membaca
  `catalog.information_schema` / `SHOW` dan meng-upsert `CATALOG_OBJECTS`.
- **Metadata tambahan dari sumber**: konektor boleh membaca komentar, tag, dan
  statistik yang tidak diekspos engine (misalnya `ACCOUNT_USAGE` Snowflake)
  dengan kredensial koneksi. Hasilnya menempel ke objek yang sudah ada di engine
  dan hanya tampil kepada caller yang bisa melihat objek itu di engine.
- Pemicu: saat pertama dibuka (lazy, dengan TTL), terjadwal per catalog, dan
  manual lewat `REFRESH CATALOG` / tombol Refresh. Job berjalan di
  `nova-worker` dengan lock Redis, polanya sama dengan migration jobs.
- Objek yang hilang di sumber ditandai `dropped_at`, tidak dihapus, supaya tag
  dan lineage tidak ikut hilang tanpa jejak.
- Catalog yang dibuat dengan `CREATE EXTERNAL CATALOG` mentah ikut terdeteksi
  (rekonsiliasi `SHOW CATALOGS`), menutup celah mirror saat ini.

### 5.5 Otorisasi

Dua bidang, satu engine policy (Ranger):

1. **Data plane (query).** Untuk catalog native dan queryable, Ranger di FE yang
   di-patch tetap penegak tunggal: catalog, database, table, column, row filter,
   masking. Tidak berubah.
2. **Metadata plane (browse/search).** Daftar objek harus difilter per caller,
   bukan root:
   - Native/queryable: daftar dari `CATALOG_OBJECTS` dipotong dengan hasil
     yang dilihat caller sendiri (query `information_schema` sebagai caller,
     yang sudah ditegakkan Ranger), atau evaluasi Ranger yang setara.
   - Tidak ada objek tanpa pasangan di engine (§6.2, S3), jadi tidak ada
     jalur otorisasi metadata selain engine. Tidak boleh ada tabel izin baru di
     `NOVA_SYSTEM`.
- `GRANT`/`REVOKE` SQL harus menerima catalog (hapus hardcode
  `default_catalog`). Bootstrap Ranger tidak boleh membuka catalog baru ke semua
  role secara default.
- Proteksi `ACCOUNTADMIN`, satu active role, dan versi security context tetap
  dibawa ke semua jalur, termasuk cache metadata (kunci cache memuat role dan
  versi context).
- Semua operasi catalog (buat koneksi, buat catalog, refresh, ubah tag) masuk
  `AUDIT_LOG` dengan redaksi, termasuk yang ditolak.

### 5.6 Permukaan pengguna

- **SQL** lewat `backend/app/sql_frontend/` (bukan deteksi ad-hoc):
  - `CREATE CONNECTION name TYPE = postgres ENDPOINT = '...' SECRET = '<ref>'`
  - `CREATE FOREIGN CATALOG name USING CONNECTION conn [OPTIONS (...)] [METADATA ONLY]`
  - `REFRESH CATALOG name [SCHEMA s [TABLE t]]` (sintaks Nova; engine 4.1.4
    tidak punya `REFRESH CATALOG`, jadi ini diterjemahkan ke sinkronisasi Nova
    dan `REFRESH EXTERNAL TABLE`)
  - `SHOW CONNECTIONS`, `DESCRIBE CONNECTION`
  Sintaks final mengikuti aturan grammar di `backend/app/sql_frontend/AGENTS.md`.
- **REST**: `/api/v1/catalog/connections`, `/catalogs`, `/objects`, `/search`,
  `/objects/{id}/tags`. `/api/v1/external-catalogs` dipertahankan sementara
  sebagai alias, lalu dihapus setelah frontend pindah.
- **UI**: Database Explorer membaca dari catalog store (cepat, bisa dicari);
  halaman Connections menggantikan form External Catalogs. Pengguna memilih
  nama koneksi; endpoint fisik dan kredensial tidak tampil.
- **Agen/Studio**: tool metadata assistant membaca catalog store yang sudah
  difilter per caller, sehingga agen bisa menemukan tabel Postgres atau
  Snowflake tanpa query live ke sumber.

### 5.7 Kredensial

- `CATALOG_CONNECTIONS` hanya menyimpan `secret_ref`. Resolver memakai
  `SecretProvider` yang sudah ada. Kalau perlu entri kredensial dari UI, simpan
  terenkripsi (Fernet, pola provider AI) dan pisahkan alur entri dari readback.
- StarRocks JDBC catalog menyimpan `password` di properti catalog FE. Redaksi
  `SHOW CREATE CATALOG` di `external_catalogs/service.py:317-337` harus tetap
  dipakai; audit hanya menyimpan statement yang diredaksi.
- JAR driver JDBC (Postgres, MySQL) dibundel di image FE, bukan diunduh dari
  internet saat `CREATE CATALOG`.

---

## 6. Rencana bertahap

Setiap fase punya kriteria keluar yang bisa dites. Tidak ada feature flag:
fitur aktif begitu dirilis, hanya bergantung pada prasyarat nyata (koneksi,
otorisasi).

### Fase 0: Spike yang menentukan arah (±1–2 minggu)

| # | Pertanyaan | Cara uji | Jika gagal |
|---|---|---|---|
| S1 | Apakah row filter dan masking Ranger berlaku pada tabel JDBC (Postgres) dan Iceberg di FE 4.1.4 yang di-patch? | Policy acceptance di stack `docker` yang ada, role non-root | Sumber queryable dibatasi ke data tanpa RLS/masking, atau perlu patch FE |
| S2 | Seberapa jauh pushdown JDBC? | `EXPLAIN` filter, agregat, join, `LIMIT` terhadap Postgres | Tulis batas performa di docs; pertimbangkan MV untuk agregat |
| S3 | Bagaimana mengevaluasi Ranger untuk objek metadata-only? | Uji evaluator Ranger (plugin Java sidecar vs catalog kosong di engine) | Mode metadata-only hanya untuk ACCOUNTADMIN sampai terjawab |
| S4 | Snowflake: Iceberg via Horizon bisa dibaca StarRocks 4.1.4? Driver JDBC Snowflake generik? | Akun Snowflake trial, Iceberg REST catalog, `EXPLAIN` | Snowflake tetap metadata-only |

Tambahan kecil: verifikasi bahwa JDBC catalog bisa dibuat dengan JAR lokal di
image FE tanpa akses internet.

### 6.1 Hasil spike Fase 0 (2026-11-05)

Dijalankan di stack compose `docker` yang sudah berjalan (FE `4.1.4-4a9848e`
dengan patch Ranger, service `nova_starrocks`). Sumber uji: container Postgres
16 sekali pakai di jaringan `docker_nova` berisi `orders` dan `customers`
(30.000 baris). Iceberg: catalog `hadoop` dengan warehouse di bucket MinIO
`temp`. Role uji `spike_analyst` mendapat policy Ranger: select pada `orders`,
row filter `region = 'APAC'`, dan mask pada `customer_email`. Semua objek uji
sudah dihapus.

**S1, Ranger pada tabel external: lolos.**

| Cek | JDBC (Postgres) | Iceberg |
|---|---|---|
| Row filter `region = 'APAC'` | Hanya 10.000 baris APAC; `WHERE region='EMEA'` → 0 | Sama |
| Mask `MASK` pada email | `user3@ex.com` → `xxxx0@xx.xxx` | Sama |
| Tabel tanpa policy (`customers`) | Ditolak (`Access denied ... SELECT ... on COLUMN`) | n/a |
| `INSERT` tanpa grant | n/a | Ditolak |
| `SHOW TABLES` / `information_schema.tables` sebagai caller | Hanya `orders` | n/a |
| Role tanpa policy apa pun | `SHOW CATALOGS` hanya `default_catalog`; query ditolak | Catalog tidak terlihat |
| Letak row filter | Ikut didorong ke SQL Postgres (`WHERE ("amount" > 900E0) AND ("region" = 'APAC')`) | Jadi predikat scan dan min/max |
| Letak mask | Dihitung di StarRocks setelah data diambil | Sama |
| `SHOW CREATE CATALOG` | Password JDBC tidak muncul | n/a |

Implikasi: penyaringan Explorer per caller bisa memakai metadata engine yang
di-query sebagai caller, untuk catalog native dan queryable (§5.5 butir 2).
Grant tingkat tabel tanpa policy `usage` pada catalog sudah cukup untuk melihat
dan meng-query catalog itu. Bootstrap dan compiler Nova tidak perlu membuat
policy usage catalog terpisah. Mask berjalan di StarRocks, jadi nilai asli
tetap mengalir dari sumber ke BE. Kredensial koneksi sebaiknya akun read-only
dengan hak sesempit mungkin.

**S2, pushdown JDBC: terukur.**

| Query | SQL yang dikirim ke Postgres |
|---|---|
| `WHERE region='APAC' AND amount > 500 LIMIT 10` | Kedua filter didorong; limit dipakai di scan node |
| `GROUP BY region, sum(amount)` | `SELECT "region", "amount" FROM "public"."orders"` (tanpa agregasi) |
| `WHERE upper(region)='APAC' AND created >= ...` | Hanya filter `created`; `upper()` dievaluasi di StarRocks |
| Join `orders` × `customers` | Dua scan terpisah; join di StarRocks |
| `ORDER BY amount DESC LIMIT 5` | Full scan; TOP-N di StarRocks |

**Temuan lain dari spike (di luar scope catalog):**

- **Mask `MASK_HASH` mematahkan query**, di tabel internal maupun external.
  Transformer servicedef `starrocks` di Ranger 2.9.0 memakai `{COL}` huruf
  besar, yang tidak disubstitusi: `No viable statement for input
  'cast((hex(sha2(from_binary(to_binary({COL'`. Kegagalannya tertutup (query
  error, data tidak bocor), tapi policy jadi tidak bisa dipakai. UI Nova
  menawarkan `MASK_HASH` (`frontend/src/features/access-control/index.tsx:96`).
- **UI Nova menawarkan `MASK_SHOW_LAST_4` dan `MASK_SHOW_FIRST_4`**, padahal
  keduanya tidak ada di servicedef `starrocks`; Ranger menolaknya dengan HTTP 400.
  Tipe yang didukung: `MASK`, `MASK_HASH`, `MASK_NULL`, `MASK_NONE`,
  `MASK_DATE_SHOW_YEAR`, `CUSTOM`.
- Kedua temuan mask di atas sudah diperbaiki: bootstrap Ranger mengoreksi
  placeholder `{COL}`, API menolak tipe mask di luar servicedef, dan UI hanya
  menawarkan `MASK`, `MASK_HASH`, `MASK_NULL` (lihat
  `docs/29-ranger-access-control.md`).
- **`REFRESH CATALOG` bukan sintaks yang valid di 4.1.4** (`No viable statement
  for input 'REFRESH CATALOG'`), padahal `docs/10-external-catalogs.md`
  mencantumkannya. Desain DDL di §5.6 harus memetakan refresh ke
  `REFRESH EXTERNAL TABLE` atau ke sinkronisasi milik Nova.
- Catalog Iceberg tipe `hadoop` di S3 gagal (`Namespace does not exist`) sampai
  prefix warehouse dan namespace punya objek. Catalog REST atau HMS tidak
  terkena masalah ini.
- `INSERT` 30.000 baris dari JDBC ke Iceberg di MinIO lokal memakan sekitar
  4 menit. Ini bukan benchmark, tapi menandakan jalur tulis Iceberg perlu
  diukur sebelum dipakai untuk ingest.

**Belum dijalankan:** JAR driver yang dibundel di image FE (spike ini
mengunduh driver dari Maven Central saat `CREATE CATALOG`). S3 dan S4 ada di
§6.2.

### 6.2 Hasil S3 dan S4 (2026-11-05)

**S4, Snowflake lewat JDBC catalog StarRocks: perlu patch FE.**
`JDBCMetadata` pada commit pin memilih schema resolver dari nama kelas driver
(`fe/fe-core/.../connector/jdbc/JDBCMetadata.java:109-123`) dan hanya mengenal
MySQL, PostgreSQL, MariaDB, ClickHouse, Oracle, dan SQL Server. Driver lain
ditolak dengan `<driver_class> not support yet`, bahkan sebelum koneksi dibuat.
Uji di FE yang berjalan: `CREATE EXTERNAL CATALOG` dengan driver Snowflake
diterima (tanpa validasi), lalu `SHOW DATABASES` gagal saat FE mencoba memuat
dan memilih resolver.

Jalur yang dipilih untuk keputusan §7.2: patch FE yang menambahkan
`SnowflakeSchemaResolver` (sekitar 200 baris, sebesar `PostgresSchemaResolver`)
plus pemetaan tipe Snowflake (`NUMBER`, `VARIANT`, `TIMESTAMP_*`, dan lainnya).
Hasilnya, tabel Snowflake menjadi tabel StarRocks biasa: di-query lewat jalur
SQL Nova, ditegakkan Ranger (S1 sudah membuktikan row filter dan mask berlaku
di tabel JDBC), dan bisa dipakai semantic view tanpa jalur eksekusi kedua.
Yang masih harus dibuktikan dengan akun Snowflake sungguhan: scanner JDBC di BE
bekerja dengan driver Snowflake, quoting identifier, dan pushdown filter.
Pushdown akan selemah Postgres (agregasi dan join tidak didorong).

**S3, otorisasi objek metadata-only: dihilangkan dari desain.** Tidak ada
endpoint evaluasi policy di Ranger Admin, sehingga mengevaluasi akses untuk
objek yang tidak dikenal engine berarti membangun evaluator policy di Nova,
yang dilarang `AGENTS.md`. Karena Snowflake kini juga lewat engine, setiap
catalog yang didaftarkan ke Nova Catalog harus punya pasangan catalog di
StarRocks. Visibilitas metadata untuk setiap caller diambil dari engine dengan
koneksi caller itu sendiri, yang sudah disaring Ranger (terbukti di S1:
`SHOW CATALOGS`, `SHOW DATABASES`, `SHOW TABLES`, `information_schema.tables`).
Mode `foreign_metadata` di §5.1 hanya boleh ada sebagai status sementara
("koneksi terdaftar, catalog engine belum dibuat") dan tidak pernah
menampilkan objek kepada pengguna.

### Fase 1: Catalog core (native + migrasi external catalogs)

- Modul `backend/app/modules/catalog/` (router, service, repository, connector
  SPI), tabel §5.2 di `docker/init-nova.sql`.
- Konektor `starrocks_native`, `iceberg_rest`, `hive`; migrasi
  `CONFIG_EXTERNAL_CATALOGS` → `CATALOG_CATALOGS`.
- Sinkronisasi engine → `CATALOG_OBJECTS` di `nova-worker`; rekonsiliasi
  catalog yang dibuat via SQL mentah.
- Explorer membaca catalog store, **difilter per caller** (menutup celah admin
  pool).
- Keluar: Explorer menampilkan objek yang sama dengan `SHOW` sebagai caller untuk
  role uji non-root; catalog SQL-mentah muncul di Connections; tidak ada
  kredensial di respons, log, maupun baris `NOVA_SYSTEM`.

**Status Fase 1 (2026-11-05): diimplementasikan.** Perbedaan dari rencana di atas:

- Sinkronisasi berjalan di **loop control-plane backend**
  (`catalog/refresher.py`, interval `CATALOG_SYNC_INTERVAL_SECONDS`, default
  300 detik, satu instance per pass lewat Redis leader lock), bukan di
  `nova-worker`, karena worker tidak berjalan di dev maupun CI.
- `CATALOG_CONNECTIONS` ditunda ke Fase 2 bersama JDBC dan `secret_ref`.
- Konektor yang ada: Iceberg (Hive Metastore, REST; hadoop lewat properti),
  Hive, dan internal (`default_catalog`, hanya disinkronkan).
- Catalog yang dibuat lewat SQL mentah tercatat dengan `source='engine'` dan
  terlihat di Explorer dan `GET /api/v1/catalog/catalogs`. Halaman Connections
  baru masuk di Fase 2.
- Visibilitas tetap dari sesi caller (Ranger), di-cache 60 detik per
  principal, role aktif, dan versi security context. Perubahan akses lewat Nova
  dan perubahan catalog langsung mengosongkan cache; grant di luar Nova terlihat
  paling lambat setelah TTL.

Kriteria keluar diuji di stack `docker` dev: untuk `nova_admin`/ACCOUNTADMIN,
`alice` (marketing, finance, regional_manager), dan `bob` (marketing),
`/catalog`, `/explorer`, dan `SHOW` sebagai caller menghasilkan database dan
tabel yang sama. Refresh hanya diizinkan untuk role security admin dan setiap
penolakan diaudit. Pemindaian `CATALOG_*` tidak menemukan kredensial. Pengujian
catalog Iceberg via SQL mentah ada di `tests/integration/test_catalog_l3.py`.

### Fase 2: Postgres dan MySQL (queryable)

- Konektor `postgres`, `mysql` → StarRocks JDBC catalog; JAR di image FE.
- DDL `CREATE CONNECTION` / `CREATE FOREIGN CATALOG` / `REFRESH CATALOG` di
  `sql_frontend`.
- `GRANT`/`REVOKE` menerima catalog; compiler Ranger dan bootstrap mendukung
  catalog non-default.
- Keluar: user non-root dengan grant Ranger bisa `SELECT` dari
  `pg_catalog_x.public.orders`, tanpa grant ditolak, dan (jika S1 lolos) row
  filter/mask berlaku. Tes integrasi memakai Postgres di stack yang ada.

### Fase 3: Metadata bisnis dan pencarian

- Tag (menghidupkan `CONFIG_OBJECT_TAGS`), komentar, owner, klasifikasi kolom
  di securable id.
- Pencarian catalog (nama, kolom, tag, komentar) yang menghormati filter per
  caller.
- Lineage tabel-ke-tabel dari `AUDIT_LOG` (roadmap #6) dikaitkan ke securable id.
- Semantic view dan entity menunjuk securable id.
- Keluar: tag pada kolom Postgres bisa dicari dan muncul di Studio; objek yang
  di-drop di sumber tetap menyimpan tag dengan status dropped.

### Fase 4: Snowflake

**Status sisi engine (2026-11-05): selesai dan terbukti dengan akun Snowflake
sungguhan.**

- Patch `patches/starrocks/4.1.4-jdbc-snowflake.patch` menambahkan:
  - `SnowflakeSchemaResolver` beserta pemetaan tipe Snowflake;
  - quoting `"` di `JDBCScanNode`;
  - pemuatan driver yang terisolasi. Driver hanya diambil dari JAR lokal `file://`
    yang checksum-nya cocok, dan tidak pernah diletakkan di `fe/lib`, karena driver
    Snowflake membawa `zstd-jni` sendiri.
- Driver `snowflake-jdbc` 3.28.0 dibundel di image FE dan BE pada
  `/opt/starrocks/drivers/`, dengan pin SHA-256.
- Lulus: `CREATE EXTERNAL CATALOG`, `SHOW DATABASES`/`TABLES`, `DESC`, `SELECT`,
  filter dan `LIMIT` yang didorong ke Snowflake (dicek lewat `EXPLAIN`), serta row
  filter dan mask Ranger untuk role non-root. Row filter juga ikut didorong ke
  Snowflake. `SHOW CREATE CATALOG` tidak menampilkan password.
- Detail ada di `docs/benchmarks/snowflake-jdbc-2026-11-05/`.
- URI wajib memuat `db=`, `JDBC_QUERY_RESULT_FORMAT=JSON`, dan `TIMEZONE=` yang sama
  dengan zona waktu engine. URI tidak boleh berisi rahasia, karena `jdbc_uri` tampil
  utuh di `SHOW CREATE CATALOG`.
- Belum: konektor Nova `snowflake`, panen `ACCOUNT_USAGE`, dan semantic view atau agen
  di atas tabel Snowflake. Ketiganya menunggu fase 2.

- Patch FE `SnowflakeSchemaResolver` (§6.2) dengan prosedur di
  `patches/starrocks/AGENTS.md`, plus JAR driver Snowflake di image FE.
- Konektor `snowflake` di Nova Catalog membuat JDBC catalog StarRocks dan
  memanen komentar serta tag Snowflake dari `ACCOUNT_USAGE`.
- Tabel Iceberg yang dikelola Snowflake boleh juga lewat Iceberg REST ke
  Horizon/Open Catalog bila performa scan file lebih penting.
- Semantic view menerima `snowflake_catalog.schema.table` sebagai dataset.
- Keluar: role non-root dengan grant Ranger bisa `SELECT` tabel Snowflake,
  row filter dan mask berlaku, dan agen menjawab pertanyaan lewat semantic view
  yang memakai tabel Snowflake. Diuji dengan akun Snowflake sungguhan.

### Fase 5 (opsional): Nova Catalog sebagai server untuk engine lain

- Ekspos endpoint Iceberg REST (proxy ke catalog Iceberg yang terdaftar) dan/atau
  subset API Unity Catalog, supaya Spark/Trino/DuckDB bisa memakai Nova Catalog.
- Butuh desain identitas tersendiri (token untuk engine eksternal yang tetap
  dipetakan ke principal StarRocks dan satu active role).

---

## 7. Keputusan

Diputuskan pemilik produk pada 2026-11-05:

1. **Bentuk "mandiri": modul backend.** Nova Catalog dibangun sebagai
   `backend/app/modules/catalog/` dengan API, connector SPI, dan tabel
   `NOVA_SYSTEM` sendiri. Pemisahan menjadi proses `nova-catalog` tetap terbuka
   tanpa mengubah model.
2. **Snowflake: tabel Snowflake-native harus bisa di-query dan bisa dipakai di
   semantic view untuk agen.** Metadata-only tidak cukup. Jalur yang
   diutamakan adalah tetap lewat StarRocks (JDBC catalog atau Iceberg), supaya
   Ranger tetap satu-satunya penegak dan semantic view memakai jalur SQL yang
   sama. Kalau StarRocks tidak bisa, keputusan ini menjadi izin eksplisit untuk
   merancang jalur eksekusi remote baru, dengan syarat yang tidak berubah:
   kredensial servis Snowflake tidak boleh menggantikan akses caller, dan
   Ranger tetap memutuskan akses. Hasil uji jalur StarRocks ada di §6.2.

Masih terbuka:

3. **Federasi query vs ingest.** Untuk sumber yang sering diagregasi, apakah
   cukup JDBC catalog (pushdown lemah), atau disediakan jalur ingest ke tabel
   native / Iceberg?
4. **Fase 5** perlu atau tidak, dan kapan.

## 8. Risiko

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Ranger tidak menegakkan RLS/masking di tabel external | Kebocoran data di catalog queryable | S1 lolos untuk JDBC dan Iceberg (§6.1); jadikan tes acceptance di Fase 2 agar regresi engine terdeteksi |
| JDBC catalog masih Beta di StarRocks | Bug tipe data, performa | Matriks tipe per konektor di tes; batasi ke Postgres/MySQL dulu |
| Metadata harvest basi | Agen dan user melihat skema lama | TTL lazy refresh, `REFRESH`, tampilkan `last_sync_at` |
| Kredensial koneksi bocor lewat `SHOW CREATE CATALOG` | Insiden keamanan | Redaksi yang sudah ada + tes regresi untuk properti JDBC |
| Beban ke database sumber | Gangguan sistem operasional pelanggan | Rate limit sinkronisasi, jalankan di worker, query metadata saja |
| Evaluasi authz di luar engine membuat engine authz kedua | Melanggar invariant | S3: setiap objek harus punya pasangan catalog engine; visibilitas selalu dari engine sebagai caller |

## 9. Hal yang sengaja tidak termasuk

- Menjadikan Gravitino, Polaris, atau Unity Catalog sebagai catalog of record.
- Store metadata relasional di luar `NOVA_SYSTEM`.
- Mesin query kedua (Trino) atau jalur eksekusi remote tanpa keputusan §7.
- Tabel izin atau model role milik Nova sendiri.
