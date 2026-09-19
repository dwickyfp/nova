# Riset: Opsi Arsitektur Agentic Assistant di Atas Stack Nova

> **Workstream:** NOVA-61 §1 (arsitektur) + §5 (skill SQL). **Pemilik:** Nova DW Research & Roadmap.
> **Tanggal akses sumber web:** 2026-09-18. **Engine:** StarRocks 4.1.4.
> **Sifat dokumen:** riset + opsi, **bukan** implementasi. Tidak ada kode produksi yang disentuh.
> **Aturan:** tidak ada secret/credential di dokumen ini; tempat nilai sensitif memakai placeholder.

---

## Ringkasan (5 bullet)

- **Tiga opsi arsitektur layak**: (A) assistant di dalam FastAPI yang memanggil provider LLM yang
  sudah ada di `NOVA_SYSTEM.CONFIG_AI_PROVIDERS`; (B) agent runtime sidecar terpisah mengikuti pola
  `nova-scheduler`/`nova-worker`; (C) memakai framework agent pihak ketiga (LangGraph / Pydantic AI
  / OpenAI Agents SDK — semua MIT).
- **Jalur tool `query_execute` harus melewati `QueryService.execute`**, tidak pernah langsung ke
  port `9030`. Pipeline itu menegakkan guard + audit + redaksi kredensial; ini terverifikasi di kode
  (`service.py:219`, `:343`, `:363-396`).
- **Delegate-first wajib dipertahankan**: tool harus berjalan di **koneksi user**, bukan root.
  Pola ini sudah dipakai NOVA-23 untuk task (`nova-23-task-orchestration-design.md:210-243`).
- **State percakapan harus hidup di `NOVA_SYSTEM`** (Single Database), tanpa credential-shaped field.
  Percakapan yang menyimpan SQL hasil `@stage` berisiko memuat kredensial yang sudah di-redaksi —
  ini titik rawan yang harus diuji, bukan diasumsikan aman.
- **Skill SQL dari NOVA-59 paling aman sebagai file markdown yang di-retrieve**, bukan di-embed ke
  system prompt. Embedding = panjang konteks + staleness; retrieval = satu sumber kebenaran.
  Ringkasan yang di-commit boleh, selama invariant tetap ditegakkan di *runtime*, bukan di prompt.

---

## 1. Stack Nova saat ini (fakta kode, untuk dasar opsi)

| Lapisan | Fakta | Sumber |
|---|---|---|
| Backend | FastAPI **modular monolith**; 26 modul di `backend/app/modules/` | `backend/app/main.py:124-148` |
| API prefix | `/api/v1`; query router di `/api/v1/query` | `backend/app/main.py:124-126` |
| LLM provider | Sudah ada CRUD + test connection provider LLM di `NOVA_SYSTEM.CONFIG_AI_PROVIDERS` / `CONFIG_AI_MODELS`; key disimpan terenkripsi, dimask saat keluar backend | `backend/app/modules/ai_ml/service.py:46-99, 112-118` |
| Query pipeline | `QueryService.execute` → `guard_user_statement` → `parse_sql` → `prepare_stage_sql` (translate + inject) → `QueryRepository.execute_as_user` → audit | `backend/app/modules/query/service.py:176-396` |
| Guard | `guard_sql`, `is_destructive_sql`, `is_unscoped_mutation`, `redact_sql_credentials` | `backend/app/common/sql_guard.py` |
| Redaksi | Nilai credential-shaped disubstitusi `***`; gagal-tertutup (`CredentialsRedactionError`) | `sql_guard.py:471-535` |
| Audit | `write_audit_log` menulis ke `NOVA_SYSTEM.AUDIT_LOG` | `backend/app/common/audit.py:10-58` |
| Audit schema | `AUDIT_LOG` punya `sql_text`, `rewritten_sql`, `status`, `session_id`, `user_name`, `duration_ms`, `rows_affected` | `docker/init-nova.sql:311-348` |
| Koneksi user | `get_user_connection` membuka koneksi sebagai user terautentikasi | `backend/app/core/deps.py:75-86` |
| Dialect | Pipeline di `backend/app/modules/query/dialect/` (parser, translator, injector, detector, ml_model) | `backend/app/modules/query/dialect/` |
| Sidecar | Sudah ada pola proses terpisah: `nova-scheduler`, `nova-worker`, Redis Streams | `backend/app/scheduler/`, `backend/app/worker/`, `docs/specs/nova-23-task-orchestration-design.md:369` |
| Frontend | React 19 + Vite + TanStack Query/Router, Monaco, shadcn/ui + Tailwind, Zustand, sonner | `frontend/package.json` |
| ML control plane | MLflow diadopsi sebagai registry (R1) — **control plane only**, sidecar | `docs/roadmap-snowflake-parity.md:183-304` |

