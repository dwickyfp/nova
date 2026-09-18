# Menjalankan Nova

Panduan ini menjelaskan cara menjalankan seluruh environment development Nova:

- Infrastructure: StarRocks, Redis, dan object storage
- Backend FastAPI: port `8000`
- Frontend React/Vite: port `5173`

## Prasyarat

Pastikan software berikut sudah tersedia:

- Docker dan Docker Compose
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js
- [pnpm](https://pnpm.io/)

Periksa instalasi:

```bash
docker --version
docker compose version
python3 --version
uv --version
node --version
pnpm --version
```

Semua perintah di bawah diasumsikan dijalankan dari root project:

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova
```

---

## 1. Jalankan Infrastructure

Backend Nova membutuhkan StarRocks, Redis, dan object storage sebelum dapat
berjalan.

```bash
cd docker
cp .env.example .env
docker compose -f docker-compose-engine.yml up -d
```

Tunggu hingga seluruh container sehat:

```bash
docker compose -f docker-compose-engine.yml ps
```

Container utama yang seharusnya aktif:

- `nova-starrocks-fe`
- `nova-starrocks-be`
- `nova-minio`
- `nova-redis`

Pantau proses startup bila diperlukan:

```bash
docker compose -f docker-compose-engine.yml logs -f
```

Tekan `Ctrl+C` untuk keluar dari tampilan log. Container akan tetap berjalan.

### Verifikasi infrastructure

```bash
curl http://localhost:8030/api/health
curl http://localhost:8040/api/health
```

Alamat development:

| Service | Address |
|---|---|
| StarRocks FE | `http://localhost:8030` |
| StarRocks MySQL | `localhost:9030` |
| StarRocks BE | `http://localhost:8040` |
| Object Storage API | `http://localhost:9000` |
| Object Storage Console | `http://localhost:9001` |
| Redis | `localhost:6379` |

---

## 2. Jalankan Backend

Buka terminal baru:

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/backend
```

### Siapkan environment

Untuk instalasi pertama:

```bash
cp .env.example .env
uv sync
```

Pastikan nilai pada `backend/.env` sesuai dengan nilai yang digunakan pada
`docker/.env`.

Contoh konfigurasi development:

```dotenv
STARROCKS_HOST=localhost
STARROCKS_FE_MYSQL_PORT=9030
STARROCKS_HTTP_PORT=8030
STARROCKS_ROOT_USER=root
STARROCKS_ROOT_PASSWORD=

REDIS_URL=redis://:nova_redis_2026@localhost:6379/0

SECRET_KEY=change-me-in-local-development
# Boleh kosong untuk development; backend membuat key sementara saat startup.
FERNET_KEY=

S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE
S3_SECRET_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
S3_BUCKET=stages

CORS_ORIGINS=["http://localhost:5173"]
DEBUG=true
LOG_LEVEL=INFO
```

Nilai Redis dan object storage harus sama dengan:

- `REDIS_PASSWORD`
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`

yang terdapat di `docker/.env`.

Untuk menggunakan Fernet key yang persisten:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Salin hasilnya ke `FERNET_KEY` pada `backend/.env`.

> Konfigurasi di atas hanya contoh untuk development lokal. Jangan gunakan
> secret development di production.

### Start FastAPI

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Backend tersedia di:

- API: [http://localhost:8000](http://localhost:8000)
- Health: [http://localhost:8000/health](http://localhost:8000/health)
- Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)

Verifikasi:

```bash
curl http://localhost:8000/health
```

Respons yang diharapkan:

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

Biarkan terminal backend tetap berjalan.

---

## 3. Jalankan Frontend

Buka terminal baru:

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/frontend
```

Install dependency:

```bash
pnpm install
```

Jalankan development server:

```bash
pnpm dev
```

Frontend tersedia di:

[http://localhost:5173](http://localhost:5173)

Vite secara otomatis meneruskan request `/api` ke backend:

```text
http://localhost:5173/api/*
              ↓
http://localhost:8000/api/*
```

Karena itu, backend harus tetap berjalan pada port `8000`.

---

## 4. Jalankan nova-scheduler

`nova-scheduler` adalah proses terpisah (bukan bagian dari FastAPI) yang
menghitung waktu cron/interval, menulis baris `CONFIG_TASK_GRAPH_RUNS` ke
`NOVA_SYSTEM`, lalu mendorong job ke Redis Streams. Proses ini **tidak pernah
mengeksekusi SQL** ke StarRocks.

Buka terminal baru:

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/backend
uv run python -m app.scheduler
```

Prasyarat: StarRocks berjalan dengan tabel `CONFIG_TASK*` (dibuat otomatis saat
backend start), dan Redis dapat dijangkau.

Setting yang dibutuhkan (default sudah cukup untuk development):

```dotenv
REDIS_URL=redis://:nova_redis_2026@localhost:6379/0

SCHEDULER_POLL_INTERVAL_SECONDS=15
SCHEDULER_LEADER_LOCK_KEY=nova:scheduler:leader
SCHEDULER_LEADER_LOCK_TTL_SECONDS=60
# Kosongkan (default) agar scheduler membaca timezone dari engine lewat
# SELECT @@time_zone. Isi hanya untuk override.
# SCHEDULER_ENGINE_TIMEZONE=Asia/Jakarta

TASK_STREAM_KEY=nova:tasks:graph_runs
TASK_STREAM_GROUP=nova-workers
TASK_STREAM_MAXLEN=10000
```

Secara default `SCHEDULER_ENGINE_TIMEZONE` **kosong**, artinya scheduler
menanyakan timezone session ke engine sendiri (`SELECT @@time_zone`). Ini penting:
Nova menulis `created_at` lewat `NOW()` di zona session engine dan membacanya
kembali tanpa tz, jadi nilai dari engine adalah satu-satunya jawaban yang tidak
melenceng dari deployment. Meng-hardcode `UTC` (atau zona lain) di config bisa
menggeser anchor dan membuat task interval tidak pernah due. Isi variabel ini
hanya bila ingin override eksplisit.

Bila variabel ini **diisi**, saat start scheduler memverifikasinya terhadap
`@@time_zone` dan **menolak start** bila tidak cocok (offset seperti `+07:00`
diterima sebagai sinonim zona IANA yang cocok). Override yang salah akan
menggeser semua anchor interval — kelalaian yang menjadi dasar NOVA-39 — jadi
lebih baik gagal saat start daripada diam-diam salah.

Hentikan dengan `Ctrl+C`. Hanya satu instance yang boleh jalan pada saat yang
sama — instance lain menunggu leader-lock dan tidak akan enqueue.

Untuk memverifikasi job masuk stream:

```bash
docker exec -it nova-redis redis-cli -a nova_redis_2026 XLEN nova:tasks:graph_runs
```

---

## 5. Jalankan nova-worker

`nova-worker` adalah proses terpisah yang mengonsumsi job graph-run dari Redis
Streams, mengeksekusi node DAG yang siap, dan memajukan state graph di
`NOVA_SYSTEM`. Eksekusi memakai **delegate-first**: `SUBMIT TASK` dikirim pada
koneksi user pemilik task, sehingga RBAC StarRocks ditegakkan engine.

Buka satu terminal per worker (boleh banyak — mereka berbagi stream lewat
consumer group):

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/backend
uv run python -m app.worker
```

Prasyarat:

- StarRocks berjalan dengan tabel `CONFIG_TASK*` (dibuat otomatis oleh worker
  saat start; kolom `heartbeat_at` ditambahkan lewat migrasi idempoten).
- Redis dapat dijangkau.
- **Pemilik task harus punya sesi login aktif.** Worker mengambil password
  pemilik dari session store (`nova:session:*`) saat eksekusi, hanya di memori,
  lalu membuangnya. Task yang pemiliknya tidak sedang login akan gagal dengan
  error yang jelas — worker tidak pernah jatuh ke koneksi root.

Setting yang dibutuhkan (default sudah cukup untuk development):

```dotenv
REDIS_URL=redis://:nova_redis_2026@localhost:6379/0

TASK_STREAM_KEY=nova:tasks:graph_runs
TASK_STREAM_GROUP=nova-workers

WORKER_NAME=nova-worker
WORKER_TASK_POLL_INTERVAL_SECONDS=1
WORKER_TASK_POLL_TIMEOUT_SECONDS=14400
WORKER_HEARTBEAT_TIMEOUT_SECONDS=120
WORKER_RECONCILE_INTERVAL_SECONDS=30
```

Catatan operasional:

- **At-least-once.** Redis hanya transport; state sebenarnya ada di
  `NOVA_SYSTEM`. Mengirim ulang job yang sama tidak mengeksekusi node dua kali —
  setiap transisi adalah conditional write pada state saat ini.
- **Restart-safe.** Kalau Redis di-flush atau worker mati di tengah node, baris
  `RUNNING` yang heartbeat-nya basi dianggap *abandoned* dan dievaluasi ulang
  oleh reconciler, bukan dipercaya. Tidak ada pekerjaan yang hilang permanen.
- **Tanpa credential di mana pun.** Tidak ada password di `CONFIG_TASK*`,
  stream, atau log; hanya id, state, dan timing.
- Eksekusi dinilai terhadap `information_schema.task_runs` milik engine:
  `SUBMIT TASK` di-follow polling sampai run selesai. Tidak ada completion hook.

### Semantik runtime `CREATE TASK` (NOVA-54)

Task yang dibuat lewat surface Nova (`CREATE TASK … AFTER / FINALIZE / WHEN /
OVERLAP_POLICY / SCHEDULE`) dieksekusi worker dengan aturan berikut. Detail
lengkap + alasan keputusan ada di `docs/08-task-manager.md` § Runtime semantics.

- **`AFTER`** — urutan dependency biasa; join menunggu **semua** parent.
- **`FINALIZE`** — bukan dependency. Berjalan hanya setelah **seluruh dependency
  graph** selesai sukses, dan **di-skip bila graph gagal** (keputusan sadar: ia
  task engine, bukan callback; menjalankannya di atas run gagal akan menulis
  klaim palsu). Kegagalan finalizer sendiri menggagalkan graph.
- **`WHEN`** — false melewati node **dan** turunannya; error evaluasi
  menggagalkan node, bukan diam-diam di-skip.
- **`OVERLAP_POLICY`** — `skip` (default) menolak run baru saat satu run graph
  masih aktif; `queue` menundanya sampai run aktif selesai; `allow` menjalankan
  konkuren. Nilai tak dikenal berperilaku `skip`.

Cara mengamati:

```bash
# Graph run beserta policy overlap-nya (kolom overlap_policy)
# SELECT id, graph_id, state, overlap_policy FROM NOVA_SYSTEM.CONFIG_TASK_GRAPH_RUNS;

# Node run: satu baris per node, termasuk finalizer (skipped bila graph gagal)
# SELECT graph_run_id, task_id, state, error_message FROM NOVA_SYSTEM.CONFIG_TASK_RUNS;

# Audit: GRAPH_RUN_* dan NODE_* (SUCCESS/SKIPPED/FAILED)
# SELECT action, object_name, status FROM NOVA_SYSTEM.AUDIT.LOG ORDER BY event_time DESC LIMIT 20;
```

Untuk memverifikasi worker mengonsumsi:

```bash
docker exec -it nova-redis redis-cli -a nova_redis_2026 XPENDING nova:tasks:graph_runs nova-workers
```

### Rekonsiliasi native (NOVA-37)

Reconciler berjalan **di dalam proses `nova-worker`**, pada cadence yang sama
dengan reconcile cycle (`WORKER_RECONCILE_INTERVAL_SECONDS`, default 30 detik).
Tidak ada proses atau kelas reconciler kedua — `Reconciler` yang sudah ada
diperluas. Setiap pass melakukan tiga hal, berurutan:

1. **Rekonsiliasi state native.** Membaca `information_schema.task_runs` hanya
   untuk node yang berstatus `running` (bukan setiap task), lalu:
   - memajukan node yang native-nya sudah selesai → `success`/`failed`;
   - mengabaikan baris native yang `CREATE_TIME`-nya lebih tua dari
     `started_at` node — baris sisa attempt sebelumnya tidak boleh mem-failkan
     node yang masih in-flight di worker lain;
   - membaca config FE lewat `ADMIN SHOW FRONTEND CONFIG LIKE '%task%'` (bukan
     `SHOW VARIABLES`) untuk `max_task_consecutive_fail_count`, dan
     `information_schema.tasks.SCHEDULE` untuk marker pause/suspend;
   - men-surface **auto-pause hanya pada ambang yang benar** ke
     `NOVA_SYSTEM.AUDIT_LOG` dengan action `TASK_AUTO_PAUSE_SUSPECTED`, supaya
     DAG tidak menggantung diam-diam. Ambang dipenuhi bila:
     - Nova menghitung **10 kegagalan beruntun** (kolom persisten
       `CONFIG_TASKS.consecutive_fail_count`, di-reset ke 0 tiap sukses) — atau
       jumlah yang dilaporkan engine di `ERROR_MESSAGE`; **atau**
     - `SCHEDULE` native menunjukkan marker `PAUSE`/`SUSPEND`.

     Kegagalan tunggal **tidak** memicu alarm auto-pause; kegagalan biasa
     hanya tercatat sebagai `NODE_FAILED`. Ini mencegah alarm fatigue yang
     membuat sinyal auto-pause asli tak terbedakan (NOVA-42).
2. **Run hilang lewat heartbeat.** `Reconciler.abandon_stale_nodes`—dipanggil
   `WorkerService.reconcile_once`—meng-abandon setiap baris `running` yang
   `heartbeat_at`-nya sudah lewat timeout worker
   (`WORKER_HEARTBEAT_TIMEOUT_SECONDS`), di-transisi kondisional ke `abandoned`
   dengan audit `NODE_ABANDONED`. Inilah sinyal run hilang yang durable: worker
   mati berhenti menstempel heartbeat, jadi barisnya ditinggalkan dan graph
   dievaluasi ulang dengan node itu runnable lagi.
3. **Re-enqueue delivery yang hilang** (Redis flush / worker mati).

Bila engine tidak tersedia, pembacaan mengembalikan `UNKNOWN` dan **tidak**
menulis apa pun — kegagalan baca transien tidak boleh disalahartikan sebagai
pekerjaan yang hilang. Dua hal ini khusus dijaga:

- **Hasil kosong belum tentu "run hilang".** Pool koneksi dapat masih membalas
  dari sesi TCP lama sesaat setelah FE mati, sehingga query "sukses" dengan 0
  baris. Karena itu hasil kosong dipercaya sebagai `MISSING` **hanya bila probe
  `SELECT 1` pada koneksi yang sama membuktikan engine hidup**; jika tidak,
  hasilnya `UNKNOWN` dan tidak ada write. Tanpa ini, run yang masih sehat akan
  ditandai `abandoned` saat FE mati (NOVA-43).
- **Kegagalan baca selalu `UNKNOWN`, jangan menebak.** Di FE yang baru,
  `information_schema.task_runs` gagal dengan 1064 pada
  `_statistics_.task_run_history` walau engine tetap melayani statement lain.
  Kegagalan itu **engine-wide dan tidak informatif per task**: ia muncul untuk
  setiap pembacaan `task_runs`, jadi tidak bisa menentukan apakah trace sebuah
  task tertentu hilang. Vonis apa pun darinya akan membuang baris sehat
  (NOVA-43/NOVA-46) — karena itu setiap kegagalan baca dikembalikan sebagai
  `UNKNOWN` dan tidak ada write. Klasifikasi per-surface (`task_run_history` /
  `getTaskRuns`) sengaja **tidak** dipakai.
- **Lost trace diselesaikan lewat jalur heartbeat yang durable.** Karena arsip
  tidak bisa dipercaya, trace yang hilang disettle bukan dari `task_runs`
  melainkan dari state `NOVA_SYSTEM`: `list_stale_task_runs` menemukan node
  `running` yang heartbeat-nya berhenti, `abandon_stale_nodes` men-transisi
  kondisional ke `abandoned` (dengan audit `NODE_ABANDONED`), lalu graph
  di-re-enqueue dari state durable (design §3). `scan` sendiri tetap **pure
  read** — ia hanya melaporkan kandidat, bukan menulis.
  Ini justru skenario "task berjalan saat FE/worker mati" yang dituju AC #2, dan
  tidak bisa menyala pada node sehat karena worker terus menstamp heartbeat.
- **Akuisisi koneksi ikut dijaga.** Kegagalan `db.system_conn()` saat FE tidak
  dapat dijangkau dikembalikan sebagai `UNKNOWN`/map kosong, bukan exception
  yang keluar dari `reconcile_native` (NOVA-44).

Idempoten: dua kali reconcile pada state yang sama tidak mengubah apa pun,
karena setiap transisi adalah conditional write.

Nilai config yang dibaca diverifikasi di engine 4.1.1: `task_runs_ttl_second =
604800` (7 hari) dan `max_task_consecutive_fail_count = 10`. Task periodik
**tidak** perlu di-re-arm setelah restart FE — yang direkonsiliasi hanya run
yang jejaknya hilang.

Operasional: tidak ada setting tambahan yang wajib. Override default 10 dengan
`WORKER_MAX_CONSECUTIVE_FAIL_COUNT` bila deployment memakai nilai FE yang
berbeda dan `ADMIN SHOW FRONTEND CONFIG` tidak dapat dibaca.

---

## 6. Login ke Nova

Buka:

[http://localhost:5173/sign-in](http://localhost:5173/sign-in)

Gunakan user StarRocks:

```text
Username: nova_admin
Password: nova
```

Nova melakukan autentikasi langsung ke StarRocks. Nova tidak memiliki tabel
user terpisah.

Password default `nova_admin` hanya untuk setup development awal dan harus
diganti ketika diminta.

---

## Urutan Startup Harian

Setelah dependency dan file `.env` selesai disiapkan, gunakan terminal berikut.

### Terminal 1 — Infrastructure

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/docker
docker compose -f docker-compose-engine.yml up -d
```

### Terminal 2 — Backend

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/backend
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Terminal 3 — Frontend

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/frontend
pnpm dev
```

### Terminal 4 — nova-scheduler (opsional)

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/backend
uv run python -m app.scheduler
```

### Terminal 5 — nova-worker (opsional)

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/backend
uv run python -m app.worker
```

Boleh dijalankan beberapa instance sekaligus; semuanya berbagi stream yang sama.

Kemudian buka:

```text
http://localhost:5173
```

---

## Menghentikan Nova

Hentikan frontend dan backend dengan `Ctrl+C` pada terminal masing-masing.

Hentikan infrastructure:

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/docker
docker compose -f docker-compose-engine.yml down
```

Perintah tersebut mempertahankan volume dan data.

Untuk menghapus container beserta seluruh volume development:

```bash
docker compose -f docker-compose-engine.yml down -v
```

> `down -v` menghapus metadata StarRocks, data tabel, object storage, dan data
> Redis. Gunakan hanya jika benar-benar ingin reset total.

---

## Development Checks

### Backend

```bash
cd backend
uv run pytest
uv run ruff check .
```

Regenerasi parser ANTLR (fondasi NOVA-54; Java hanya dipakai saat generate,
runtime tetap Python murni):

```bash
bash backend/scripts/generate_grammar.sh   # unduh antlr-4.13.2 jika belum ada
cd backend && uv run python scripts/check_grammar_drift.py
```

Commit hasil generate bersama perubahan `.g4`. CI `grammar-drift` menjalankan
ulang generator dan gagal bila artefak yang di-commit tidak identik dengan
hasil generate, atau bila ada suntingan di luar blok `NOVA-BEGIN`/`NOVA-END`.

Test khusus `nova-scheduler` (unit, tanpa engine/Redis):

```bash
cd backend
uv run pytest tests/unit/test_task_schedule.py \
              tests/unit/test_task_scheduler_tick.py \
              tests/unit/test_task_scheduler_leader_lock.py
```

Test integrasi scheduler (butuh StarRocks + Redis; skip otomatis bila tidak ada):

```bash
cd backend
uv run pytest tests/integration/test_task_scheduler.py -v
```

Test khusus `nova-worker` (unit, tanpa engine/Redis):

```bash
cd backend
uv run pytest tests/unit/test_task_orchestration_dag.py \
              tests/unit/test_task_orchestration_worker.py
```

Test integrasi worker (butuh StarRocks + Redis; skip otomatis bila tidak ada).
Membuktikan delegate-first RBAC, DAG `A→B→[C,D]`, semantik gagal/skip/suspend,
idempotensi, dan restart-safety:

```bash
cd backend
uv run pytest tests/integration/test_task_worker.py -v
```

Test rekonsiliasi native (unit, tanpa engine/Redis):

```bash
cd backend
uv run pytest tests/unit/test_task_reconciler.py
```

Test integrasi rekonsiliasi (butuh StarRocks; skip otomatis bila tidak ada).
Membuktikan run-hilang ditandai `abandoned` (bukan sukses), pembacaan config
lewat `ADMIN SHOW FRONTEND CONFIG` (dan toleran bila engine mati), serta
idempotensi dua pass.

Suite ini **self-contained**: fixture-nya membuat `NOVA_SYSTEM.AUDIT_LOG`
sendiri, jadi tidak perlu `seed_engine.sh` lebih dulu. Menunjuk engine test
yang sudah jalan lewat env var:

```bash
cd backend
NOVA_ORCH_SR_PORT=29030 NOVA_ORCH_SR_HOST=127.0.0.1 \
  uv run pytest tests/integration/test_task_reconciler.py -v
```

Bila engine belum jalan, naikkan stack test lebih dulu (lalu jalankan perintah
di atas tanpa env var, atau dengan env var yang sesuai):

```bash
cd backend
docker compose -f docker-compose.test.yml up -d --wait
uv run pytest tests/integration/test_task_reconciler.py -v
```

Catatan: suite test integrasi lain yang menulis `NOVA_SYSTEM.AUDIT_LOG`
(mis. `test_tasks_rbac_connection.py`) juga membuat tabel itu sendiri. Untuk
suite warisan yang masih mengandalkan environment pra-seed, jalankan
`bash tests/integration/seed_engine.sh` setelah stack naik.

### Frontend

```bash
cd frontend
pnpm build
pnpm lint
```

Test frontend menggunakan Playwright. Install Chromium sebelum menjalankannya:

```bash
pnpm test:browser:install
pnpm test
```

---

## Troubleshooting

### Port sudah digunakan

Periksa proses yang memakai port:

```bash
lsof -i :5173
lsof -i :8000
lsof -i :9030
```

Frontend Nova harus menggunakan `5173`, sedangkan backend menggunakan `8000`.

### Backend gagal terhubung ke StarRocks

Periksa container:

```bash
cd docker
docker compose -f docker-compose-engine.yml ps
docker logs nova-starrocks-fe --tail 100
docker logs nova-starrocks-be --tail 100
```

StarRocks membutuhkan waktu lebih lama daripada service lain ketika startup
pertama.

### Backend gagal terhubung ke Redis

Pastikan password di `backend/.env` sama dengan `REDIS_PASSWORD` pada
`docker/.env`.

Format URL Redis dengan password:

```dotenv
REDIS_URL=redis://:PASSWORD@localhost:6379/0
```

### Login menghasilkan network error

Periksa backend:

```bash
curl http://localhost:8000/health
```

Kemudian pastikan frontend dijalankan melalui `pnpm dev`, karena proxy `/api`
dikonfigurasi oleh Vite.

### Perubahan frontend tidak tampil

Hentikan lalu jalankan kembali Vite:

```bash
pnpm dev
```

Jika dependency berubah:

```bash
pnpm install
```

### Reset environment lokal

```bash
cd docker
docker compose -f docker-compose-engine.yml down -v
docker compose -f docker-compose-engine.yml up -d
```

Perintah tersebut menghapus seluruh data development sebelumnya.
