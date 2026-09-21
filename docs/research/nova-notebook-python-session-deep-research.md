# Riset Mendalam: Notebook Python & Integrasi "Session" ala Snowpark

> **Workstream:** permintaan baru — workspace Python notebook (`.ipynb` + `.py`) dengan Python
> yang bisa berintegrasi ke *session* database, meniru **Snowflake Notebooks + Snowpark Python**.
> **Status:** riset + opsi arsitektur, **bukan** implementasi. Tidak ada kode produksi disentuh.
> **Engine pin:** StarRocks 4.1.4 (`docker/docker-compose-engine.yml:122,159`).
> **Tanggal akses sumber web:** 2026-09-20.
> **Aturan:** tidak ada secret/credential di dokumen ini. Setiap klaim faktual diberi sumber +
> tingkat keyakinan; yang tidak terverifikasi ditandai eksplisit (`[PERLU VERIFIKASI]`).

---

## 0. Ringkasan eksekutif

User meminta tiga hal, dan penting dipisah karena masing-masing punya tingkat kesulitan yang
sangat berbeda:

1. **UI notebook** — sel Python + SQL + Markdown, tab `.ipynb` dan `.py`, tombol Run, pilih
   role/warehouse (gambar referensi: Snowsight worksheet). **Ini murah** — Monaco + editor sel.
2. **Python yang benar-benar jalan** — ada kernel/runner yang mengeksekusi kode user.
   **Ini mahal dan berisiko tinggi**, karena memperkenalkan masalah baru yang belum pernah
   dimiliki Nova: **eksekusi kode arbitrer di host**.
3. **"Integrasi ke session"** — Python bisa memakai koneksi/kredensial user yang sama seperti
   worksheet SQL. Di Snowflake ini adalah `snowpark.Session`. **Ini bagian yang paling
   "native"**, dan sebenarnya **paling murah** karena Nova sudah punya semua primitifnya.

Temuan inti:

1. **Nova tidak punya eksekusi Python sama sekali hari ini.** Tidak ada `subprocess`, `exec()`,
   kernel, Jupyter, Snowpark, atau sandbox di jalur runtime. Satu-satunya "Python" adalah
   `ml_engine` yang menjalankan scikit-learn **in-process di event loop FastAPI** — pola yang
   **tidak boleh** dipakai untuk kode user arbitrer. (`backend/app/modules/ml_engine/service.py:288`)
2. **Primitif "session" sudah ada dan persis yang dibutuhkan.** JWT membawa `sub`+`sid`; `sid`
   menunjuk ke hash Redis `nova:session:*` yang menyimpan **password StarRocks ter-enkripsi
   Fernet**; setiap query mendekripsi lalu membuka koneksi `asyncmy` **as user** dengan
   `SET ROLE <active_role>`. (`backend/app/core/deps.py:15`, `core/redis.py:34`,
   `core/database.py:57`, `modules/query/repository.py:125`). Untuk proses di luar request
   (kernel background), sudah ada pola recovery kredensial: `SessionCredentialProvider.password_for`
   yang `SCAN` sesi Redis dan mencocokkan `username` (`modules/task_orchestration/credentials.py:62`).
3. **Snowpark "native" itu = Path A, bukan Path B.** Yang membuat Snowpark terasa native adalah
   **sesi Python yang hidup dan memegang koneksi DB**, lalu operasi DataFrame **lazy → pushdown
   jadi SQL**. Ini bukan soal menjalankan Python di dalam database; ini soal Python *sebagai
   klien* dengan sesi yang persisten. Konsekuensinya: engine yang tidak punya socket TCP
   (Pyodide/WASM, WebContainers) **secara struktural tidak bisa** jadi jalur utama yang native.
4. **Rekomendasi arsitektur (detail di §7):** **kernel CPython per-user yang long-lived**,
   dijalankan di dalam **container sandbox** (Docker + `gVisor`/`nsjail`), dikelola FastAPI via
   **protokol Jupyter kernel** (ZeroMQ) atau **Jupyter Kernel Gateway** (REST+WS), dengan shim
   Python ala-Snowpark (`nova.Session`) yang memakai `asyncmy`/`pymysql` + `pyarrow` sebagai data
   plane. Ini memberi "native-feel" tertinggi tanpa menyerahkan data ke vendor sandbox cloud.
5. **Risiko terbesar bukan notebooknya, tapi isolasi host.** MySQL proxy hari ini mengisolasi di
   level *SQL/StarRocks*; kode Python mengisolasi di level *host*. Keputusan sandbox (§6) adalah
   keputusan arsitektur paling berisiko di sini, bukan keputusan UI.
6. **Engine yang "bisa di-integrate native":** ada dua kelas. (a) **Engine kernel** yang
   menjalankan Python — `ipykernel`/Jupyter adalah standar de-facto dan paling cocok; Marimo
   menarik karena file notebook-nya **memang `.py`**; E2B/Modal/Daytona memberi isolasi kuat
   tanpa mengurus infra. (b) **Data plane** — `Arrow Flight SQL` + `ADBC` adalah jalur
   "native" ideal, tetapi **[PERLU VERIFIKASI]** StarRocks 4.1.4 **tidak mengiklankan** endpoint
   Flight SQL di dokumentasi publik; jalur praktis hari ini adalah **MySQL protocol + `pyarrow`**
   (konversi di sisi klien).
7. **Semua invariant Nova tetap berlaku:** delegate-first (koneksi sebagai user, `SET ROLE`),
   credential-invisible (password hanya di memori kernel, tidak pernah ke `NOVA_SYSTEM`/log/response),
   audit ke `NOVA_SYSTEM.AUDIT.LOG` untuk setiap eksekusi, dan **tanpa JVM di runtime**
   (Java hanya build/CI untuk ANTLR — `README.md:933`).

Ringkasan keputusan ada di §9. Opsi yang **tidak** direkomendasikan (dan alasannya) ada di §8.

---

## 1. Apa sebenarnya yang membuat Snowpark terasa "native"?

Ini kerangka penilaian untuk semua engine di §4–§5. Snowpark bukan "Python di dalam Snowflake";
ia punya enam properti yang harus ditiru Nova:

| # | Properti Snowpark | Artinya untuk Nova |
|---|---|---|
| 1 | **Sesi hidup yang persisten** (`snowpark.Session`) | Satu objek sesi per user, bukan koneksi per-request. Memegang konteks database/schema/role. |
| 2 | **DataFrame lazy** — transformasi belum dieksekusi | `s.table("x").filter(...)` membangun *plan*/AST, bukan menjalankan. |
| 3 | **Pushdown** — komputasi pindah ke engine | Operasi ala-Pandas dikompilasi jadi SQL dan dijalankan di StarRocks, bukan di klien. |
| 4 | **UDF / stored procedure** | Kode Python bisa "dikirim" ke sisi engine. |
| 5 | **Notebook/worksheet UI berbagai sesi** | Sel Python + sel SQL berbagi sesi yang sama di Snowsight. |
| 6 | **Kredensial dan RBAC tetap berlaku** | Sesi mewarisi role/privilege user. |