**Konsekuensi:** Nova tidak mulai dari nol. Provider LLM, pipeline SQL, guard, audit, dan pola sidecar
sudah ada. Pertanyaan arsitektur yang tersisa adalah *di mana loop agent berjalan* dan *bagaimana
tool dieksekusi dengan aman*.

---

## 2. Opsi (a): Assistant di dalam FastAPI

**Bentuk:** modul baru `backend/app/modules/assistant/` dengan router `/api/v1/assistant`. Loop agent
berjalan di request path FastAPI; memanggil provider dari `CONFIG_AI_PROVIDERS` via `ai_ml_service`.

**Sisi positif**
- Tidak ada proses/datastore baru; satu deployment.
- Reuse langsung `ai_ml_service.get_provider_api_key()` (`ai_ml/service.py:94-99`) — key tetap
  backend-only dan tidak pernah ke UI.
- Tool `query_execute` bisa memanggil `QueryService.execute` in-process, sehingga guard/Audit/redaksi
  otomatis berlaku.
- Auth, session, `encrypted_password` sudah tersedia via `get_current_user`.

**Sisi negatif / risiko**
- Loop agent = streaming panjang; menahannya di worker FastAPI memakan koneksi dan memperumit timeout.
- Latensi LLM + multi-iteration tool call bersaing dengan pool koneksi user.
- Scaling agent = scaling FastAPI; tidak bisa dikalibrasi terpisah.

**Effort: S–M.** Kandidat terbaik untuk **v1**.

---

## 3. Opsi (b): Agent runtime sidecar (pola `nova-scheduler`/`nova-worker`)

**Bentuk:** proses `nova-agent` terpisah. FastAPI menjadi *control plane* (menerima pesan, menulis
state, mem-publish job ke Redis Streams); sidecar menjalankan loop agent dan memanggil kembali Nova
untuk eksekusi SQL.

**Sisi positif**
- Isolasi failure: LLM lambat/error tidak menahan request path inti (mengikuti argumen MLflow
  acceptance criterion 6, `roadmap-snowflake-parity.md:273-275`).
- Scaling/observability terpisah; bisa di-deploy/di-restart sendiri.
- Pola sudah terbukti di Nova untuk task (D9.2), jadi tidak memperkenalkan pola baru.

**Sisi negatif / risiko**
- **Delegate-first menjadi lebih sulit.** Sidecar tidak memegang password user. Untuk menjalankan SQL
  atas nama user, sidecar harus meminta FastAPI mengeksekusi (callback), atau memegang kredensial
  sementara — yang terakhir melanggar invariant. Desain yang benar: sidecar **tidak** menyentuh
  StarRocks; ia memanggil endpoint internal Nova yang sudah memegang koneksi user.
- Kompleksitas operasional naik (proses, health, queue, idempotensi).
- State percakapan harus disinkronkan antara control plane dan sidecar.

**Effort: M–L.** Layak untuk **v2** setelah v1 membuktikan pola.

---

## 4. Opsi (c): Framework agent pihak ketiga

| Framework | Versi stabil (2026-09-18) | Lisensi | Streaming tool-call | HITL approval | Catatan maturity |
|---|---|---|---|---|---|
| **LangGraph** | `langgraph` 1.2.11 (PyPI, 2026-08-11) | **MIT** | Ya | Ya — `interrupt()` + `Command` resume | Paling eksplisit untuk interrupt/resume; ekosistem terbesar (~41.9k star) |
| **Pydantic AI** | 2.45.0 (2026-09-18) | **MIT** | Ya | Ya — deferred tools / HITL | Typed-first, gerak cepat, sudah v2; cocok dengan Pydantic Nova |
| **OpenAI Agents SDK** | 0.22.3 (2026-09-17) | **MIT** | Ya | Ya — halaman HITL first-class | Masih 0.x tapi sangat aktif; tidak vendor-locked (bisa model non-OpenAI) |
| **CrewAI** | 1.15.22 (2026-09-16) | MIT (dari teks lisensi) | Ya | Ya | Abstraksi tinggi role/crew; kontrol node lebih rendah |
| **Microsoft AutoGen** | `autogen-agentchat` 0.7.5 (2025-09-30) | **Repo LICENSE = CC-BY-4.0** (GitHub metadata), package PyPI = MIT | Ya | Ya | **Stale** (commit terakhir ~2026-04-06) + **konflik lisensi CC-BY-4.0** → jangan adopsi tanpa review legal |
| **LlamaIndex** | 0.14.24 (2026-08-19) | MIT | Ya | Ada di modul agent | Platform RAG/dokumen; orkestrasi agent hanya satu modul |

