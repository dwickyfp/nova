# Proposal: e2e Nova dengan `tester-army/e2e`

> **Status:** proposal, belum diputuskan. Non-normatif sampai spike di §6
> lolos dan ada keputusan.
> **Tanggal riset:** 2026-11-05. Versi yang dianalisis: paket npm `e2e`
> `0.22.0` (dirilis 2026-11-05), repo `tester-army/e2e` (Apache-2.0).

Penanda keyakinan: **[kode]** dicek di repo Nova, **[docs]** dari dokumentasi
`e2e`, **[belum]** belum dicoba pada Nova.

---

## 1. Ringkasan

- **Ya, `e2e` cocok menjadi lapisan e2e Nova**, tapi sebagai lapisan *baru*
  di atas yang sudah ada, bukan pengganti semua tes browser. Nova saat ini
  tidak punya suite e2e browser yang menjalankan UI asli terhadap backend,
  StarRocks yang di-patch, dan Ranger di CI. Yang ada hanya tes komponen
  Vitest dengan API tiruan, plus beberapa skrip Playwright manual. `e2e`
  mengisi celah itu.
- **Yang diganti:** skrip Playwright ad-hoc (`frontend/scripts/*.mjs`,
  `backend/tests/integration/intelligence_browser.mjs`) dan bagian UI dari
  `scripts/verify_ranger_e2e.py`.
- **Yang tetap:** 1.435 tes komponen Vitest browser (cepat, deterministik,
  memeriksa tema dan lebar layar), pytest unit/integrasi/eval di backend,
  serta bagian protokol MySQL di `verify_ranger_e2e.py`.
- **Lightpanda bukan pengganti Chromium untuk Nova.** Tidak ada layout
  engine, sehingga editor Monaco, peta MapLibre, Superset dalam iframe,
  emulasi tema, event resize, drag, dan scroll tidak bisa diandalkan.
  Pakai Lightpanda untuk *smoke* dan alur tata kelola berbasis form,
  Chromium untuk sisanya. Pembagiannya harus dibuktikan dengan spike (§6).
- **Risiko utama:** `e2e` masih pra-1.0 (API bisa berubah antar minor),
  langkah agen mengirim snapshot layar ke model (ada data tergovern di
  sana), dan uji agentic Studio menambah biaya model setiap run.

---

## 2. Apa itu `e2e` (tester-army)

| Aspek | Isi |
|---|---|
| Model tes | Satu file mencampur tiga jenis langkah: **goal** `agent.act` (LLM menjalankan UI), **asersi** `agent.assert` / `waitFor` / `extract` (LLM menilai layar), dan **locator** `screen.getByRole(...)` + `expect` (tanpa model) [docs] |
| Replay cache | Aksi `agent.act` direkam setelah ada pemeriksaan yang lolos, lalu diputar ulang tanpa memanggil model sampai UI berubah. Kalau UI berubah, agen mengambil alih dari layar saat itu. Asersi agen selalu live [docs] |
| Engine web | `@e2e-dev/web`: Chromium, Firefox, WebKit lewat Playwright. Provider browser: lokal, Lightpanda, Kernel, smol, TestMu [docs] |
| Lightpanda | `@e2e-dev/lightpanda`: satu `lightpanda serve` per worker, atau attach ke container lewat CDP. Ada DOM, JS, dan accessibility tree, tapi tanpa layout [docs] |
| Autentikasi | `test.setup` + `session.save('admin')`, lalu tes memakai `{ session: 'admin' }`. Password berupa handle `Secret` yang tidak pernah terkirim ke model [docs] |
| Persona | Banyak entri `agents` dengan `system`/`context` sendiri, bisa satu persona per role [docs] |
| API test | `fetch` + `expect(...).toMatchSchema(zod)` di run yang sama, tanpa model [docs] |
| Fixture | `test.extend` dengan setup/teardown, bisa memakai agen atau API [docs] |
| Model | AI Gateway, API key langsung, `@ai-sdk/openai-compatible` (lokal/Ollama), ACP (Claude Code/Codex lewat langganan), atau *decision model* yang lebih murah [docs] |
| Seleksi tes | `decisionFilter()` hanya menjalankan tes yang kemungkinan terdampak PR [docs] |
| Laporan | `.e2e/report.json`, JUnit, trace/screenshot/video, reporter komentar PR GitHub [docs] |
| Syarat | Node ≥ 24.8 atau ≥ 22.22.3. CI Nova memakai Node 22, lokal `v22.22.3` [kode] |
| Telemetri | CLI mengirim data penggunaan anonim secara default. Matikan dengan `E2E_TELEMETRY_DISABLED=1` [docs] |
| Kematangan | 0.22.0, repo dibuat Juli 2026, ±13k bintang, rilis sangat sering. Status resmi: "APIs and config can still change between minor releases" [docs] |

