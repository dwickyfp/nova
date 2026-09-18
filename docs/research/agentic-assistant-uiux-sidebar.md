# Riset UI/UX: Sidebar Agentic Assistant Nova

> Dokumen riset. Bagian 4 dari scope NOVA-61. Tidak ada kode produksi yang diubah.
> Referensi NOVA-61. Tanggal akses semua sumber: 18 September 2026.

Basis kode yang dibaca: commit `0c55f14` pada branch
`agent/frontend-expert-react-vite/c2c5e6f4b845`.

## 0. Ringkasan untuk pemilik keputusan

Riset ini tidak memutuskan **E1** (apakah assistant boleh mengeksekusi SQL
langsung) dan **E2** (scope `always allow`). Keduanya keputusan produk di luar
mandat dokumen ini. Yang dokumen ini lakukan: menyediakan pola referensi nyata
dan matriks opsi supaya keputusan itu bisa diambil dengan bukti.

Tiga hal yang paling mengikat desain Nova:

1. **Tidak ada satu pun produk pembanding yang menghapus jejak approval.** CoCo,
   VS Code Copilot, dan Genie semuanya memberi user satu titik kontrol sebelum
   aksi yang mengubah keadaan. Nova harus mengikuti.
2. **Scope `always allow` punya preseden jelas dan tidak seragam.** VS Code
   menyediakan empat tingkat (once / session / workspace / all future) dan
   menandai `Allow all` sebagai berisiko. CoCo CLI memakai sistem tiga tingkat.
   Artinya scope adalah keputusan desain yang sadar, bukan default.
3. **Design system Nova sudah punya hampir semua primitif yang dibutuhkan**,
   termasuk `Sheet`, `ScrollArea`, `StatusBadge`, `EmptyState`, dan
   `Collapsible`. Yang belum ada satu pun adalah pola approval tool. Di situlah
   nilai baru dokumen ini.

---

## 1. Pola panel kanan (hide/show, lebar, persistensi, layar sempit)

### 1.1 Bukti dari produk pembanding

**Snowflake Copilot (legacy) dan CoCo.**

- Panel dibuka dari tombol **Ask Copilot** / ikon CoCo di pojok **kanan bawah**
  worksheet, dan panel **terbuka di sisi kanan** worksheet. Satu sesi chat
  terikat ke satu worksheet: "Each chat session with Snowflake Copilot is
  associated with a particular worksheet. Opening a new worksheet opens a new
  chat session."
  (https://docs.snowflake.com/en/user-guide/snowflake-copilot, confidence
  tinggi, diakses 2026-09-18)