Sumber: GitHub API `/repos`, `/releases/latest`, raw `LICENSE`, dan PyPI JSON untuk setiap paket;
semua diakses **2026-09-18**. Keyakinan: tinggi untuk versi/lisensi, **sedang** untuk klaim
streaming/HITL per framework (dari dokumentasi masing-masing; granularitas berbeda).

**Rekomendasi (opini, bukan fakta):** jika memakai framework, **LangGraph (MIT)** atau **Pydantic AI
(MIT)**. LangGraph menang pada model interrupt/resume yang eksplisit (persis kebutuhan approval tool);
Pydantic AI menang pada integrasi tipe dan rendahnya dependensi. **Hindari AutoGen** sampai status
lisensi dan maintenance jelas.

**Peringatan lock-in:** framework agent mengubah bentuk state percakapan dan tool contract. Untuk
Nova yang memprioritaskan format terbuka dan menghindari lock-in, opsi (a) dengan loop sendiri
(kecil) lebih mudah dipertahankan daripada framework besar — kecuali kebutuhan multi-agent/subgraph
nyata muncul.

---

## 5. Matriks opsi arsitektur

Kolom **Rekomendasi** adalah opini yang ditandai, bukan fakta.

| Opsi | Effort | Risiko utama | Dependensi | Rekomendasi |
|---|---|---|---|---|
| **(a) In-FastAPI, loop sendiri, reuse `CONFIG_AI_PROVIDERS`** | **S** | streaming panjang menahan worker; scaling menyatu dengan API | `ai_ml_service`; `QueryService`; state baru di `NOVA_SYSTEM` | **ADOPT untuk v1** — paling sedikit komponen baru, guard/audit otomatis berlaku |
| **(b) Sidecar `nova-agent`** | **M–L** | delegate-first sulit; kompleksitas ops; sinkronisasi state | Redis Streams; pola `nova-scheduler`/`nova-worker`; endpoint internal | **COBA di v2** setelah v1 stabil; hanya jika beban agent terbukti mengganggu API |
| **(c) Framework — LangGraph / Pydantic AI (MIT)** | **M** | lock-in bentuk state & tool contract; upgrade framework memaksa migrasi | paket baru (MIT); model runtime baru | **TAHAN** sampai kebutuhan multi-agent nyata; jika diadopsi, pilih LangGraph atau Pydantic AI |
| **(c-neg) AutoGen (CC-BY-4.0 + stale)** | — | lisensi tidak jelas untuk komersial; maintenance mandek | — | **JANGAN** — sampai review legal |
| **Embed LLM via StarRocks `AI_*` functions (bukan assistant)** | **S** | bukan agentic (tidak ada loop/tool/consent); satu panggilan tanpa konteks kaya | fungsi `AI_*` yang sudah ada | **BUKAN alternatif** — berguna sebagai tool di dalam opsi (a), bukan pengganti assistant |

---

## 6. Skill SQL (NOVA-59) sebagai konteks assistant

NOVA-59 menghasilkan `docs/sql_docs/` (dialect, `@stage`, ML DDL, AI functions, guardrails). Saat
penulisan dokumen ini, direktori `docs/sql_docs/` **belum ada di branch** — jadi desain di bawah
adalah rancangan, bukan deskripsi isi yang sudah terverifikasi.

Tiga pendekatan:

| Pendekatan | Cara kerja | Trade-off | Rekomendasi |
|---|---|---|---|
| **A. Retrieval dari repo** | Assistant mencari file `docs/sql_docs/*.md` relevan per pertanyaan (keyword/embedding), menyuntik potongan ke konteks | Konteks selalu segar (satu sumber = repo); butuh langkah retrieval; risiko memuat dokumen yang tidak relevan | **UTAMA** |
| **B. Embed penuh ke system prompt** | Seluruh `docs/sql_docs/` dimasukkan ke prompt | Sangat panjang → mahal + mengorbankan ruang untuk konteks percakapan; **stale** setiap doc berubah; sulit diaudit | **JANGAN** |
| **C. Ringkasan yang di-commit** | Ringkasan padat di-commit sebagai "skill" statis | Pendek dan deterministik; mudah basi; harus di-regenerate manual | **PELENGKAP** — hanya untuk invariant stabil yang langka berubah |