---

## 3. Lapisan uji Nova saat ini

| Lapisan | Alat | Lingkungan | Di CI? |
|---|---|---|---|
| Komponen frontend | Vitest browser + Playwright Chromium, 169 file / 1.435 tes | API tiruan | Ya [kode] |
| Unit dan eval backend | pytest, eval harness dengan provider berskrip | Tanpa engine | Ya [kode] |
| Integrasi L3 | pytest terhadap StarRocks **upstream** (RBAC native, tanpa Ranger) | Stack compose sementara | Ya [kode] |
| Acceptance Ranger | `scripts/verify_ranger_e2e.py` (proxy MySQL + tool agen) | Stack `docker` dengan patch | Tidak [kode] |
| Skrip UI ad-hoc | `frontend/scripts/verify-city-rbac-ui.mjs`, `task-role-ui.mjs`, `verify-intelligent-ui.mjs`, `benchmark-studio-maps.mjs`, `probe-studio-harness-ui.mjs`, `backend/tests/integration/intelligence_browser.mjs` | Manual | Tidak [kode] |
| Lab agentic live | `backend/tests/benchmark/smart_native` (gold + LLM judge) | Stack dev + LLM berbayar | Tidak [kode] |

Celahnya: belum ada tes otomatis yang membuktikan alur pengguna nyata, yaitu
login → ganti role → Explorer/Worksheet/Studio/BI, terhadap FE yang di-patch
dan Ranger. Pemeriksaan seperti itu sejauh ini dilakukan manual. Contohnya
pemeriksaan Explorer per role dan mask `MASK_HASH` lewat UI pada 2026-11-05.

---

## 4. Kecocokan Lightpanda dengan Nova

Lightpanda tidak punya layout. Batasan resminya [docs] dipetakan ke
permukaan Nova:

| Permukaan Nova | Bergantung pada | Lightpanda | Target |
|---|---|---|---|
| Login, ganti role, menu | DOM, cookie | Bisa, tapi session yang di-restore terbuka dalam keadaan logout; set cookie setelah `app.open` [docs] | Lightpanda |
| Database Explorer, Access Control, Users/Roles, Data Access | Form, tree, accessibility tree | Kemungkinan bisa, kecuali node di bawah fold. Layout `Main` Nova punya region scroll sendiri [belum] | Lightpanda, dibuktikan di spike |
| Workspaces (editor SQL Monaco) | Pengukuran layout, posisi textarea | Kemungkinan rusak [belum] | Chromium |
| Studio / Smart (streaming, kartu, peta) | SSE, canvas/WebGL (MapLibre) | Teks bisa; peta tidak (canvas) [docs] | Chromium |
| BI `/bi` (Superset dalam iframe) | iframe, chart canvas | iframe mati secara default, listener lintas frame tidak jalan [docs] | Chromium |
| Tema terang/gelap, lebar layar | `matchMedia`, event resize | Tidak jalan [docs] | Tetap di Vitest browser + Chromium |
| Unduh/ekspor CSV, `confirm()` | Event download, dialog | Tidak jalan [docs] | Chromium |
| Drag dashboard | `Input.setInterceptDrags` | Tidak jalan [docs] | Chromium |

Kesimpulan: dua target dalam satu config, `lightpanda` (smoke dan tata kelola)
dan `chromium` (penuh), dipilih dengan `e2e run --target`.

---

## 5. Desain yang diusulkan

### 5.1 Struktur