- CoCo menyempurnakan ini: panel yang sama, tapi konteks aktif mengikuti file
  atau notebook yang sedang dilihat ("CoCo knows which SQL file or notebook you
  are currently viewing and uses that as background context"). Sumber:
  https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code (confidence
  tinggi).
- Tidak ada dokumentasi soal resize drag atau lebar persis panel. **Tidak
  terdokumentasi.** Jangan mengarang angka lebar dari Snowflake.

**VS Code Copilot Chat.**

- Chat adalah **view** yang bisa dipindah, bukan komponen modal. Artikel
  dokumentasi menyebut "Chat view, chat editor tabs, and the Agents window"
  sebagai surface yang berbagi mekanik interaksi yang sama
  (https://code.visualstudio.com/docs/copilot/chat/copilot-chat, confidence
  tinggi).
- Konsekuensinya penting untuk Nova: chat bukan dialog modal. Di VS Code, panel
  chat tidak mengunci seluruh aplikasi; user tetap bisa mengedit file sambil
  chat terbuka. Approval tool adalah satu-satunya titik yang memblokir, dan itu
  pun lewat dialog konfirmasi terpisah, bukan lewat modal panel.
- VS Code menyediakan opsi **queue / steer / stop-and-send** saat respons sedang
  berjalan, plus reorder pesan yang menunggu. Ini pola yang layak diadopsi.

**Cursor.** Dokumentasi publik Cursor yang bisa di-fetch hanya halaman overview
dan model; detail panel/chat tidak tersedia sebagai dokumen teknis. **Tidak
terdokumentasi** di sumber primer yang bisa dikutip.

**Databricks Genie Code.** Chat tersedia di notebook, SQL editor, jobs,
dashboard, dan file editor; ia "adapts to the product surface you are using"
(https://docs.databricks.com/aws/en/genie-code/features-capabilities,
last updated 2026-09-15, confidence tinggi). Genie One punya antarmuka chat
tersendiri yang diakses lewat UI Genie, bukan panel samping di dalam workspace.

**Kesimpulan yang didukung bukti.** Untuk konteks Nova (SQL workspace yang sudah
punya sidebar kiri secondary dan editor Monaco di tengah), panel kanan adalah
pola yang dominan di produk sekelas ini: CoCo dan Copilot keduanya menaruh chat
di kanan, keduanya non-modal, dan keduanya mengikat konteks ke file/worksheet
aktif.

### 1.2 Kondisi Nova saat ini (yang harus dihormati)

- Layout workspace sudah berupa flex row: `<aside>` kiri
  (`features/workspaces/index.tsx:1144`) dengan lebar `w-14` saat collapsed dan
  `w-80` saat expanded, lalu `<section>` editor
  (`features/workspaces/index.tsx:1294`).
- Persistensi state layout sudah punya pola matang: `sidebar_collapsed`
  disimpan lewat `PUT /workspaces/state` (debounce 300 ms,
  `features/workspaces/index.tsx:799-828`). Ini adalah tempat yang benar untuk
  menyimpan state panel assistant per user, bukan `localStorage` baru.
- Layout global memakai cookie lewat `LayoutProvider`
  (`context/layout-provider.tsx:35-49`), dan sidebar kiri memakai `SidebarProvider`
  dengan shortcut keyboard `Ctrl/Cmd + B` (`components/ui/sidebar.tsx:92-107`).
- Lebar sidebar kiri di-hardcode sebagai konstanta: `SIDEBAR_WIDTH = '16rem'`,
  `SIDEBAR_WIDTH_MOBILE = '18rem'`, `SIDEBAR_WIDTH_ICON = '3rem'`
  (`components/ui/sidebar.tsx:27-29`).

### 1.3 Matriks opsi penempatan panel

Skala effort S/M/L kasar untuk implementasi frontend saja (belum termasuk
backend streaming).

| Opsi | Lebar | Resize | Layar sempit | Effort | Risiko | Dependensi |
|---|---|---|---|---|---|---|
| **A. Panel kanan di dalam `<section>` workspace** | `w-[22rem]` tetap, aligned dengan `SIDEBAR_WIDTH` 16rem | Tidak | Di bawah ~1024px jadi overlay `Sheet` (sudah ada, `components/ui/sheet.tsx`) | S | Rendah. Ruang editor menyusut; Monaco harus diberi `min-w-0` agar tidak overflow | Layout flex workspace sudah mendukung |
| **B. Panel kanan resizable** (drag handle) | 20-36rem | Ya, handle 4-8px | Sama seperti A | M | Sedang. Butuh pola drag; workspace sudah punya preseden drag (results panel, `features/workspaces/index.tsx:1119-1128, 733-750`). State harus dipersistensi | Opsi A |
| **C. Tab di panel bawah hasil** | n/a | Sudah resizable (`resultsHeight`) | Tab menggantikan tab panel saat sempit | S | Sedang-tinggi. Tab Results/History/Explain sudah 4; menambah tab kelima mengurangi ruang dan mengaburkan hierarki. Assistant bukan hasil query | Panel hasil sudah ada |
| **D. Panel kanan global (di luar workspace, di level `AuthenticatedLayout`)** | `w-[24rem]` | Tidak | Overlay `Sheet` | M | Tinggi. Konteks file aktif tidak otomatis tersedia; butuh penyambungan state lintas route | Butuh context provider global baru |

**Rekomendasi dokumen ini: Opsi A, dengan jalur upgrade ke B.**
Alasan satu baris: A memakai layout flex yang sudah ada dan `Sheet` yang sudah
ada, jadi nol dependensi baru, dan B adalah perluasan dari A (bukan arsitektur
berbeda) sehingga bisa ditunda sampai ada permintaan nyata.

Panel kanan di dalam `<section>` juga menyelesaikan masalah konteks: komponen
panel bisa membaca `activeTab` langsung dari `WorkspacesPage`, sama seperti
`runQuery` membaca `activeTab` (`features/workspaces/index.tsx:968-1000`). Opsi D
memaksa lifting state besar-besaran dan tidak sebanding manfaatnya.

**Yang tidak boleh dilakukan (constraint dari issue):** jangan hardcode tinggi.
Panel harus `flex min-h-0 flex-col` dengan area pesan `ScrollArea className='min-h-0 flex-1'` persis seperti sidebar kiri (`features/workspaces/index.tsx:1145, 1224`). Jangan `overflow-x-scroll`.

---

## 2. Streaming respons

### 2.1 Bukti pola

- **SSE (Server-Sent Events).** MDN mendokumentasikan `EventSource`, format
  `text/event-stream`, event bernama, field `id`/`retry`, dan reconnect otomatis.
  Catatan penting: tanpa HTTP/2, batas koneksi SSE adalah 6 per browser per
  domain. (https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events,
  Baseline widely available, confidence tinggi.)
- **Cancel.** SSE ditutup dengan `evtSource.close()`. Untuk `fetch` stream,
  pembatalan memakai `AbortController`; Nova sudah memakai pola ini untuk query
  (`features/workspaces/index.tsx:983-984, 1007`), jadi tidak ada kosakata baru.
- **Partial state.** VS Code menegaskan bahwa "Stopping a request doesn't undo
  file edits, terminal commands, or other actions that already completed."
  (https://code.visualstudio.com/docs/copilot/chat/copilot-chat, confidence
  tinggi). Ini aturan desain: setelah stop, UI harus tetap menampilkan apa yang
  sudah terjadi, dan menandai dengan jelas batas antara "sudah dieksekusi" dan
  "dibatalkan".

### 2.2 Rekomendasi Nova

| Aspek | Rekomendasi | Alasan |
|---|---|---|
| Transport | **SSE lewat `fetch` + `ReadableStream`, bukan `EventSource`** | `fetch` menerima `Authorization` header; `EventSource` tidak. Nova menyimpan token di Zustand, bukan cookie (`stores/auth-store.ts`), jadi `EventSource` tidak bisa dipakai tanpa mengubah auth. |
| Frame | `data:` berisi JSON dengan tipe eksplisit: `text_delta`, `tool_call`, `tool_status`, `done`, `error` | Satu parser, satu kontrak. Meniru event bernama SSE MDN. |
| Cancel | `AbortController` per turn; tombol Stop di composer | Sudah jadi pola Nova di `runQuery`. |
| Partial state | Setelah abort, render pesan asisten parsial + badge "dibatalkan"; jangan hapus | Konsisten dengan VS Code. |
| Reconnect | **Tidak ada auto-reconnect di v1** | Auto-reconnect agentik bisa menggandakan tool call (efek samping). Ini berbeda dari SSE generik. Trade-off harus ditulis di spec implementasi. |
| Batas | Satu SSE stream hidup per sesi; jangan buka stream per pesan ke domain yang sama tanpa membatasi | Batas 6 koneksi per browser/domain (MDN). |

**Belum diketahui:** apakah FastAPI/uvicorn di Nova sudah punya endpoint
streaming (`StreamingResponse`) yang terpasang dan teruji. Dokumen ini tidak
memverifikasi itu; itu ranah workstream 3. Anggapan yang aman: belum ada, jadi
effort streaming masuk hitungan backend.

---

## 3. Tampilan tool call dan approval (bagian terpenting)

Ini bagian yang belum punya pola baku di Nova. Berikut pola dari sumber primer
dan rancangan alternatifnya.

### 3.1 Bukti pola approval

**VS Code (paling kaya dan paling eksplisit).**
(https://code.visualstudio.com/docs/agents/run/approvals, confidence tinggi)

- Permission level per sesi: **Manual** (default), **Assisted** (LLM judge),
  **Allow all**. `Allow all` memunculkan dialog peringatan pertama kali.
- Approval tool punya empat scope: **once**, **session**, **workspace**, **all
  future invocations**.
- Ada pembedaan **pre-approval** ("runs the tool without a confirmation
  dialog") vs **post-approval** ("adds the tool result to the chat context
  without review").
- Sebagian tool bisa dikecualikan dari auto-approval agar selalu manual.
- Peringatan eksplisit: hasil tool dapat berisi **prompt injection**.

**CoCo CLI.** Punya "three-tier approval system" dan "automatic risk
assessment" plus OS-level sandboxing
(https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code, confidence
tinggi). Detail tiga tingkatnya **tidak dirinci** di halaman overview; halaman
CLI tidak memuat tabel tingkat. Jadi: tulis "three-tier approval" sebagai fakta
tersitasi, jangan mengarang isi tiap tingkat.

**CoCo di Snowsight.** Untuk perubahan kode, CoCo menampilkan **diff view** dan
user menerima/menolak sebelum diterapkan
(https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-snowsight).
Untuk eksekusi SQL, dokumentasi menyatakan user "can execute the statements or
copy them to your clipboard" — jadi ada jalur eksekusi langsung maupun
hand-off. **Apakah ada opsi "always allow" di CoCo Snowsight: tidak
terdokumentasi.**

**Genie Code.** Quick Fix menyajikan rekomendasi yang bisa di-**Accept and run**;
Databricks merekomendasikan "always review any code generated ... before
running it" (https://docs.databricks.com/aws/en/genie-code/features-capabilities,
confidence tinggi). Ini pola inline approval, bukan panel.

### 3.2 Alternatif tampilan tool call untuk Nova

Semua alternatif di bawah memakai blok pesan di dalam transcript (bukan dialog
modal per tool), kecuali dinyatakan lain.

**Alternatif 1, Inline "tool card" di transcript (rekomendasi utama).**

Sebuah kartu di dalam alur pesan, menganut status yang sama dengan
`StatusBadge` Nova:

```
┌─────────────────────────────────────────────┐
│  [ikon] query_execute        running  ⟳     │
│  SELECT region, SUM(amount)                 │
│  FROM sales WHERE ...                       │
│  ─────────────────────────────────────────  │
│  [ Deny ]                    [ Allow ]      │
│  [ ] Always allow SELECT for this session   │
└─────────────────────────────────────────────┘
```

- Status: `queued` (badge netral), `running` (badge info + spinner), `done`
  (badge sukses), `failed` (badge danger). Semua memakai
  `components/ui/status-badge.tsx` dengan `tone` yang sesuai; tidak perlu
  komponen status baru.
- SQL ditampilkan penuh dan bisa dibaca sebelum approve. Ini setara "review the
  tool name and input parameters" VS Code.
- Checkbox "Always allow" hanya untuk kelas statement yang aman. Untuk
  statement destruktif, checkbox tidak muncul. Ini memetakan ke keputusan
  allow/deny workstream 3.

**Alternatif 2, Approval bar yang menempel di composer.**

Saat ada tool call menunggu, composer berubah menjadi bar konfirmasi
(allow/deny/always allow) dan input pesan dinonaktifkan. Kelebihan: satu titik
fokus, tidak mungkin terlewat. Kekurangan: memblokir mengetik pesan berikutnya,
padahal VS Code justru mengizinkan queue/steer.

**Alternatif 3, Diff view penuh sebelum menerapkan.**

Untuk tool yang menulis (create table, alter), tampilkan diff sebelum/sesudah
alih-alih SQL mentah. Ini pola CoCo di Workspaces. Effort lebih besar; hanya
layak kalau Nova mengizinkan DDL dari assistant (keputusan E1).

**Alternatif 4, Auto-approve global per workspace.**

Setara `Allow all` VS Code. **Tidak direkomendasikan** untuk Nova v1: Nova
memegang RBAC engine, dan `Allow all` melebar ke DDL admin. Kalau tetap dipakai,
wajib ada dialog peringatan sekali seperti VS Code, dan hanya untuk statement
non-destruktif.

**Rekomendasi dokumen ini: Alternatif 1 sebagai default, dengan elemen
Alternatif 2 sebagai fallback saat panel sempit.** Alasan satu baris: Alternatif
1 memberi jejak audit visual permanen di transcript tanpa memblokir alur kerja,
dan memetakan langsung ke `StatusBadge` yang sudah ada.

### 3.3 Kontrak data yang disarankan (bukan kode produksi)

```ts
type ToolCallStatus = 'queued' | 'awaiting_approval' | 'running' | 'done' | 'failed' | 'denied'

type ToolCallView = {
  id: string
  tool: string                 // mis. 'query_execute'
  // SQL lengkap yang akan dijalankan, sudah lewat guard backend.
  // Tidak boleh memuat kredensial (lihat constraint issue).
  preview: string
  status: ToolCallStatus
  // Hasil ringkas; baris data mentah tidak dirender di sini.
  summary?: { rowCount?: number; elapsedMs?: number }
  error?: string
}
```

Catatan keamanan untuk UI: `preview` dan `error` adalah **teks dari server**.
Frontend tidak boleh menampilkan kredensial. Redaksi adalah tanggung jawab
backend (NOVA-10, NOVA-11, NOVA-21), tapi UI tetap harus merender sebagai teks
biasa (bukan `dangerouslySetInnerHTML`), dan tidak menyalin isi tool result ke
`localStorage`.

---

## 4. Manajemen sesi/percakapan

### 4.1 Bukti

- **CoCo:** sesi terikat worksheet/file aktif, dan ada "session persistence"
  di CLI (https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code).
  Sesi juga konteks-aware terhadap file yang dilihat.
- **VS Code:** multi-sesi paralel, ganti sesi tanpa kehilangan konteks, session
  history, plus fitur branch/steer/queue
  (https://code.visualstudio.com/docs/copilot/chat/copilot-chat).
- **Genie Code:** bisa branch chat, `@`-mention konteks, edit pesan lama untuk
  regenerate (https://docs.databricks.com/aws/en/genie-code/features-capabilities).

### 4.2 Rekomendasi

**Judul otomatis: jangan dibuat di v1 tanpa sumber teks yang jelas.** Membuat
judul dari pesan pertama user adalah default yang murah dan jujur (judul =
kutipan awal pesan), bukan judul "yang disimpulkan" yang berisiko salah.
Alternatif: biarkan user menamai. Jangan menampilkan judul generatif yang tidak
bisa dilacak asalnya.

**Sinkronisasi dengan editor.** Sesi assistant harus menyimpan referensi
`file_id` aktif saat dibuat (kolom yang sama yang dipakai `/query/execute`,
`features/workspaces/index.tsx:996`). Saat user ganti tab, jangan pindah sesi
otomatis; tampilkan badge "sesi ini dibuat untuk file X" supaya user tahu
konteksnya. Ini lebih aman daripada diam-diam mengganti konteks.

**Daftar thread.** Tempatkan di `Sheet`/`Popover` dari header panel, bukan
sidebar ketiga. Tiga kolom (nav kiri + editor + panel kanan) sudah padat di
layar 1280px; daftar thread harus overlay, bukan kolom permanen.

**Operasi minimal v1:** new, switch, rename, delete, dan clear. Delete wajib
konfirmasi (pakai `ConfirmDialog` yang sudah ada,
`components/confirm-dialog.tsx`).

**Belum diketahui:** apakah state percakapan disimpan di `NOVA_SYSTEM` atau
hanya in-memory. Itu keputusan workstream 1 dan 3 ("Single Database"). Dokumen
ini hanya menetapkan: **UI tidak boleh menyimpan riwayat percakapan di state
lokal yang bertahan lintas reload** kalau isinya bisa memuat SQL/data; itu
melanggar "Single Database" dan berisiko credential-shaped content.

---

## 5. Kesesuaian dengan design system Nova

Sumber aturan: `DESIGN.md` (single source of truth), `docs/specs`, NOVA-16, dan
token di `frontend/src/styles/theme.css`.

### 5.1 Primitif yang sudah ada dan harus dipakai (jangan duplikasi)

| Kebutuhan UI | Primitif yang sudah ada | Path |
|---|---|---|
| Panel kanan overlay (layar sempit) | `Sheet` + `SheetContent side='right'` | `frontend/src/components/ui/sheet.tsx:44-79` |
| Area pesan scroll | `ScrollArea` | `frontend/src/components/ui/scroll-area.tsx` |
| Status tool (queued/running/done/failed) | `StatusBadge` (tone: neutral/info/success/danger, prop `dot`) | `frontend/src/components/ui/status-badge.tsx:41-64` |
| State kosong / error | `EmptyState` (`variant='error'`) | `frontend/src/components/ui/empty-state.tsx:15-63` |
| Section collapsible (mis. tool detail) | `Collapsible` | `frontend/src/components/ui/collapsible.tsx` |
| Konfirmasi hapus sesi | `ConfirmDialog` | `frontend/src/components/confirm-dialog.tsx` |
| Composer multibaris | `Textarea` | `frontend/src/components/ui/textarea.tsx` |
| Aksi utama/kirim | `Button` | `frontend/src/components/ui/button.tsx` |

**Komponen baru yang benar-benar diperlukan (tidak menduplikasi):**

1. `AssistantPanel` (container kanan; komposisi, bukan primitif baru).
2. `MessageList` / `MessageBubble` (belum ada pola bubble di Nova; pesan user
   vs asisten). Deskripsi: `MessageBubble({ role, content, status })`.
3. `ToolCallCard` (lihat 3.2 Alternatif 1). Props:
   `ToolCallCard({ call: ToolCallView, onApprove, onDeny, onAlwaysAllow })`.
4. `ApprovalControls` (allow/deny/always allow) bila tidak digabung ke
   `ToolCallCard`.

### 5.2 Aturan token yang mengikat (dari `DESIGN.md` dan gate ESLint)

- Warna hanya token semantik: `text-success-strong`, `bg-surface-2`,
  `border-surface-border`, `text-muted-foreground`. Dilarang
  `bg-emerald-600`, `text-blue-500` di `features/**` (A1,
  `DESIGN.md:108-128`).
- Hex literal dilarang di `src/**` kecuali `theme.css` (A2). Jadi jangan
  menyalin `#d04738` ke komponen panel.
- **Jangan pakai `hsl(var(--token))`**: token Nova bernilai `oklch(...)`/hex,
  bukan triplet HSL (`DESIGN.md:163-180`).
- Aturan B1: ikon AI generik (`Sparkles`, `Wand2`, `Bot`) **tidak boleh
  ditambah ke lokasi baru** sampai bahasa visual ML/LLM diputuskan
  (`DESIGN.md:272-277`). Ini langsung mengikat tombol pembuka panel assistant.
  **Konsekuensi:** pilihan ikon tombol assistant adalah keputusan design system
  yang tertunda, bukan detail bebas. Dokumen ini menandainya sebagai blocker
  desain, bukan memilih ikon diam-diam.
- Aturan B2: setiap data view punya keadaan kosong, memuat, error, dan pakai
  `EmptyState`/`LoadingOverlay`, bukan skeleton ad-hoc (`DESIGN.md:279-299`).
  Panel assistant adalah data view: daftar sesi kosong, transcript kosong,
  koneksi gagal harus punya tiga layar berbeda.
- Aturan B4 dan keputusan `--primary`: `#d04738` hanya boleh jadi teks di mode
  terang; di mode gelap pakai `--primary-foreground` di atas blok `--primary`
  (`DESIGN.md:306-320`). Jangan merender teks `text-primary` di atas background
  gelap.
- Radius dari sistem: `--radius: 0.625rem` dan turunannya
  (`styles/theme.css:2, 134-137`). Jangan bikin radius baru.

### 5.3 Dependensi baru

**Tidak ada dependensi baru yang diperlukan.** Semua kebutuhan (panel, scroll,
badge, dialog, confirm) sudah tertutup. Kalau implementasi memilih markdown
rendering untuk pesan asisten, kandidat paling umum adalah `react-markdown`
(MIT) — **nama dan lisensi disebut, belum diputuskan.** Ini satu-satunya
kandidat dependensi yang teridentifikasi, dan hanya jika format kaya
diperlukan. Untuk v1, teks + blok kode polos sudah cukup dan nol dependensi.

---

## 6. Aksesibilitas dan keyboard

### 6.1 Fokus dan mode panel

Panel kanan non-modal **tidak boleh** memakai focus trap. Focus trap hanya untuk
dialog modal. W3C APG: `aria-modal='true'` hanya sah kalau aplikasi mencegah
interaksi dengan luar dialog **dan** styling menyembunyikan luar dialog
(https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/, confidence tinggi).
Karena Opsi A adalah panel inline yang hidup berdampingan dengan editor, ia
**bukan** modal. Yang benar:

- Panel memakai landmark `<aside aria-label="Assistant">`.
- Tombol toggle punya `aria-expanded` dan `aria-controls` yang menunjuk id
  panel.
- Shortcut toggle: ikuti pola Nova yang ada. Sidebar kiri memakai
  `Ctrl/Cmd + B` (`components/ui/sidebar.tsx:92-107`). Untuk panel assistant,
  rekomendasi `Ctrl/Cmd + J` (tidak bentrok dengan format `Ctrl+Shift+F` atau
  run di Monaco). **Belum diverifikasi** terhadap semua binding Monaco; ini
  harus dicek saat implementasi.
- Saat panel dibuka, **jangan** pindahkan focus paksa ke input. VS Code tidak
  memaksa; user mungkin membuka panel hanya untuk membaca. Beri tombol "focus
  composer" yang bisa diakses keyboard.

Kalau Opsi D (panel global) atau layar sempit memakai `Sheet`, maka focus trap
**wajib**, dan itu sudah gratis dari Radix (`sheet.tsx` dibangun di atas
`@radix-ui/react-dialog`). Ringkasnya: modal-semantik hanya di `Sheet`, bukan di
panel inline.

### 6.2 Live region untuk streaming

- Streaming teks tidak boleh mengumumkan setiap token. Itu akan membanjiri
  screen reader. WCAG SC 4.1.3 (Status Messages, Level AA) memberi teknik
  `role='status'` untuk hasil aksi, `role='alert'` untuk error, dan `role='log'`
  untuk update berurutan
  (https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html,
  confidence tinggi).
- Desain yang benar: satu `role='status' aria-live='polite'` **terpisah** yang
  mengumumkan transisi status ("Menjalankan query", "Query selesai, 42 baris"),
  bukan isi pesan. Pesan asisten sendiri diumumkan saat selesai, bukan saat
  streaming.
- Error tool memakai `role='alert'` (hanya untuk error yang benar-benar perlu
  perhatian; WCAG menandai `role='alert'` berlebihan sebagai failure F103).
  `EmptyState variant='error'` sudah memasang `role='alert'`
  (`components/ui/empty-state.tsx:28`).
- Tool call yang butuh approval adalah aksi yang tidak boleh dilewatkan:
  tampilkan status + tombol yang reachable dengan Tab, dan beri `aria-describedby`
  yang menyebut tool dan dampaknya. Jangan andalkan warna saja; `StatusBadge`
  sudah membawa teks label, jadi jangan hapus teksnya.

### 6.3 Tap target, mobile, dan zoom

- Target minimal 44x44 px (aturan antislop R-03, sejalan DESIGN.md). Tombol
  approve/deny harus ukuran penuh, bukan ikon kecil.
- Mobile: panel jadi `Sheet` (sudah responsif: `w-3/4 ... sm:max-w-sm`,
  `sheet.tsx:59-60`). Pastikan tidak ada horizontal overflow; Monaco punya
  lebar sendiri dan harus diberi `min-w-0` di parent flex.
- Teks harus selamat di zoom 200% tanpa terpotong. Transkrip pakai `ScrollArea`,
  bukan tinggi tetap.
- Jangan sembunyikan satu-satunya jalur approval di balik hover.

---

## 7. Matriks opsi ringkas (untuk E1 dan E2)

Dokumen ini tidak memutuskan E1/E2, tapi menyediakan opsi ber-effort.

### 7.1 Surface eksekusi (E1)

| Opsi | Deskripsi | Effort UI | Risiko | Preseden |
|---|---|---|---|---|
| E1a | Assistant mengeksekusi, approval per call | S | Sedang; perlu approval + audit | CoCo, VS Code |
| E1b | Assistant hanya menaruh SQL ke worksheet, user yang Run | S | Rendah | Copilot legacy "Add" |
| E1c | Hybrid: SELECT/SHOW auto ke hasil panel, DDL/destruktif hand-off ke worksheet | M | Sedang | CoCo (execute atau copy) |

### 7.2 Scope `always allow` (E2)

| Opsi | Scope | Effort | Risiko | Preseden |
|---|---|---|---|---|
| E2a | Sekali (per call) | S | Terendah | VS Code default |
| E2b | Per sesi percakapan | S | Rendah | VS Code session; "session" scope |
| E2c | Per user + workspace (persisten) | M | Sedang; butuh penyimpanan di `NOVA_SYSTEM` | VS Code workspace |
| E2d | Global `Allow all` | S | **Tinggi**; melebar ke DDL admin | VS Code (dengan warning) |

Untuk Nova, opsi yang paling konservatif dan paling sesuai pola existing adalah
**E2b**; E2d sebaiknya tidak dirilis tanpa dialog peringatan dan pemisahan
statement destruktif.

---

## 8. Belum diketahui / belum terverifikasi

1. **Lebar dan resize panel CoCo/Copilot secara persis** tidak terdokumentasi
   di sumber primer yang bisa dikutip. Rekomendasi lebar di §1.3 adalah
   usulan, bukan angka yang diambil dari produk.
2. **Opsi "always allow" di CoCo Snowsight** tidak terdokumentasi. Yang
   terdokumentasi hanya three-tier approval di CoCo CLI (tanpa rincian tiap
   tingkat).
3. **Model consent tool di Copilot Chat web/desktop** tidak dirinci di halaman
   yang diakses; yang kaya detail adalah VS Code.
4. **Apakah Nova sudah punya endpoint streaming** belum diverifikasi di dokumen
   ini (ranah workstream 3).
5. **Penyimpanan state percakapan** (in-memory vs `NOVA_SYSTEM`) belum
   diputuskan; menunggu workstream 1 dan 3.
6. **Ikon tombol assistant** terblokir aturan B1 (`DESIGN.md:272-277`).
7. **Shortcut `Ctrl/Cmd + J`** belum diverifikasi bebas konflik dengan seluruh
   binding Monaco Nova.
8. **Markdown rendering**: apakah perlu `react-markdown` atau cukup teks +
   blok kode, belum diputuskan.

---

## 9. Rujukan

Semua diakses 18 September 2026.

- Snowflake Copilot (legacy):
  https://docs.snowflake.com/en/user-guide/snowflake-copilot
- CoCo overview:
  https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code
- CoCo in Snowsight:
  https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-snowsight
- CoCo CLI:
  https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-cli
- VS Code Copilot Chat:
  https://code.visualstudio.com/docs/copilot/chat/copilot-chat
- VS Code approvals & permissions:
  https://code.visualstudio.com/docs/agents/run/approvals
- Databricks Genie (overview): https://docs.databricks.com/aws/en/genie/
- Databricks Genie Code capabilities:
  https://docs.databricks.com/aws/en/genie-code/features-capabilities
- Cursor docs (overview): https://docs.cursor.com/chat/overview
- MDN SSE:
  https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events
- W3C APG Dialog (Modal):
  https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/
- WCAG SC 4.1.3 Status Messages:
  https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html
- Nova internal: `DESIGN.md`, `docs/roadmap-snowflake-parity.md:147`,
  `docs/research-frontend-patterns.md`.