**Invariant tidak boleh bergantung pada prompt.** Dokumen skill boleh *menginstruksikan* assistant
untuk tidak membuat `DROP ROLE ACCOUNTADMIN`, tetapi **penegakan tetap di `sql_guard.py`**. Prompt
adalah *advisory*; guard adalah *enforcement*. Tool `query_execute` wajib melewati pipeline yang sama
terlepas dari apa yang dikatakan skill. Ini mencegah "skill mendikte SQL yang melanggar invariant".

**Mencegah staleness:** retrieval sebaiknya menyertakan `git` revision/commit dari dokumen, dan
regenerasi ringkasan (opsi C) masuk checklist saat `docs/sql_docs/` berubah. Tanpa itu, assistant
dapat mengutip aturan yang sudah usang.

**`AGENTS.md` sebagai padanan**: CoCo memakai `AGENTS.md` per workspace sebagai instruksi persisten.
Nova sudah punya `AGENTS.md` di root repo — tetapi itu ditujukan untuk *kontributor agent*, bukan
untuk assistant end-user. Jika Nova mengadopsi pola ini, **jangan** memakai file yang sama; buat
permukaan terpisah agar instruksi engineering tidak bocor ke percakapan user.

---

## 7. Penyimpanan state percakapan tanpa melanggar "Single Database"

**Aturan:** semua state persisten di `NOVA_SYSTEM` (`AGENTS.md` §3). Tidak ada SQLite/PostgreSQL.

Usulan bentuk (nama tabel & kolom adalah **usulan**, bukan existing):

- `NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS` — 1 baris per percakapan
  - `thread_id` (PK), `user_name`, `workspace_file_id` (opsional), `title`, `created_at`,
    `updated_at`, `archived` (bool)
- `NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES` — 1 baris per pesan
  - `message_id` (PK), `thread_id`, `role` (`user`/`assistant`/`tool`), `content` (TEXT),
    `tool_name`, `tool_status`, `created_at`, `sequence`
- `NOVA_SYSTEM.CONFIG_ASSISTANT_TOOL_PERMISSIONS` — state consent
  - lihat dokumen `agentic-assistant-query-tool-permissions.md` untuk skema & alasan

**Konsekuensi kredensial (wajib):**
- Riwayat percakapan **tidak boleh** memuat password, token, API key, connection string, atau
  credential-shaped value. SQL yang disimpan harus bentuk yang **sudah di-redaksi** oleh
  `redact_sql_credentials`, bukan `engine_sql`.
- **Titik rawan yang harus diuji, bukan diasumsikan:** assistant dapat menerima kembali SQL hasil
  `@stage` yang membawa kredensial jika ia menyalin dari sumber yang salah. Karena itu, hanya
  `QueryResult.executed_sql` (yang sudah di-redaksi di `repository.py:68-69`) yang boleh masuk state.
- `PreparedSQL.engine_sql` dan `QueryResult` dengan credential tidak boleh pernah diserialisasi ke
  state percakapan. Ini invariant yang butuh test eksplisit.
- Tool result (rows) juga tidak boleh memuat kolom yang berisi secret — perlu redaksi nilai, bukan
  hanya nama kolom.

---

## 8. Konfirmasi jalur `query_execute` (dengan file:line)

**Pernyataan:** tool `query_execute` **harus** lewat `QueryService.execute` / `POST /api/v1/query/execute`,
**tidak pernah** langsung ke port `9030` (StarRocks FE MySQL).

Jalur yang menegakkan jaminan:

| Jaminan | Lokasi |
|---|---|
| Guard + konfirmasi destruktif | `backend/app/modules/query/service.py:218-231` (`guard_user_statement`), `sql_pipeline.py:68-86` |
| Deteksi @stage → translate | `service.py:262-321`, `sql_pipeline.py:98-178` |
| Inject kredensial (hanya ke statement engine) | `sql_pipeline.py:134-136`, `dialect/injector.py:26-52` |
| Redaksi sebelum keluar proses | `service.py:343` (`redact_for_output`), `repository.py:68-69` (constructor `QueryResult`) |
| Eksekusi sebagai user (RBAC) | `service.py:345-353` (`execute_as_user`), `repository.py:125-172` |
| Audit sukses | `service.py:363-378` |
| Audit gagal engine | `service.py:380-396` |
| Audit pra-engine (guard/t@stage ditolak) | `service.py:398-451` |
| Sanitasi response | `router.py:8`, `router.py:65-69` (`SanitizingJSONResponse`) |