**Kesimpulan penting:** properti #1–#3 dan #6 adalah **Path A** — proses Python lokal/remote
yang memegang koneksi DB dan *push down* SQL. Ini **persis** yang dibutuhkan Nova dan **persis**
yang sudah bisa dilakukan primitif Nova hari ini. Properti #4 (Python di dalam engine) memerlukan
StarRocks UDF Python, yang **belum tentu ada** di 4.1.4 dan bukan prasyarat (lihat §5.6).

Sumber: https://docs.snowflake.com/en/developer-guide/snowpark/python/index · https://docs.snowflake.com/en/developer-guide/snowpark/python/creating-session

---

## 2. Kondisi Nova hari ini (baseline yang harus dihormati)

### 2.1 Jalur eksekusi query (web)

`QueryService.execute` (`backend/app/modules/query/service.py:227-510`) adalah pipeline tetap:

```
guard_user_statement → parse_sql (ANTLR @stage) → prepare_stage_sql (FILES() + inject creds)
  → decrypt_password(encrypted_password)
  → repo.execute_as_user(sql, username, password, database, role)
  → write_audit_log(...)
```

`execute_as_user` (`modules/query/repository.py:125-172`) membuka `db.user_conn(username, password)`
— **koneksi baru per request, tanpa pool** — lalu `_execute_on` melakukan `SET ROLE <role>` dan
query. `QueryResult` **selalu** meredaksi `executed_sql` (`repository.py:68`).

Sumber kredensial: `decrypt_password` dari Redis session (`service.py:410`). Untuk jalur proxy,
password dilewati karena koneksi sudah terautentikasi (`repository.py:130`).

### 2.2 Model sesi/auth

- **JWT HS256** membawa `{"sub": username, "sid": session_id}` (`core/security.py:22`).
- **Redis hash** `nova:session:<sid>` menyimpan `username`, `encrypted_password` (Fernet),
  `roles`, `active_role`, TTL `SESSION_TTL_SECONDS` (default 3600). (`core/redis.py:34-51`)
- `get_current_user` (`core/deps.py:15-53`) memvalidasi + refresh TTL + mengembalikan
  `encrypted_password` ke handler.
- **StarRocks = sumber kebenaran auth** (AGENTS.md §5). Tidak ada tabel user terpisah.

### 2.3 Pola "jalankan sebagai user di luar request" — sudah ada

`SessionCredentialProvider.password_for(username)` (`modules/task_orchestration/credentials.py:62-86`)
mem`SCAN` semua key `nova:session:*`, mencocokkan field `username`, dan mendekripsi password.
Ini dipakai worker task orchestration untuk `DelegateExecutor`. **Ini persis pola yang akan
dipakai kernel notebook** untuk mendapatkan kredensial user tanpa JWT di tangan.

**Keterbatasan yang perlu dicatat:** tidak ada reverse index `username → sid`; provider
melakukan `SCAN` linear. Untuk kernel per-user ini masih OK (satu kali saat start/resume), tapi
bila jumlah sesi besar, indeks balik layak dipertimbangkan.

### 2.4 Workspace & file

- Metadata di StarRocks: `NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES` dengan `entry_type ∈ {file,folder}`;
  versi di `CONFIG_WORKSPACE_FILE_VERSIONS`. (`common/nova_system.py:11-49`)
- **Isi file di object storage (S3/MinIO)** via boto3; key `<base_prefix>/<username>/<path>`,
  versi `<base_prefix>/<username>/.versions/<entry_id>/<n>.sql`. (`modules/workspaces/service.py:51-67`)
- **Semua file hari ini adalah teks SQL.** `get_file` menolak non-file, key versi hardcode `.sql`,
  Monaco hardcode language `'sql'`. (`service.py:148`, `frontend/src/features/workspaces/index.tsx:4070`)
- Frontend: `frontend/src/features/workspaces/index.tsx` (~4.4k baris), satu `MonacoSqlEditor`
  per tab, `PUT /workspaces/state` menyimpan tab.

### 2.5 Redis & worker

Satu Redis untuk sesi + transport task (Redis Streams `nova:tasks:graph_runs`). `nova-worker`
dan `nova-scheduler` adalah proses terpisah (`backend/app/worker/__main__.py`,
`backend/app/scheduler/__main__.py`) — **preseden untuk proses kernel terpisah dari FastAPI**.

### 2.6 Yang belum ada

Tidak ada `subprocess`, `multiprocessing`, `exec()`, `ProcessPoolExecutor`, kernel, Jupyter,
Snowpark, atau sandbox **di kode aplikasi**. Dokumentasi bahkan mencatat notebook sebagai
*out-of-v1* (`README.md:846`, `docs/roadmap-snowflake-parity.md:151`). Permintaan ini adalah
**E-decision baru**, dan dokumen ini adalah bahannya.

---

## 3. Dua jalur fundamental, dan satu jalan buntu

**Path A — Proses Python hidup memegang koneksi DB (Snowpark, Jupyter, Marimo server-side).**
State antar-sel native (interpreter hidup). Bisa push down SQL. Bisa kredensial di memori.
→ **Ini jalur yang benar untuk Nova.**

**Path B — Runtime tanpa socket (Pyodide/WASM, WebContainers).**
Tidak bisa membuka koneksi MySQL/TCP. Semua akses DB harus lewat HTTP bridge buatan sendiri,
tidak ada sesi DB persisten, dan kredensial berisiko terekspos di browser. Tidak ada threading,
batas memori ~2 GB.
→ **Buntu untuk "native". Hanya layak untuk notebook non-DB / demo.**

Ini mempersempit pilihan secara drastis: **engine harus CPython proses, bukan WASM.**

---

## 4. Kandidat engine — profil dan penilaian

Skor 1–5 (5 terbaik). "Native-feel" = kedekatan ke properti Snowpark §1.