```
e2e/                         paket pnpm sendiri (versi e2e dipin persis)
  e2e.config.ts              target chromium + lightpanda, agents per persona
  fixtures/
    stack.ts                 cek kesehatan stack, base URL
    governance.ts            buat/hapus role, policy, user uji lewat API Nova
    data.ts                  tabel sintetis di database e2e_*
  tests/
    auth.setup.e2e.ts        session per persona
    governance/*.e2e.ts      Explorer per role, mask, row filter, ACCOUNTADMIN guard
    workspace/*.e2e.ts       SQL worksheet, hasil, riwayat
    catalog/*.e2e.ts         external catalog (Postgres JDBC), visibilitas
    studio/*.e2e.ts          pertanyaan bisnis, jawaban, kepatuhan RLS
    bi/*.e2e.ts              dashboard Superset
    api/*.e2e.ts             kontrak API dengan zod
```

Ditaruh di luar `frontend/` supaya dependensinya (Playwright milik `e2e`, AI
SDK) tidak tercampur dengan Playwright yang dipin untuk Vitest (`1.59.1`).

### 5.2 Persona dan tata kelola

- Persona sesuai role uji: `admin` (ACCOUNTADMIN), `marketing`, `finance`,
  `regional_manager`. Kredensial dibaca dari environment sebagai `Secret`,
  tidak pernah ditulis di tes.
- Setiap tes tata kelola punya **pasangan negatif**: role yang tidak berhak
  mendapat 404/kosong/mask. Ini menggantikan pemeriksaan manual 2026-11-05.
- Fixture membuat dan menghapus objeknya sendiri lewat API Nova (role,
  policy, catalog, user), dan tidak pernah memakai `root` untuk akses data.
  Ini mengikuti aturan `AGENTS.md`.

### 5.3 Model untuk langkah agen

- Default: **locator dulu**. `agent.act` hanya dipakai untuk alur yang
  memang dinamis (Studio, wizard), dan setiap `act` diikuti `expect`
  supaya replay cache berfungsi.
- Snapshot layar yang dikirim ke model berisi data, jadi tes hanya memakai
  **data sintetis** di database `e2e_*`. Model harus provider yang disetujui
  atau model lokal lewat `@ai-sdk/openai-compatible`.
- Untuk menghemat biaya, `decisionExecutor` atau ACP (langganan Claude Code)
  bisa dipakai untuk langkah agen, dan cache disimpan antar run CI.

### 5.4 Menguji agen Nova (Studio/Smart)

`e2e` cocok untuk ini karena jawaban agen tidak deterministik:

```ts
test('marketing hanya melihat Jakarta di jawaban Studio', { session: 'marketing' },
  async ({ app, agent, screen }) => {
    await app.open('/studio');
    await screen.getByRole('textbox', 'Ask Nova').fill('Total penjualan per kota?');
    await screen.getByRole('button', 'Send').tap();
    await agent.waitFor('jawaban selesai dan menampilkan tabel atau grafik', { timeout: 180_000 });
    const result = await agent.extract('setiap kota dan totalnya', { schema: CityTotals });
    expect(result.rows.map((r) => r.city)).toEqual(['Jakarta']);       // RLS
    expect(result.rows[0].total).toBe(await groundTruth('marketing'));  // dari API SQL
  });
```

- Nilai kebenaran (ground truth) diambil dari API SQL Nova dengan persona
  yang sama, sehingga tes membuktikan jawaban agen konsisten dengan Ranger.
- Tes ini lambat dan memakai dua LLM (milik Nova dan milik `e2e`), jadi
  dijalankan malam hari atau manual, bukan di setiap PR. Lab `smart_native`
  tetap menjadi tolok ukur akurasi. Tes e2e ini menjadi penjaga alurnya.

### 5.5 CI

| Job | Pemicu | Stack | Target | Model |
|---|---|---|---|---|
| `e2e-smoke` | Setiap PR | Governed (FE di-patch + Ranger) sementara | `lightpanda` | Tidak ada (locator saja) |
| `e2e-full` | Malam hari dan label PR | Sama | `chromium` | Replay cache; agen hanya saat cache miss |
| `e2e-agentic` | Malam hari | Sama + provider LLM Nova | `chromium` | Live |

- CI L3 sekarang memakai StarRocks upstream tanpa Ranger. E2e tata kelola
  butuh image FE yang di-patch dan Ranger, jadi butuh job baru yang
  membangun stack `docker/docker-compose-engine.yml` (sekali per run) dan
  dipin ke `ubuntu-24.04`, seperti job lain.
- `E2E_TELEMETRY_DISABLED=1` di semua job, laporan `.e2e/` diunggah sebagai
  artifact, dan JUnit dibaca CI.