**Kesimpulan:** jalur ini sudah menegakkan guard + audit + redaksi kredensial. Tool assistant yang
memanggil `QueryService.execute` mewarisi seluruh jaminan itu gratis. Tool yang membuka koneksi
sendiri **kehilangan semuanya** tanpa terlihat — karena itu dilarang.

**Catatan penting:** `sanitizing response` (NOVA-21) sudah diperbaiki sehingga jalur error pun
diredaksi. Itu berarti tool assistant juga tidak boleh membuat jalur error sendiri yang melewati
`SanitizingJSONResponse`.

---

## 9. Yang belum terverifikasi / belum diketahui

1. **Isi `docs/sql_docs/` belum ada** di branch saat riset — desain skill §6 bersifat rancangan.
2. **Apakah `QueryService.execute` cukup untuk kebutuhan agent** (mis. multi-statement, streaming
   hasil baris demi baris) — belum dianalisis; `execute_statements` mengembalikan list, bukan stream.
3. **Bentuk event streaming** yang akan dipakai assistant (SSE vs WebSocket vs fetch stream) —
   diputuskan di workstream UI/UX, bukan di sini.
4. **Beban nyata** loop agent terhadap pool koneksi FastAPI — belum diukur. Ini yang menentukan
   apakah opsi (b) sidecar diperlukan.
5. **Apakah ada endpoint internal Nova yang cocok** sebagai callback sidecar (opsi b) yang sudah
   memegang koneksi user — belum diidentifikasi.
6. **Kebijakan retensi** riwayat percakapan (berapa lama disimpan, apakah bisa dihapus user) —
   keputusan produk, belum ditetapkan.
7. **`StarRocks/mcp-server-starrocks` sebagai jalur alternatif** — ada (Apache-2.0, v0.4.0
   2026-05-05), tetapi melewati pipeline Nova (tidak ada guard/audit Nova) dan membuka koneksi
   sendiri, sehingga **tidak cocok** dengan invariant Nova. Dicatat sebagai "belum dianalisis lebih
   jauh", bukan rekomendasi.

---

## 10. Keputusan yang dibutuhkan dari team lead (workstream ini)

1. **Opsi arsitektur mana untuk v1** — rekomendasi: **(a) in-FastAPI dengan loop sendiri**, sidecar
   ditunda ke v2. Setuju/tidak?
2. **Apakah framework agent diizinkan?** Jika ya, LangGraph atau Pydantic AI (keduanya MIT); jika
   tidak, loop sendiri. Ini memengaruhi jumlah dependensi baru secara permanen.
3. **Bentuk skill SQL** — rekomendasi: retrieval dari repo (opsi A) sebagai utama, ringkasan
   di-commit (opsi C) hanya untuk invariant stabil. Setuju?

---

## Provenance

| Klaim | Sumber |
|---|---|
| Peta router & prefix `/api/v1` | `backend/app/main.py:124-148` |
| Provider LLM di `CONFIG_AI_PROVIDERS` + masking key | `backend/app/modules/ai_ml/service.py:46-99,112-118` |
| Pipeline guard→translate→inject→execute→audit | `backend/app/modules/query/service.py:176-451`; `sql_pipeline.py` |
| Guard & redaksi terverifikasi | `backend/app/common/sql_guard.py:296-360,471-535` |
| `QueryResult` meredaksi `executed_sql` di constructor | `backend/app/modules/query/repository.py:28-78` |
| Eksekusi sebagai user | `repository.py:125-172`; `core/deps.py:75-86` |
| Audit log schema | `docker/init-nova.sql:311-348`; `backend/app/common/audit.py:10-58` |
| Delegate-first (pola NOVA-23) | `docs/specs/nova-23-task-orchestration-design.md:210-243,369` |
| Sidecar MLflow sebagai preseden | `docs/roadmap-snowflake-parity.md:183-304` |
| React 19 + Vite + TanStack + shadcn/ui + Monaco | `frontend/package.json` |
| Versi & lisensi framework | GitHub API `/repos`, `/releases/latest`, raw `LICENSE`, PyPI JSON — diakses 2026-09-18 |
| CoCo/Cortex Agents sebagai referensi model | lihat `docs/research/agentic-assistant-snowflake-coco.md` |