| Engine / model | Native-feel | Isolasi | Ops (5=simplest) | Latency | Ekosistem Py | Effort integrasi FastAPI+MySQL | Catatan |
|---|---|---|---|---|---|---|---|
| **ipykernel/Jupyter (self-managed, container/user)** | 5 | 2 (4 di container) | 3 | 4 | 5 | 3 | Path A; butuh sandbox sendiri; tanpa Hub |
| **JupyterHub + K8s** | 5 | 5 | 1 | 3 | 5 | 2 | Multi-tenant penuh, infra berat |
| **Jupyter Kernel Gateway (embedded)** | 5 | 2 | 4 | 4 | 5 | 5 | API headless ideal; bawa sandbox sendiri |
| **Enterprise Gateway (K8s)** | 5 | 5 | 2 | 3 | 5 | 3 | Kernel-as-a-Service; syarat K8s |
| **Marimo (server-side)** | 4 (reaktif) | 2 (4 di container) | 4 | 4 | 5 | 3 | Notebook **memang `.py`**; adopsi UX-nya |
| **Marimo WASM** | 1 | 5 (klien) | 5 | 5 | 3 | 3 | Tanpa socket → bukan DB-native |
| **Pyodide / PyScript** | 1 | 5 (klien) | 5 | 5 | 3 | 2 | Tanpa socket/thread; 2 GB; risiko creds |
| **E2B (microVM)** | 4–5 | 5 | 4 | 3 | 5 | 4 | Snapshot RAM+disk; data keluar VPC |
| **Modal Sandbox** | 4–5 | 5 | 4 | 3 | 5 | 4 | Lifecycle/pool kaya; data keluar VPC |
| **Fly Machines / Daytona** | 4 | 5 | 3 | 4 | 5 | 3 | Fly mentah/DIY; Daytona fokus agen |
| **gVisor (self-host)** | 4–5 | 5 | 2 | 3 | 5 | 3 | OCI runtime; penalti syscall-heavy |
| **Firecracker (self-host)** | 4 | 5 | 1 | 4 | 5 | 2 | Boot <125 ms; kelola sendiri |
| **nsjail / bubblewrap** | 3 (per-exec) | 3–4 | 4 | 5 | 4 | 4 | Hardening murah; tanpa state antar-sel sendiri |
| **Docker per-session (+warm pool)** | 5 | 4 | 3 | 3 (warm: 5) | 5 | 4 | Keseimbangan terbaik self-host |
| **Ray / Dask** | 3 | 2 | 2 | 3 | 5 | 3 | Scaling compute, bukan sandbox |
| **Arrow Flight SQL / ADBC** | 5 (data plane) | N/A | 5 | 5 | 5 | 4 (StarRocks TBD) | Jalur data "native"; verifikasi endpoint |

### 4.1 Jupyter / JupyterLab / ipykernel
Arsitektur **dua proses + ZeroMQ**: server (Tornado/HTTP) mengelola *kernel record*; setiap kernel
adalah proses OS terpisah berbicara **Jupyter Messaging Protocol v5.5** lewat 5 socket
(Shell/IOPub/stdin/Control/Heartbeat). Setiap pesan adalah multipart ter-HMAC-SHA256. Frontend
dapat bicara langsung ke kernel via WebSocket (bridge `ZMQChannelsWebsocketConnection`), persis
yang dipakai JupyterLab.
**State:** kernel = interpreter hidup, `user_ns` tidak dibongkar → variabel (dan DataFrame) tetap
ada antar-sel. **Isolasi lemah by default** (proses same-user); multi-tenant didelegasikan ke
JupyterHub+Spawner. **Runtime:** `ipykernel` (CPython). **Koneksi DB:** `pymysql`/`pymysql`+Arrow
langsung ke FE StarRocks — Path A nyata.
Sumber: https://jupyter-client.readthedocs.io/en/latest/messaging.html · https://jupyter-server.readthedocs.io/en/latest/operators/public-server.html

### 4.2 Jupyter Kernel Gateway / Enterprise Gateway
**Kernel Gateway** = "kernel tanpa notebook": REST (`POST /api/kernels`) + WebSocket
(`/api/kernels/{id}/channels`) untuk eksekusi. **Tidak ada UI** — UI adalah aplikasi Anda.
**Enterprise Gateway** = versi cluster: "Kernel-as-a-Service", kernel diluncurkan remote di
K8s/YARN/Docker via *process proxy* + *kernel launcher*, mendukung HA.
Ini **titik manis untuk embed di aplikasi non-Jupyter**: FastAPI memegang client HTTP ke Gateway,
memetakan kernel id ↔ sesi Nova, dan passthrough WS. Tanpa JupyterHub. Kekurangannya: Kernel
Gateway **tanpa isolasi** (harus sandbox sendiri); Enterprise Gateway membawa K8s.
Sumber: https://jupyter-kernel-gateway.readthedocs.io/en/latest/ · https://jupyter-enterprise-gateway.readthedocs.io/en/latest/

### 4.3 Marimo
Notebook **reaktif**: disimpan sebagai **file Python murni** dengan `@app.cell`; tanda tangan sel
mendeklarasikan dependensi dan return mendeklarasikan definisi, sehingga Marimo membangun DAG
dan hanya menjalankan ulang sel yang terpengaruh. Dua mode server: `marimo edit` dan
`marimo run`. Mode WASM = Pyodide.
**Relevansinya untuk Nova:** user minta `.py`, dan **notebook Marimo memang `.py`** — lebih dekat
ke worksheet Snowpark daripada `.ipynb`, dan bisa di-`import` sebagai modul. Tapi Marimo adalah
aplikasi penuh; integrasi natural adalah iframe/adopsi UX, bukan "kernel headless yang saya
kontrol". Mode WASM tidak bisa socket → bukan jalur DB-native.
Sumber: https://docs.marimo.io/guides/apps/ · https://docs.marimo.io/guides/editor_features/watching/ · https://docs.marimo.io/guides/wasm/

### 4.4 Pyodide / PyScript
CPython di WASM. **Batasan fatal untuk Nova:** tanpa socket TCP native, single-threaded tanpa
proses, langit-langit memori ~2 GB, tanpa pdb, dan kredensial StarRocks berisiko di browser
(melanggar aturan credential-invisible). State hilang saat refresh. **Bukan jalur utama.**
Sumber: https://pyodide.org/en/stable/usage/api/python-api.html

### 4.5 Sandbox cloud: E2B / Modal / Fly / Daytona
Ini **penyedia isolasi**, bukan engine notebook — Anda menjalankan `ipykernel`/Marimo di dalamnya.
- **E2B**: microVM Firecracker; pause/resume mempertahankan **RAM + filesystem** (cerita
  persistensi state Python yang nyata); SDK `Sandbox.create()`, reconnect by id.
- **Modal**: `modal.Sandbox.create(...)`; `exec`, Volume, idle timeout (default 5 mnt, max 24 jam),
  snapshot filesystem, TCP tunnel, named sandbox untuk pool; **secara eksplisit mendokumentasikan
  contoh sandbox Jupyter**.
- **Fly Machines**: microVM + HTTP API; kontrol penuh, tapi Anda membangun manajemen kernel.
- **Daytona**: sandbox OCI-compatible, create ~90 ms, snapshot, warm pool, preview proxy, LSP.