- Lokal: jalankan terhadap stack `docker` yang sudah ada (sesuai `AGENTS.md`,
  tanpa stack kedua).

---

## 6. Rencana bertahap

### Fase 0: Spike (±1 minggu)

Pasang `e2e` di `e2e/`, lalu tulis 5 tes di kedua target:

1. Login + ganti role (marketing → finance → regional_manager), mengikuti alur
   yang sudah diverifikasi manual.
2. Explorer per role, termasuk catalog JDBC Postgres sementara.
3. Mask `MASK_HASH` lewat Access Control, lalu query sebagai `marketing` di
   Workspaces.
4. Worksheet SQL (Monaco): ketik, jalankan, baca hasil.
5. Satu pertanyaan Studio dengan `agent.waitFor` + `agent.extract`.

Yang diukur: lolos/gagal per target, durasi, token model, tingkat replay
cache, apakah session cookie Nova ter-restore, dan apakah Monaco jalan di
Lightpanda.

**Kriteria lanjut:** tes 1–3 lolos stabil (5×) di Chromium, dan setidaknya
tes 1–2 lolos di Lightpanda. Kalau tidak, Lightpanda ditunda dan semua tes
memakai Chromium.

### 6.1 Hasil spike Fase 0 (2026-11-05)

Dijalankan dari paket `e2e/` (`e2e@0.22.0`, `@e2e-dev/web@0.15.4`,
`@e2e-dev/lightpanda@0.0.1`) terhadap stack `docker` yang sudah berjalan
(FE `4.1.4` yang di-patch + Ranger, container `nova-backend`) dan frontend
`origin/main` di port 5182. Lightpanda `1.1.0-nightly` berjalan di container
dengan jaringan host. Semua tes hanya memakai locator, tanpa model. Tes 5
(Studio) belum dijalankan karena menunggu keputusan provider model (§7.1).

| Tes | Chromium | Lightpanda |
|---|---|---|
| Ganti role lewat menu akun (marketing → finance → regional_manager → marketing) | 5/5 lolos | Tidak bisa: sidebar tidak dirender (media query `md:` tanpa viewport). Tag `layout` |
| Explorer ACCOUNTADMIN melihat `NOVA_SYSTEM` | 5/5 | 5/5 |
| Explorer marketing hanya melihat `analytics` | 5/5 | 5/5 |
| Explorer finance (tanpa policy) melihat 0 database, role diganti lewat API | 5/5 | 5/5 |
| API Explorer: 404 untuk `NOVA_SYSTEM`, 200 JSON untuk `analytics` | 5/5 | 5/5 |
| Worksheet Monaco sebagai marketing: hanya Jakarta, telepon tersamar | Lolos setelah bug di bawah diperbaiki (3/3) | Tidak bisa: Monaco tidak membuat area input tanpa layout. Tag `layout` |

Durasi per run: Lightpanda ±5 detik untuk 4 tes (±1–1,4 detik per tes).
Chromium ±7–8 detik untuk 6 tes di luar tes worksheet.

**Bug produk yang ditemukan.** Di `main`, membuat file pertama di Workspaces
yang masih kosong (tombol "New File" di sidebar maupun "+" di strip tab)
membuat React masuk loop render (`Maximum update depth exceeded`, dilempar di
layout effect `TabsList`, `frontend/src/components/ui/tabs.tsx`), lalu
halaman jatuh ke error 500. Bug ini terulang setiap kali di Chromium dan juga
di browser biasa. Tes komponen Vitest tidak menangkapnya karena API-nya
tiruan. Pemicunya adalah `open_tabs` tersimpan yang masih menunjuk file
yang sudah dihapus. Bug ini diperbaiki di PR #205, yang juga menambahkan
tes komponen untuk kondisi tersebut. Setelah perbaikan itu masuk `main`,
seluruh suite Chromium (7 tes) lolos 3/3 dan Lightpanda (4 tes) lolos 3/3.

**Temuan tentang `e2e` dan Lightpanda:**

- `app.baseUrl` diakhiri `/`, bertentangan dengan dokumentasi. Tanpa
  `new URL(path, base)`, fixture API mengirim ke `//api/...` dan proxy Vite
  mengembalikan HTML 200. Tes API sekarang juga memeriksa `content-type`.
- HTTP biasa hanya diterima untuk host loopback (`INVALID_APP_URL`). Lightpanda
  di container butuh `--network host` supaya aplikasi terlihat di
  `127.0.0.1`. Di CI Linux ini perilaku bawaan Docker.
- Port 9222 di mesin dev sudah dipakai Chrome milik pengguna (remote
  debugging). Lightpanda dipindah ke 9333 supaya runner tidak pernah
  tersambung ke browser pribadi.
- `web({ connect: { cdpEndpoint } })` harus berupa fungsi.
- Fixture harus didefinisikan sebelum fixture yang memakainya.
- Locator menuntut tepat satu node. Ini menangkap dua tombol "Toggle Sidebar"
  di Chromium (rail + header) yang di Lightpanda hanya satu.
- Lightpanda tidak menampilkan toast `sonner` di pohon aksesibilitas. Penanda
  login memakai shell aplikasi, bukan toast.
- `e2e` CLI dan Lightpanda sama-sama mengirim telemetri secara default.
  Matikan dengan `E2E_TELEMETRY_DISABLED=1` dan
  `LIGHTPANDA_DISABLE_TELEMETRY=true`.

**Kesimpulan spike:**

1. Chromium memenuhi kriteria lanjut untuk tes 1–3 (5/5).
2. Lightpanda layak untuk tes tata kelola yang tidak bergantung pada layout,
   dengan role diganti lewat API. Menu, sidebar, dan editor tetap di Chromium.
   Pemisahannya memakai tag `layout` (`--exclude-tag layout` untuk
   Lightpanda).
3. Dalam spike singkat ini, e2e sudah menemukan satu bug produk yang lolos
   dari 1.435 tes komponen.

### Fase 1: Fondasi

- Paket `e2e/`, config dua target, persona, fixture tata kelola dan data.
- Job CI `e2e-smoke` di stack governed sementara.
- Runbook lokal di `docs/testing/` (reuse stack `docker`).

### Fase 2: Migrasi skrip ad-hoc

Port `verify-city-rbac-ui.mjs`, `task-role-ui.mjs`,
`intelligence_browser.mjs`, `verify-intelligent-ui.mjs`, dan bagian UI/agen
dari `verify_ranger_e2e.py` ke `e2e`, lalu hapus skrip lama setelah hasilnya
setara. Bagian proxy MySQL tetap di Python karena itu uji protokol, bukan
UI.

### Fase 3: Cakupan permukaan

Satu alur utama + satu kasus negatif tata kelola untuk setiap modul: auth dan
role, Explorer, external catalog, stage, Workspaces, Tasks, Access Control,
Users/Roles, BI, Studio, ML, Migration, Settings.

### Fase 4: Agentic Studio malam hari

Tes jawaban Studio per persona dengan ground truth dari API, berbagi kasus
dengan `smart_native`, dengan batas biaya per run.

---

## 7. Keputusan yang perlu diambil

1. **Provider model untuk langkah agen `e2e`.** Snapshot layar dikirim ke
   model. Pilihannya provider yang sama dengan Nova, langganan Claude Code
   lewat ACP, atau model lokal.
2. **Job CI governed.** Membangun FE yang di-patch + Ranger di setiap PR
   menambah waktu CI. Alternatifnya: smoke di setiap PR, sisanya malam hari.
3. **Toleransi pra-1.0.** Versi dipin persis dan upgrade dilakukan sengaja,
   atau tunggu 1.0.

## 8. Risiko

| Risiko | Mitigasi |
|---|---|
| API `e2e` berubah sebelum 1.0 | Pin versi, satu paket terisolasi, upgrade lewat PR sendiri |
| Data tergovern masuk ke prompt model | Data sintetis di `e2e_*`, provider disetujui atau lokal |
| Biaya dan flakiness langkah agen | Locator dulu, replay cache, agen live hanya malam hari |
| Lightpanda gagal pada layout Nova | Spike Fase 0, Chromium sebagai target default |
| CI lebih lama karena stack governed | Smoke ringan per PR, suite penuh malam hari |
| Telemetri CLI keluar ke pihak ketiga | `E2E_TELEMETRY_DISABLED=1` di CI dan lokal |

Sumber: README dan `docs/` repo `tester-army/e2e` (core-concepts, cache,
authentication, agents, executors, decision-models, api-testing, fixtures,
ci, integrations/lightpanda), npm `e2e@0.22.0`.