**Trade-off umum:** isolasi kuat **tanpa mengurus ops Firecracker/gVisor**, tapi **data keluar
dari environment Anda** + **vendor lock-in** — signifikan untuk console DB enterprise.
Sumber: https://docs.e2b.dev · https://modal.com/docs/guide/sandbox · https://www.daytona.io/docs/en/

### 4.6 Primitif isolasi self-host (gVisor / Firecracker / Kata / nsjail / bubblewrap)
- **gVisor**: *application kernel in userspace* (Sentry menangkap syscall; Gofer menangani FS via
  9P; `runsc` = OCI runtime untuk Docker/K8s). Keamanan mirip VM, footprint mirip container.
  Penalti pada beban syscall-heavy. **Pilihan umum untuk kode tak-terpercaya multi-tenant.**
- **Firecracker**: microVM KVM, boot **<125 ms**, overhead <5 MiB, sampai **150 microVM/detik/host**,
  `jailer` sebagai lapis kedua. Ini yang dipakai Lambda/E2B/Fly/Kata. Opsi self-host paling aman.
- **Kata Containers**: VM per container via OCI; paling berat.
- **nsjail**: isolasi proses ringan (namespaces, chroot/pivot_root, rlimit, seccomp-bpf via Kafel,
  cgroup v1/v2). **`nsjail`-membungkus Python per-eksekusi** adalah hardening paling sederhana.
- **bubblewrap**: sandbox unprivileged via user namespace; Anda yang mendefinisikan kebijakan.

**Panduan:** nsjail/bubblewrap untuk hardening per-eksekusi yang murah; gVisor untuk multi-tenant
kuat tanpa VM; Firecracker/Kata bila tenancy menuntut isolasi kelas hardware.
Sumber: https://gvisor.dev/docs/ · https://firecracker-microvm.github.io/ · https://github.com/google/nsjail · https://github.com/containers/bubblewrap

### 4.7 Dask / Ray / Arrow Flight SQL / ADBC (data plane)
Orthogonal terhadap pilihan kernel, tapi inti "native feel".
- **Arrow Flight SQL**: protokol gRPC yang berbicara format kolumnar Arrow untuk DB SQL; mendukung
  prepared statement, bulk ingest, metadata, dan **manajemen sesi** (`SetSessionOptions`/`CloseSession`).
  Hasil = stream `FlightData` (record batch Arrow) → zero-copy ke pandas/polars/duckdb.
  **Ini satu-satunya jalur yang DB-native sekaligus Arrow-native dengan sesi server-side.**
- **ADBC**: standar konektivitas DB Arrow-native lintas bahasa; `adbc_driver_flightsql` dll.
- **Ray**: tasks/actors/object store; actor stateful bisa jadi model untuk *session actor* per user,
  tapi bukan batas isolasi dan menambah cluster. **Dask**: scheduler/workers; layer scaling compute.

> **[PERLU VERIFIKASI]** Dokumentasi publik StarRocks (https://docs.starrocks.io/llms.txt) **tidak
> mengiklankan endpoint Arrow Flight SQL**. Konektor native StarRocks adalah Flink/Kafka/Spark +
> **MySQL protocol (FE query_port 9030)** + MCP server baru. Jadi jalur praktis hari ini adalah
> **MySQL protocol + konversi Arrow di sisi klien**. Flight SQL dicatat sebagai ekstensi yang harus
> dikonfirmasi di source StarRocks sebelum dijadikan desain.
Sumber: https://arrow.apache.org/docs/format/FlightSql.html · https://arrow.apache.org/adbc/current/index.html · https://docs.ray.io/en/latest/ray-core/walkthrough.html

---

## 5. Model spawn & persistensi state (pertanyaan inti)

| Model | State antar-sel | Cold start | Isolasi | Multi-tenant | Kompleksitas |
|---|---|---|---|---|---|
| **Kernel long-lived per-user** (Jupyter/Kernel Gateway/Marimo) | Native — `user_ns` hidup; DataFrame tetap di RAM | Hanya saat pertama | Lemah kecuali di-container | Baik dengan container/microVM per-user | Medium |
| **Proses/container per-eksekusi** (nsjail `-Mo`) | **Tidak ada** kecuali dieksternalisasi | Tiap sel | Terkuat | Sangat baik (stateless) | Rendah–Medium |
| **Sandbox/microVM per-sesi** (E2B/Modal/Daytona/pod) | Native selama jalan; snapshot untuk pause | Sekali per sesi | Kuat (VM) | Sangat baik | Medium (managed)/Tinggi (self-host) |
| **Sandbox pooled** (warm pool + lease) | Native selama di-lease | Nyaris nol | Kuat | Sangat baik | Tinggi (pool manager) |
| **Browser WASM** (Pyodide) | Di tab; hilang saat refresh | Nyaris instan | N/A (klien) | Buruk untuk DB-native | Rendah |

**Cara sistem nyata mempertahankan variabel antar-sel:**
- **Jupyter/Kernel Gateway:** kernel *adalah* state — interpreter hidup. Itulah inti "kernel".
- **Marimo:** sesi server + **DAG reaktif**; state diturunkan ulang dengan menjalankan sel terdampak.
- **Per-eksekusi (nsjail/Firecracker fresh tiap sel):** tidak bisa menjaga interpreter hangat, jadi
  harus **eksternalisasi state** (snapshot namespace/`cloudpickle`, atau objek sesi yang merupakan
  proses hidup). Ini justru tempat **Ray actor** atau container persisten bersinar.
- **E2B/Modal:** pause/resume mempertahankan RAM+FS (E2B) atau snapshot FS (Modal).

**Implikasi untuk Nova:** bila ingin DataFrame hidup antar-sel (ala Snowpark), **wajib proses
per-user yang long-lived** (kernel-in-container/microVM) atau *stateful actor*. Isolasi
per-eksekusi hanya layak bila kita rela kehilangan state in-memory — yang merusak UX notebook —
atau bila sengaja snapshot/pickle (rapuh untuk DataFrame besar).

---

## 6. Keputusan isolasi (risiko arsitektur utama)

Nova hari ini mengisolasi di level **SQL/StarRocks** (RBAC, `SET ROLE`, guard). Kode Python
mengisolasi di level **host**: filesystem, network, memori, CPU, proses. Ini masalah **baru**
yang tidak pernah dimiliki MySQL proxy. Opsi, berurut dari paling terkontrol ke paling mudah:

1. **Docker per-sesi + gVisor (`runsc`)** — batas multi-tenant yang kuat, OCI-native, tanpa K8s.
   Isolasi kelas-VM dengan UX container. **Rekomendasi default** bila self-host.
2. **nsjail membungkus interpreter** — paling murah, memperkuat proses tunggal. Cocok sebagai
   lapis dalam, atau bila menolak container apa pun. Tidak sekuat gVisor terhadap exploit kernel.
3. **Docker per-sesi tanpa gVisor** — cukup bila semua pengguna dipercaya (internal tool).
   Bukan batas keamanan yang sebenarnya terhadap kode jahat.
4. **E2B/Modal/Daytona** — isolasi terkuat paling cepat dengan effort ops paling kecil, dengan
   konsekuensi **data keluar VPC** + lock-in. Bila Nova on-prem/enterprise, ini sering tidak
   dapat diterima.

**Kebijakan yang harus dipegang apa pun opsinya** (lihat juga AGENTS.md):
- CPU/mem/disk **rlimit** per sesi; **timeout** eksekusi sel (QueryService hari ini **tidak punya
  timeout/cancel handle** — `docs/specs/nova-61-agentic-assistant-design.md:412` — ini harus
  ditambahkan untuk kernel).
- **Network default: hanya FE MySQL (9030) + endpoint stage**. Tidak ada internet bebas.
- Password StarRocks hanya di memori proses kernel, di-inject saat sesi dibuat, **tidak ditulis ke
  `NOVA_SYSTEM`/log/response**. Sepadan dengan `_snapshot_version` yang sengaja tidak menyimpan
  konten credential.
- Setiap eksekusi → `NOVA_SYSTEM.AUDIT.LOG`.

---

## 6A. Platform: gVisor **hanya Linux** — dan dev machine Nova itu macOS/Windows

Ini pertanyaan konkret dan jawabannya penting untuk desain. **Ya, gVisor (dan `runsc`) hanya
jalan di Linux.** Dokumentasi resmi gVisor menyatakan syaratnya secara eksplisit:

> *"gVisor supports x86_64 and ARM64, and **requires Linux 5.6+**."*
> — https://gvisor.dev/docs/user_guide/install/

Alasannya arsitektural, bukan sekadar packaging: gVisor adalah *application kernel* yang
mengimplementasikan ulang antarmuka syscall **Linux** di userspace (Sentry, Gofer, `runsc`).
Ia tidak punya port ke kernel XNU (macOS) maupun NT (Windows). Hal yang sama berlaku untuk
**nsjail** (bergantung pada Linux namespaces + seccomp-bpf), **Firecracker** (butuh KVM Linux),
dan **Kata Containers** (OCI runtime + hypervisor Linux). **Semua primitif isolasi self-host di
§4.6 adalah teknologi Linux.**

> Catatan lingkungan: dev machine saat ini **macOS 26.6.2 arm64 dengan OrbStack** (Docker 29.4).
> Docker Desktop / OrbStack / Colima menjalankan container di dalam **VM Linux** kecil — jadi
> Docker *command* jalan, tetapi **kernel yang mengeksekusi container adalah kernel Linux milik
> VM itu**, bukan kernel host macOS. Inilah kunci jawabannya di bawah.

### 6A.1 Yang sebenarnya Anda hadapi: dua environment berbeda

| Environment | Kernel | gVisor bisa? | Peran |
|---|---|---|---|
| **Dev di macOS/Windows** | XNU / NT (+ VM Linux untuk Docker) | **Tidak langsung** | Menulis kode, menguji alur, menjalankan StarRocks via compose |
| **Docker VM di macOS/Windows** (OrbStack/Colima/Docker Desktop) | Linux (di dalam VM) | Hanya bila runtime VM-nya dapat memasang `runsc` | Kadang bisa, sering tidak — lihat 6A.2 |
| **Server Linux** (bare-metal / VM Linux / EC2 / GCE) | Linux 5.6+ | **Ya** | Tempat gVisor seharusnya hidup |
| **Kubernetes Linux** | Linux | **Ya** | `RuntimeClass` gVisor (mis. GKE Sandbox) |

**Konsekuensi desain:** gVisor adalah **keputusan runtime server**, bukan keputusan aplikasi.
Nova tetap harus **mendesain untuk Linux di produksi**, tetapi **tidak boleh mengasumsikan gVisor
ada di mesin dev**. Artinya: lapisan sandbox harus **pluggable** — interface isolasi yang punya
beberapa implementasi (no-op/process untuk dev, Docker, Docker+gVisor, microVM managed), dipilih
lewat config. Ini juga sejalan dengan cara Nova memperlakukan engine/lib lain via `config`.

### 6A.2 Bisakah gVisor dipakai dari macOS lewat OrbStack?

Secara prinsip: **hanya jika Anda bisa menjalankan `runsc` sebagai OCI runtime di dalam VM Linux
yang meng-host container** — yang berarti `runsc` harus di-install **di dalam** OrbStack VM
(`orb run`/shell ke VM), bukan di macOS, lalu Docker *di dalam VM itu* dikonfigurasi
`{"runtimes": {"runsc": ...}}`. OrbStack menjalankan kernel Linux-nya sendiri (terlihat:
`kernel=7.0.14-orbstack`), jadi ada kernel Linux 5.6+ — tetapi:

- **OrbStack dan Docker Desktop tidak menyediakan UI/driver resmi untuk gVisor.** Ini bukan
  alur yang didukung vendor; Anda masuk ke VM dan mengubah `/etc/docker/daemon.json`, menyediakan
  `runsc` + `gvisor-bin/` di dalam VM, meregenerasi runtime, dan berharap versi kernel VM cocok.
- **Lisensi/arsip gVisor untuk arm64 tersedia**, tetapi tar-nya membawa sidecar `gvisor-bin/`
  yang harus hidup di path yang sama; auto-download ditutup akhir September 2026
  (sumber: halaman install gVisor yang sama).
- **Firecracker** di macOS lebih buruk lagi: butuh `/dev/kvm` di kernel Linux; Docker Desktop &
  OrbStack **tidak mengekspos nested virtualization/KVM** secara default. Jadi Firecracker lokal
  praktis **tidak mungkin** tanpa VM Linux milik sendiri (mis. UTM/Hypervisor.framework + Linux guest).

**Kesimpulan praktis:** jangan jadikan "gVisor jalan di laptop Mac" sebagai prasyarat apa pun.
Kalau Anda butuh isolasi setara gVisor saat dev di macOS, cara termanis adalah **menjalankan
kernel sandbox sebagai container/image yang sama persis dengan produksi**, lalu memakai
**Docker biasa (runc) di local** dan **gVisor hanya di server Linux** — lihat 6A.3.

### 6A.3 Pola yang direkomendasikan: abstraksi sandbox pluggable

```
SandboxBackend (interface)          ← dipilih via config: NOVA_SANDBOX_BACKEND
├── ProcessBackend                  ← dev-only, TANPA isolasi keras (macOS/Windows OK)
├── DockerBackend (runc)            ← dev & prod trusted; jalan di semua OS via VM Linux
├── GvisorBackend (runsc)           ← HANYA Linux 5.6+; prod multi-tenant yang di-hosting sendiri
├── FirecrackerBackend              ← HANYA Linux + KVM; prod isolasi kelas hardware
└── ManagedBackend (E2B/Modal)      ← lintas-platform (SDK call); data keluar VPC
```

Aturan pemetaan yang disarankan:

| Konteks | Backend | Alasan |
|---|---|---|
| Dev di macOS/Windows, kerja fitur | `ProcessBackend` atau `DockerBackend` | Cepat; isolasi bukan tujuan di laptop |
| CI | `DockerBackend` | Deterministik, jalan di runner Linux/Docker |
| Staging (Linux) | `DockerBackend` + `gvisor` bila tersedia | Uji jalur produksi |
| **Produksi multi-tenant** | `GvisorBackend` (Linux) atau `ManagedBackend` | Batas keamanan yang nyata |
| Produksi tenancy adversarial | Firecracker/Kata atau managed microVM | Kelas hardware |

**Konsekuensi jujur yang harus diterima:** selama dev di macOS/Windows, Anda **tidak** bisa
menguji isolasi keras secara lokal. Yang bisa diuji lokal adalah **kontrak** `SandboxBackend`
(lifecycle kernel, streaming output, interrupt, culling, injeksi kredensial) — dan itu justru
bagian yang paling bernilai untuk dibangun dan diuji tanpa gVisor.

### 6A.4 Altematif lintas-platform yang benar-benar jalan di macOS/Windows

Bila tujuannya "isolasi kuat tapi tim dev tetap di Mac/Windows", opsi yang **tidak** bergantung
pada gVisor lokal:

1. **Container Docker biasa di local + gVisor di server (pola `SandboxBackend`).** Paling
   direkomendasikan: satu image kernel, dua runtime.
2. **Managed sandbox (E2B/Modal/Daytona)** — SDK memanggil microVM di cloud; **jalan dari OS apa
   pun** (termasuk Mac/Windows) karena isolasinya di sisi penyedia. Ini satu-satunya opsi yang
   memberi isolasi keras **saat dev dari laptop**, dengan trade-off data keluar VPC + lock-in.
3. **Remote Linux dev box** (mis. VM Linux di cloud, atau devcontainer Linux) — jalankan gVisor di
   sana; laptop hanya jadi klien.
4. **Windows: WSL2** memberi kernel Linux asli (5.15+) di dalamnya, dan di WSL2 **gVisor bisa
   di-install** (`runsc install` + `systemctl reload docker`) bila Docker yang dipakai adalah
   Docker di dalam WSL2. Ini jalur paling mulus untuk Windows.
5. **macOS: tidak ada padanan WSL2.** OrbStack/Colima/Docker Desktop adalah VM Linux tersembunyi
   tanpa hook gVisor resmi; mengopreknya tidak didukung. Untuk isolasi keras di Mac, pakai opsi 2
   atau 3.

### 6A.5 Rekomendasi final untuk pertanyaan platform

- **Desainlah seolah gVisor hanya ada di server Linux.** Itu memang kenyataannya.
- **Jangan kunci keputusan arsitektur pada kemampuan laptop.** Buat `SandboxBackend` pluggable
  sejak Fase 2, dan default dev ke Docker/Process.
- **Untuk dev dari macOS:** pakai Docker biasa (`runc`). Untuk menguji isolasi keras, arahkan ke
  **remote Linux CI/staging** atau gunakan **managed sandbox**.
- **Untuk dev dari Windows:** gunakan **WSL2 + Docker di dalam WSL2**, di situ gVisor bisa dipasang.
- **Untuk produksi:** Linux + gVisor (self-host), atau managed microVM bila on-prem bukan syarat.

Sumber tambahan: https://gvisor.dev/docs/architecture_guide/platforms/ ·
https://gvisor.dev/docs/user_guide/quick_start/docker/ · https://docs.docker.com/desktop/features/wsl/

---

## 7. Arsitektur yang direkomendasikan

### 7.1 Bentuk keseluruhan

```
React (Monaco sel)                     ← UI notebook: Python / SQL / Markdown
  │  POST /api/v1/notebooks/{id}/execute
  ▼
FastAPI NotebookService                ← auth Nova (JWT→sid→Redis), audit, RBAC
  │  (a) sel SQL  → QueryService yang sudah ada
  │  (b) sel Python → kernel manager
  ▼
Kernel Manager (Nova internal)         ← map notebook_id ↔ kernel_id, lifecycle, culling
  │  REST + WS (Jupyter Kernel Gateway, atau jupyter_client langsung)
  ▼
Container sandbox per-user (gVisor)    ← ipykernel + nova-sdk (shim Snowpark) preinstalled
  │  nova.Session(...)  → asyncmy/pymysql + pyarrow  → FE StarRocks :9030
  ▼
StarRocks 4.1.4                        ← RBAC sebagai user; hasil → Arrow → pandas/polars
```

### 7.2 Sesi bersama (inti permintaan "integrasi ke session")

Shim Python `nova` di dalam kernel, ditulis sebagai paket kecil:

```python
import nova

# Sesi hidup, mewarisi database/schema/role + kredensial dari sesi Nova user.
s = nova.Session(database="analytics", schema="public", role="ANALYST")

df = (s.table("orders")                 # lazy — belum dieksekusi
        .filter("amount > 100")
        .group_by("region")
        .agg({"amount": "sum"}))

df.to_pandas()                          # barulah menghasilkan SQL + eksekusi
df.sql()                                # perlihatkan SQL yang akan dijalankan (transparansi)
```

Implementasi `Session` memakai primitif Nova yang sudah ada:
- **Kredensial**: disuntik saat sesi dibuat. Dua rute: (a) dari `encrypted_password` request
  konteks (bila sesi dibuat lewat HTTP), atau (b) `SessionCredentialProvider.password_for(username)`
  untuk kernel yang lahir dari background. Password **hanya di memori kernel**.
- **Eksekusi**: `asyncmy`/`pymysql` `connect(user=username, password=...)` + `SET ROLE <role>`,
  **persis** `execute_as_user`/`_execute_on` (`modules/query/repository.py:125,174`).
- **Konversi hasil**: `pyarrow` → `pandas`/`polars`. Bila kelak StarRocks punya Flight SQL,
  ganti driver ke `adbc_driver_flightsql` tanpa mengubah API `nova.Session`.
- **`@stage`**: shim **wajib** memakai `prepare_stage_sql` yang sama (`sql_pipeline.py:98`) agar
  `s.sql("SELECT * FROM @stage1.data.csv")` ikut di-rewrite + inject kredensial storage. **Jangan
  menduplikasi logika dialect.**
- **Guard & audit**: jalur Python tidak boleh melewati `guard_user_statement`. Idealnya eksekusi
  yang dihasilkan shim dikirim lewat `QueryService` (in-process call atau internal API),
  sehingga guard, redaksi, dan audit tetap satu sumber kebenaran.

### 7.3 Sel SQL vs sel Python
- **Sel SQL** → teruskan ke `QueryService.execute` yang ada. Nol perubahan.
- **Sel Python** → kirim ke kernel. Di dalam kernel, bila user ingin SQL, panggil `nova.Session`
  (yang pada gilirannya memakai pipeline yang sama).
- **Sesi bersama**: satu kernel per notebook; sel SQL dan sel Python berbagi konteks
  database/schema/role. Ini yang membuatnya terasa seperti worksheet Snowsight.

### 7.4 File `.ipynb` dan `.py`
- Perluas `entry_type` agar mengenali notebook (mis. `file` dengan `content_type` terpisah, atau
  `entry_type="notebook"`), plus key versi yang tidak lagi hardcode `.sql`
  (`modules/workspaces/service.py:56-67`). Migrasi ringan karena metadata di tabel PK StarRocks
  (`CONFIG_WORKSPACE_ENTRIES`, AGENTS.md §7).
- `.ipynb` = format JSON Jupyter (interop: bisa diupload/diunduh).
- `.py` = **notebook sebagai skrip** (ala Marimo / Snowpark worksheet). Ini memenuhi permintaan
  user di gambar kedua (`test.py` + tombol Run). Dua mode bisa berbagi kernel yang sama.
- Frontend: generalisasi `MonacoSqlEditor` menjadi editor multi-bahasa dan tambahkan komponen
  sel (Python/SQL/Markdown) — pola ada di `frontend/src/features/workspaces/index.tsx`.

### 7.5 Pemanfaatan kembali infrastruktur Nova
- **Proses terpisah** sudah jadi preseden: `nova-worker`, `nova-scheduler`. Kernel service bisa
  jadi proses/container ketiga (`nova-kernel`), atau dijalankan sebagai sibling.
- **Redis** sudah dipakai di luar sesi untuk transport; bisa dipakai untuk channel kontrol kernel
  (interrupt/status) tanpa menambah broker.
- **Delegasi kredensial** sudah terbukti di `DelegateExecutor`; jangan bikin mekanisme baru.

---

## 8. Opsi yang TIDAK direkomendasikan (dan alasannya)

| Opsi | Kenapa ditolak |
|---|---|
| **Eksekusi in-process di FastAPI** (seperti `ml_engine` hari ini) | Satu kode user bisa mematikan/mencuri seluruh app. `ml_engine` hanya OK karena modelnya internal, bukan kode user arbitrer. |
| **Pyodide/WASM sebagai jalur utama** | Tanpa socket → tidak ada sesi DB native; kredensial di browser melanggar credential-invisible; tanpa thread; 2 GB. |
| **`exec()` di thread/process di dalam app** | Tidak ada batas FS/network; GIL; tidak scalable; tetap tidak aman. |
| **Per-eksekusi fresh process tanpa state** | Merusak model notebook (variabel antar-sel hilang) kecuali pakai pickle rapuh. |
| **fork Calcite/StarRocks planner untuk "pushdown" Python** | Menempatkan JVM di request path — melanggar invariant (README `:936`). |
| **Simpan password user di `NOVA_SYSTEM`** | Melanggar AGENTS.md §2 (credential-invisible). |
| **Menduplikasi parser `@stage` di shim Python** | Dua sumber kebenaran dialect; pasti drift. Wajib pakai `prepare_stage_sql`. |

---

## 9. Rekomendasi & tahapan

### 9.1 Rekomendasi utama

**Kernel CPython long-lived per-user, di dalam container sandbox yang backend-nya pluggable
(Docker `runc` untuk dev, **gVisor hanya di server Linux** untuk produksi multi-tenant), dikelola
FastAPI lewat protokol kernel Jupyter (via Jupyter Kernel Gateway atau `jupyter_client`), dengan
shim `nova.Session` di atas `asyncmy`+`pyarrow`.**

**Penting (karena dev di macOS/Windows):** gVisor **hanya Linux 5.6+** §6A — jangan kunci
arsitektur pada isolasi lokal. Buat `SandboxBackend` pluggable sejak awal; dev pakai
Docker/Process, produksi pakai gVisor (Linux) atau managed microVM.

Alasan:
1. **Native-feel tertinggi** — interpreter hidup (state antar-sel, DataFrame tetap di RAM) +
   pushdown SQL, persis properti Snowpark §1.
2. **Ekosistem Python penuh** — `ipykernel` menerima semua library (pandas, polars, sklearn, dsb).
3. **Integrasi FastAPI paling bersih** — Kernel Gateway memang dirancang untuk "kernel tanpa UI"
   (REST lifecycle + WS eksekusi), tanpa JupyterHub.
4. **Data tetap di dalam environment Nova** — tidak seperti E2B/Modal, penting untuk enterprise.
5. **Kredensial tetap invisible** — hanya di memori kernel; pakai `SessionCredentialProvider`
   yang sudah ada.
6. **Tidak ada JVM di runtime** — murni Python, sesuai invariant.
7. **`@stage`, guard, audit tetap satu sumber** — shim memanggil `QueryService`/`prepare_stage_sql`.

### 9.2 Alternatif bila self-host isolasi tidak memungkinkan
**E2B atau Modal** (microVM, snapshot state, effort ops rendah) — dengan catatan eksplisit
**data keluar VPC** dan lock-in; harus disetujui sebagai trade-off keamanan/residensi data.
**Bonus:** backend ini **jalan dari macOS/Windows** (SDK memanggil microVM di cloud), jadi
satu-satunya opsi yang memberi isolasi keras **saat dev dari laptop** (§6A.4).

### 9.3 Alternatif format notebook
**Marimo server-side** bila prioritasnya file `.py` yang benar-benar Python-importable dan UX
reaktif. Komprominya: mengadopsi editor/UX Marimo, bukan komponen React Nova sendiri.

### 9.4 Pendorong revisit
- Bila StarRocks 4.1.x mengumumkan **Arrow Flight SQL** → ganti data plane ke `adbc_driver_flightsql`
  (tanpa mengubah API `nova.Session`).
- Bila jumlah user/sesi besar → pertimbangkan **warm pool** sandbox + reverse index `username→sid`.
- Bila tenancy menjadi adversarial → naikkan dari Docker+gVisor ke **Firecracker/Kata**.

### 9.5 Urutan implementasi yang disarankan
1. **Fase 0 (riset/keputusan):** putuskan engine kernel + model isolasi. Dokumen ini adalah bahannya.
2. **Fase 1 (UI + SQL, tanpa Python):** generalisasi editor ke multi-bahasa; tambah tipe file
   `.ipynb`/`.py`; sel Markdown. Belum ada eksekusi Python. Risiko rendah.
3. **Fase 2 (kernel + Python dasar, trusted/internal):** `SandboxBackend` + `DockerBackend` (jalan
   di macOS/Windows via VM Linux), `ipykernel`, tombol Run, stream output. **Uji kontrak backend di
   local; isolasi keras diuji di CI/Linux.** Belum ada `nova.Session`.
4. **Fase 3 (integrasi session — inti permintaan):** shim `nova.Session` + `@stage` + guard + audit.
5. **Fase 4 (isolasi keras + polish):** `GvisorBackend` (Linux only) atau `ManagedBackend`;
   rlimit, timeout/cancel, culling, warm pool, versioning notebook.

---

## 10. Risiko terbuka

| # | Risiko | Mitigasi |
|---|---|---|
| 1 | **Isolasi host kode arbitrer** — masalah baru, tidak ada preseden di Nova | `SandboxBackend` pluggable; Docker+gVisor di produksi Linux; rlimit; network allowlist; audit |
| 1b | **gVisor tidak jalan di macOS/Windows**; dev tidak bisa menguji isolasi keras lokal (§6A) | Desain backend pluggable; dev Docker/Process; uji isolasi di CI/Linux/WSL2; atau managed sandbox |
| 2 | **Tidak ada timeout/cancel query** hari ini | Tambah timeout + cancel handle ke `QueryService` sebelum kernel daring |
| 3 | **DataFrame besar** menabrak memori kernel | rlimit + arahkan ke pola `to_pandas()` eksplisit + pagar baris |
| 4 | **Password di memori kernel** (proses long-lived) | Tidak pernah disk/log; rotasi saat sesi berakhir; container di-recycle saat sesi berakhir |
| 5 | **`[PERLU VERIFIKASI]` Flight SQL StarRocks** | Pakai MySQL protocol+pyarrow sebagai default; jadikan Flight SQL revisit-trigger |
| 6 | **SCAN linear `SessionCredentialProvider`** saat sesi banyak | Reverse index `username→sid` bila skala naik |
| 7 | **Drift dialect `@stage`** bila shim tidak memakai `prepare_stage_sql` | Wajib lewat pipeline yang sama; larang duplikasi |
| 8 | **Scope creep** — ini E-decision baru (README `:846`) | Kunci ke subset: kernel Python + sesi bersama; tunda UDF-in-engine, multi-user sharing |

---

## 11. Matriks keputusan ringkas

| Pertanyaan | Jawaban |
|---|---|
| Engine Python? | **CPython proses via `ipykernel`** (Jupyter protocol). WASM ditolak. |
| Cara embed ke FastAPI? | **Jupyter Kernel Gateway** (REST+WS) atau `jupyter_client` langsung. |
| Isolasi? | **`SandboxBackend` pluggable.** Dev macOS/Windows: Docker/Process. Produksi: **gVisor (Linux 5.6+ only)** atau managed microVM. |
| Dev dari Mac? | **Tidak bisa gVisor lokal.** Docker biasa + CI/Linux untuk uji isolasi, atau managed sandbox (E2B/Modal). |
| Dev dari Windows? | **WSL2 + Docker di dalam WSL2** — gVisor bisa dipasang di situ. |
| State antar-sel? | **Kernel long-lived per-user** (native), opsional snapshot saat culling. |
| Data plane? | **MySQL protocol + `pyarrow`** hari ini; **ADBC/Flight SQL** bila StarRocks mendukung. |
| Bentuk shim? | `nova.Session` (lazy DataFrame → SQL pushdown) yang memanggil `QueryService`/`prepare_stage_sql`. |
| Kredensial? | Hanya di memori kernel; via `SessionCredentialProvider`; audit ke `NOVA_SYSTEM.AUDIT.LOG`. |
| Format file? | `.ipynb` (JSON) + `.py` (skrip); perluas `CONFIG_WORKSPACE_ENTRIES` & key versi. |
| JVM di runtime? | **Tidak** — invariant tetap. |

---

## 12. Indeks sumber

**Snowpark / Snowflake**
- Snowpark Python index: https://docs.snowflake.com/en/developer-guide/snowpark/python/index
- Creating a session: https://docs.snowflake.com/en/developer-guide/snowpark/python/creating-session

**Jupyter**
- Kernel messaging: https://jupyter-client.readthedocs.io/en/latest/messaging.html
- Server operators/security: https://jupyter-server.readthedocs.io/en/latest/operators/public-server.html
- Kernel Gateway: https://jupyter-kernel-gateway.readthedocs.io/en/latest/
- Enterprise Gateway: https://jupyter-enterprise-gateway.readthedocs.io/en/latest/
- jupyter-server-proxy: https://jupyter-server-proxy.readthedocs.io/en/latest/

**Marimo**
- Apps: https://docs.marimo.io/guides/apps/
- Watching: https://docs.marimo.io/guides/editor_features/watching/
- WASM: https://docs.marimo.io/guides/wasm/

**WASM / browser**
- Pyodide API: https://pyodide.org/en/stable/usage/api/python-api.html
- WebContainers: https://webcontainers.io/

**Sandbox cloud**
- E2B: https://docs.e2b.dev
- Modal Sandboxes: https://modal.com/docs/guide/sandbox
- Daytona: https://www.daytona.io/docs/en/

**Isolasi self-host**
- gVisor: https://gvisor.dev/docs/
- Firecracker: https://firecracker-microvm.github.io/
- nsjail: https://github.com/google/nsjail
- bubblewrap: https://github.com/containers/bubblewrap

**Data plane**
- Arrow Flight SQL: https://arrow.apache.org/docs/format/FlightSql.html
- ADBC: https://arrow.apache.org/adbc/current/index.html
- Ray Core: https://docs.ray.io/en/latest/ray-core/walkthrough.html
- StarRocks docs index: https://docs.starrocks.io/llms.txt

**Internal Nova (baseline)**
- `backend/app/core/deps.py:15` · `core/redis.py:34` · `core/database.py:57` · `core/security.py:22`
- `backend/app/modules/query/service.py:227` · `modules/query/repository.py:125` · `modules/query/sql_pipeline.py:98`
- `backend/app/modules/task_orchestration/credentials.py:62` · `worker/__main__.py`
- `backend/app/modules/workspaces/service.py:51-67` · `common/nova_system.py:11-49`
- `frontend/src/features/workspaces/index.tsx`
- `README.md:846,933,936` · `docs/roadmap-snowflake-parity.md:151` · `AGENTS.md`
