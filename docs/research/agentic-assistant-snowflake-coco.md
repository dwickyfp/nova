# Riset: Snowflake Coco (Cortex Code) dan Padanannya

> **Workstream:** NOVA-61 §1 — riset teknologi. **Pemilik:** Nova DW Research & Roadmap.
> **Tanggal akses semua sumber:** 2026-09-18. **Engine pin Nova saat riset:** StarRocks 4.1.4.
> **Aturan:** setiap klaim faktual punya URL sumber + tingkat keyakinan. Yang tidak bisa
> diverifikasi ditandai eksplisit. Dokumen ini **tidak** menaruh secret/credential apa pun.

---

## Ringkasan (5 bullet)

- **"Coco" adalah nama produk dari Cortex Code**, dibangun di atas Cortex Agents. Ia muncul sebagai
  panel kanan di Snowsight (ikon di kanan-bawah), sebagai Desktop IDE, dan sebagai CLI. Snowflake
  sendiri menyebutnya "Snowflake CoCo" — **akronimnya tidak terdokumentasi**.
- **Coco bukan sekadar text-to-SQL**: ia agent yang merencanakan, memilih tool, mengeksekusi, dan
  merefleksikan hasil. Ia bisa mengeksekusi SQL/DDL **langsung dari panel**, dengan consent
  bertingkat: *Allow once* → *Allow for session / in this chat* → *Always allow*.
- **Model consent Snowsight sudah terdokumentasi dan GA**: opsi izin SQL (Allow Once / Allow all in
  this chat / Always Allow) masuk **GA 29 April 2026**; selector approval mode (Default/Bypass)
  masuk **GA 16 September 2026**. Scope "Always allow" di Snowsight = per tool, diingat browser.
- **Padanannya terbelah dua kubu**: text-to-SQL terkurasi (Cortex Analyst/Semantic Views, Databricks
  Genie Agents, Power BI Copilot) vs coding/agentic assistant (CoCo, Databricks Genie Code). Nova
  membutuhkan **keduanya**, dan itu dua arsitektur berbeda — bukan satu fitur.
- **Yang tidak terdokumentasi dan tidak boleh dikarang**: expansion akronim CoCo, tanggal GA produk
  CoCo, apakah CoCo merender result set ke worksheet atau panel grid di Snowsight, dan apakah
  BigQuery punya padanan agentic yang setara CoCo (halaman yang tersedia hanya Gemini assist biasa).

---

## 1. Nama produk, edition, dan permukaan UI

| Pertanyaan | Jawaban | Keyakinan | Sumber |
|---|---|---|---|
| Nama resmi fitur | **Snowflake CoCo** (produk) / **Cortex Code** (nama doc set & changelog) / **CoCo in Snowsight**, **CoCo Desktop**, **CoCo CLI** | Tinggi | [cortex-code.md](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code.md) |
| Epic lengkap "CoCo"? | **Tidak terdokumentasi** di halaman mana pun yang diakses | — | — |
| Kapan tersedia / edition | Semua akun Commercial (non-Gov), FedRAMP Moderate/High, DoD, KSA sovereign, dengan **cross-region inference aktif**. Desktop/CLI: Commercial (non-Gov, VPS, Sovereign) + cross-region inference | Tinggi | [cortex-code.md](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code.md) |
| Tanggal GA produk | **Tidak terdokumentasi.** Changelog hanya memberi tanggal per-fitur. Artefak paling awal: versi CLI `1.0.6` bertanggal **2026-02-04** (bukan klaim tanggal launch) | Rendah | [changelog.md](https://docs.snowflake.com/en/user-guide/cortex-code/changelog.md) |
| Permukaan UI | (1) **Panel kanan Snowsight** — "Select the CoCo icon in the lower-right corner. The CoCo panel opens on the right side". (2) **CoCo Desktop** (IDE macOS/Windows). (3) **CoCo CLI** | Tinggi | [cortex-code-snowsight.md](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-snowsight.md), [cortex-code.md](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code.md) |
| Versi CLI terbaru saat akses | `1.1.87` (14 Sep 2026) | Tinggi | [changelog.md](https://docs.snowflake.com/en/user-guide/cortex-code/changelog.md) |

**Catatan penamaan.** Doc set diberi judul "Cortex Code" sementara body memakai "CoCo". CoCo
dibangun di atas **Cortex Agents** (lihat §2). Ini penting untuk Nova: "Coco" bukan engine sendiri,
ia adalah *produk pengalaman* di atas platform agent generik.

---

## 2. Kemampuan konkret — didokumentasi vs klaim marketing

Legenda: **DOC** = ada di dokumentasi primer; **UI** = terlihat di UI tapi tidak dispesifikasikan;
**?** = tidak ditemukan.

| Kemampuan | Status | Sumber/penjelasan |
|---|---|---|
| Generate SQL dari prompt | **DOC** | "Write a query for top 10 customers by revenue…" ([snowsight](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-snowsight.md)) |
| Jelaskan SQL | **DOC** | "What does this SQL script do?"; quick action **Explain** pada SQL ter-highlight |
| Perbaiki error SQL | **DOC** | Tombol **Fix** di results grid saat statement gagal |
| Create table / objek by prompt | **DOC (tidak eksplisit "create table")** | Contoh prompt menunjukkan generate table/model/notebook/dbt project; tidak ada baris literal "CREATE TABLE by prompt" — kemampuan generatif DDL tersirat dari agentic coding, bukan dipisah |
| Modifikasi & review perubahan | **DOC** | **Diff view** insertions/deletions sebelum apply |
| Inline code suggestion (ghost text) | **DOC** | Saran abu-abu saat mengetik; accept `Tab`, dismiss `Esc` |
| Update/edit SQL di workspace | **DOC** | "Update the top performers query to show the top 100." |
| Optimize query / jelaskan lambat | **DOC** | "Explain why this query is slow and optimize it." |
| Notebook authoring (SQL/Python cells) | **DOC** | Add/remove/reorder cells, run notebook/cells |
| dbt lifecycle | **DOC** | Scaffold model, DAG, tests, docs, run dbt commands |
| Natural-language schema search | **DOC** | Cari tabel/kolom dengan bahasa alami |
| Integrated Q&A (docs Snowflake) | **DOC** | Jawab pertanyaan fitur/SQL dari dokumentasi resmi |
| Marketplace discovery (public & internal) | **DOC** | Cari listing eksternal & internal |
| Account admin: governance/security/cost | **DOC** | Cek akses role, PII, credit consumption, warehouse mahal |
| Web search | **DOC** | Harus di-enable ACCOUNTADMIN (AI/ML > Agents > Settings) |
| Agent skills via `/` | **DOC** | Built-in + personal skills, disimpan di `.snowflake/cortex/skills` |
| `AGENTS.md` persistensi instruksi | **DOC** | Root workspace, otomatis disertakan tiap percakapan |
| Semantic model Cortex Analyst via `@` | **DOC** | `@models/revenue.yaml` sebagai konteks |

**Yang TIDAK ditemukan (jangan diklaim):**
- Tidak ada halaman yang menyatakan CoCo bisa membuat **tabel fisik** langsung dari satu prompt
  sebagai operasi satu-langkah. Ia bisa menulis DDL, tetapi bukti primer hanya contoh prompt umum.
- Tidak ada spesifikasi format/schema tool-call yang dipublikasikan (tool names internal bukan
  dokumentasi publik) — kecuali kategori permission di halaman security CLI.

---

## 3. Model interaksi

| Aspek | Temuan | Keyakinan | Sumber |
|---|---|---|---|
| Multi-turn | **Ya.** "maintaining context across the session"; percakapan lanjut dengan follow-up | Tinggi | snowsight.md |
| Konteks file aktif | **Ya.** "CoCo knows which SQL file or notebook you are currently viewing and uses that as background context" | Tinggi | cortex-code.md |
| Attach objek katalog | **Ya.** Ketik `@` untuk mencari catalog object (table/schema/view) sebagai inline context | Tinggi | snowsight.md |
| Melihat isi data atau metadata saja? | **Tidak dispesifikasikan secara eksplisit.** CoCo mengeksekusi SQL (dengan izin), jadi ia **dapat** melihat hasil data. Untuk *generasi*, CoCo memakai query history, isi workspace, schema tabel, dan beberapa query terakhir. Data classification Snowsight menyatakan output CoCo berstatus **Customer Data** | Sedang | snowsight.md; [cortex-code.md](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code.md) |
| Model pemilihan LLM | Model yang role pengguna punya akses; pilihan **Auto / Auto Intelligent / Auto Efficient**; model selector di kanan-bawah message box | Tinggi | snowsight.md |
| Reuse query history untuk saran | **Ya.** AI code suggestion memakai query history user + schema + beberapa query terakhir | Tinggi | snowsight.md |
| Sesi selalu mulai dengan default role | **Ya.** "CoCo always starts a session using your default role, regardless of the role you've selected" | Tinggi | snowsight.md |

---

## 4. Tool execution dan consent (bagian paling penting)

### 4.1 Apakah Coco bisa mengeksekusi SQL/DDL?

**Ya, dan terdokumentasi.** Dua jalur bukti primer:

1. Halaman Snowsight: *"If the response from CoCo includes SQL statements, you can execute the
   statements or copy them to your clipboard."*
2. Changelog Snowsight mencatat perubahan bertanggal untuk **permission eksekusi SQL** dan
   **approval mode**, yang hanya relevan jika CoCo mengeksekusi tool.

### 4.2 Consent model — inilah spesifikasi yang bisa diadopsi Nova

**Snowsight (web)** — terdokumentasi dan **GA**:

| Tanggal GA | Fitur |
|---|---|
| **29 Apr 2026** | Permission updates for SQL execution: **Allow Once, Allow all in this chat, Always Allow** |
| **9 Sep 2026** | **Restrict this chat** (restricted session scope) |
| **16 Sep 2026** | **Approval modes** (Default Approvals / Bypass Approvals) |

Sumber: [cortex-code-snowsight/changelog.md](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-snowsight/changelog.md).

Detail selector approval mode di Snowsight:
- **Default approvals**: prompt per tool call; pilihan "allow single call / allow tool for this chat /
  always allow that tool".
- **Bypass approvals**: tool call dijalankan tanpa prompt individual.
- Percakapan baru mulai pada **Default approvals**; pilihan Bypass diingat per browser.
- Parameter `COCO_SNOWSIGHT_ALLOW_ALL_PERMISSION_OPTIONS_DISABLED` **hanya menyembunyikan** opsi
  "Allow <tool> in this chat" dan "Always allow <tool>" — jadi opsi itu memang ada di Snowsight.
- Bypass tetap tunduk pada **privilege Snowflake dan restricted session scope** — jadi Bypass bukan
  pemberian capability tanpa batas.

**CoCo Desktop / CLI** — tiga tingkat, lebih detail:

| Pilihan prompt | Scope | Sumber |
|---|---|---|
| **Allow once** | satu call | [permission-modes.md](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-desktop/permission-modes.md) |
| **Allow for session** | "this tool in this chat" | idem |
| **Always allow** | "never prompt for this tool again on this machine" (Desktop) | idem |
| **Reject** | batalkan call | idem |

CLI menambahkan **"Always allow (this session)"** (sampai keluar CLI) dan **"Always allow (persist)"**
(indefinite), dengan cache disimpan di `~/.snowflake/cortex/permissions.json`, scoped ke
**project directory + tool type + command pattern**:

```json
{
   "/path/to/project": {
      "Bash": { "npm test": "allow", "make build": "allow" },
      "Write": { "*": "allow" }
   }
}
```

Sumber: [security.md](https://docs.snowflake.com/en/user-guide/cortex-code/security.md),
[permission-modes.md](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-desktop/permission-modes.md).

### 4.3 Kategorisasi risiko dan SQL

CoCo mengklasifikasi operasi berdasarkan risiko ([security.md](https://docs.snowflake.com/en/user-guide/cortex-code/security.md)):

| Level | Contoh | Behavior |
|---|---|---|
| SAFE | `ls`, `cat`, `echo`, `grep` | auto-approved |
| LOW | buat file baru | biasanya auto-approved |
| MEDIUM | edit file, bash moderat | prompt di Confirm mode |
| HIGH | `rm`, `curl`, `wget`, `sudo` | selalu prompt |
| CRITICAL | `rm -rf`, operasi destruktif | konfirmasi ekstra |

SQL dikategorikan per tipe operasi:

| Kategori | Operasi | Behavior |
|---|---|---|
| READ_ONLY | `SELECT`, `SHOW`, `DESCRIBE` | **auto-approved** |
| WRITE | `INSERT`, `UPDATE`, `DELETE`, `CREATE` | prompt |
| USE_ROLE | `USE ROLE`, `USE WAREHOUSE` | prompt |

**Implikasi untuk Nova yang perlu dibaca teliti:** pola CoCo meng-auto-approve SQL read-only dan
mem-prompt write. Nova saat ini punya pembagian serupa di `sql_guard.py` (`is_destructive_sql`,
`is_unscoped_mutation`) tetapi **belum** mengklasifikasi read-only vs write untuk keputusan consent
tool. Lihat dokumen arsitektur untuk matriks opsinya.

### 4.4 Tool yang selalu prompt dan bypass organisasi

- Tool tertentu **tidak pernah** bisa auto-approve: `browser_evaluate`, `browser_run_code` (JS
  arbitrer di halaman hidup), `browser_read_clipboard` (clipboard bisa berisi token), subagents, dan
  tool destruktif yang memilih keluar dari auto-approval.
- Admin bisa mematikan Bypass fleet-wide via managed setting `permissions.dangerouslyAllowAll`.
- Environment variable `COCO_DANGEROUS_MODE_REQUIRE_SQL_WRITE_PERMISSION=true` memaksa prompt SQL
  write bahkan di bypass mode.

Sumber: [permission-modes.md](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-desktop/permission-modes.md), [security.md](https://docs.snowflake.com/en/user-guide/cortex-code/security.md).

### 4.5 Audit dan observabilitas

- Prompt trace CoCo disimpan di **`SNOWFLAKE.LOCAL.AI_OBSERVABILITY_EVENTS`** — bukan di sistem
  audit pengguna. Rating thumbs-up/down **tidak** masuk ke account monitoring data.
- View usage per permukaan: `CORTEX_CODE_SNOWSIGHT_USAGE_HISTORY`,
  `CORTEX_CODE_DESKTOP_USAGE_HISTORY`, `CORTEX_CODE_CLI_USAGE_HISTORY`.
- Nilai `INTERFACE` pada event: `cli`, `desktop`, `snowsight`.

Sumber: [observability.md](https://docs.snowflake.com/en/user-guide/cortex-code/observability.md),
[snowsight.md](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-snowsight.md).

**Implikasi Nova:** Snowflake memisahkan **product feedback observability** dari **account audit**.
Nova punya satu tabel `NOVA_SYSTEM.AUDIT_LOG`; keputusan apakah trace LLM (prompt/response) masuk
tabel yang sama adalah keputusan desain, bukan otomatis. Lihat dokumen arsitektur §audit.

### 4.6 Biaya / cost control

- CoCo ditagih per **token consumption**; tool yang dipanggil bisa menambah biaya terpisah
  (warehouse, Cortex Search, code execution).
- Di Cortex Agents ada **orchestration budget** per agent dan **per-user quota**.
- Snowsight: CoCo in Snowsight ditagih token untuk pelanggan Snowflake existing.

Sumber: [cortex-code.md](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code.md),
[cortex-agents.md](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents.md).

---

## 5. Integrasi ke SQL workspace

| Pertanyaan | Temuan | Keyakinan |
|---|---|---|
| Hide/show panel | Panel kanan dibuka dari ikon CoCo di kanan-bawah; "The CoCo panel opens on the right side" | Tinggi |
| Persistensi sesi | CoCo "maintaining context across the session"; integrasi Workspaces tahu file aktif. Mekanisme persistensi eksplisit (storage) **tidak terdokumentasi** untuk Snowsight | Sedang |
| Hasil bisa dimasukkan ke worksheet | **Tidak terdokumentasi.** Halaman menyebut SQL bisa "execute … or copy to clipboard" dan diff view bisa "apply them directly to the script". Tidak ada pernyataan bahwa result set masuk ke grid worksheet | Rendah |
| Di mana result set dirender (panel grid vs worksheet) | **Tidak terdokumentasi** di halaman yang diakses | — |
| Skills sebagai file | Personal skills di `.snowflake/cortex/skills/`; `AGENTS.md` di root workspace | Tinggi |
| Callback ke obrolan via teks terpilih (quick actions) | Design system Nova saat ini memakai `sonner`, `radix`, `monaco` — padanan CoCo adalah quick actions **Quick Edit, Format, Add to Chat, Explain** | Tinggi |

### 5.1 Arsitektur Cortex Agents di baliknya (relevan untuk Nova)

Coco memakai loop **Plan → Use tools → Reflect and respond**, dengan konsep:

- **Agent** = object schema-level yang membundel model + tools + instructions.
- **Tools**: Cortex Analyst (SQL terstruktur), Cortex Search (unstructured), code execution sandbox,
  Data to Chart, custom tools (stored proc/UDF), agent skills, MCP connectors, web search.
- **Thread** = konteks percakapan persisten (client tidak mengelola state).
- **Run** = satu request `agent:run`; agent mengeluarkan **events** yang menampilkan reasoning, tool
  call, dan refleksi.
- **Access control**: butuh role `SNOWFLAKE.CORTEX_USER` atau `CORTEX_AGENT_USER` + privilege pada
  object yang dipakai tool. Session permission ditentukan dari **default role** user.

Sumber: [cortex-agents.md](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents.md).

**Ini adalah cetak biru yang paling langsung bisa dipetakan ke Nova** — dengan perbedaan krusial:
pada Snowflake, RBAC ditegakkan engine di jalur eksekusi; pada Nova, RBAC ditegakkan StarRocks
melalui **koneksi user** (lihat dokumen arsitektur §8 dan desain tool `query_execute`).

---

## 6. Padanannya

### 6.1 Cortex Analyst / Cortex Agents / Snowflake Intelligence

| Sumbu | Cortex Analyst | Cortex Agents | CoCo |
|---|---|---|---|
| Fokus | text-to-SQL terkurasi via **Semantic Views** | platform agent: plan/tool/reflect | produk agentic coding+admin |
| Tool execution | Tidak — mengembalikan SQL; eksekusi terpisah | Ya, loop penuh dengan banyak tool | Ya, termasuk SQL |
| Consent | N/A (bukan agent eksekutor) | Kebijakan privilege per tool; bukan model allow/deny UI | **Allow once / for session / always allow** |
| UI placement | Di-embed aplikasi via REST API; bisa Streamlit | Snowflake Intelligence + Cortex Code | Panel kanan Snowsight / Desktop IDE / CLI |
| Multi-turn | Ya — kirim `messages[]`; **tidak** melihat hasil query sebelumnya | Threads | Ya |
| Akses | `CORTEX_USER` / `CORTEX_ANALYST_USER` | `CORTEX_USER` / `CORTEX_AGENT_USER` | `COPILOT_USER` + `CORTEX_USER`/`CORTEX_AGENT_USER` |

Sumber: [cortex-analyst.md](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst.md), [cortex-agents.md](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents.md).

**Catatan penting:** Snowflake merekomendasikan transisi dari Cortex Analyst ke **Cortex Agents**
("supports every Cortex Analyst capability with higher answer quality"). Legacy **Snowflake Copilot**
sudah deprecated demi CoCo.

### 6.2 Databricks Genie

Keluarga produk (3 bagian):

- **Genie One** — antarmuka sederhana untuk business user menemukan & berinteraksi dengan data.
- **Genie Agents** — environment domain-specific tempat data team mengonfigurasi trusted data,
  metrics, business rules yang menopang jawaban Genie One.
- **Genie Code** — AI coding & data assistant untuk developer di dalam workspace.

Semua di-grounding ke data organisasi dan di-govern melalui **Unity Catalog**. Genie Code ditagih
pay-as-you-go dengan allowance bulanan gratis per user sejak 8 Juli 2026; Genie One & Genie Agents
gratis user hingga 31 Januari 2027.

Keyakinan: Tinggi. Sumber: [Databricks Genie](https://docs.databricks.com/aws/en/genie/) (Last
updated Sep 11, 2026).

**Poin arsitektural untuk Nova:** Databricks memisahkan **kurasi domain (Genie Agents)** dari
**permukaan konsumsi (Genie One)** dari **coding assistant (Genie Code)**. Ini pola yang lebih
matang daripada satu panel monolitik.

### 6.3 Power BI Copilot

- **Copilot pane** di sisi kanan report (GA) — tanya jawab & ringkasan tentang report yang terbuka.
- **Standalone Copilot** (preview) — full-screen, lintas item, cari & analisis apa pun yang user
  punya akses.
- **Copilot in apps** (preview) — scoped ke konten app, mendukung **verified answers** dari penulis
  app.
- **Copilot for report authors** — buat/edit report, tulis DAX, deskripsi measure.
- Persyaratan: kapasitas Fabric berbayar (F2+) / Premium P1+; tenant setting Azure OpenAI; region
  didukung; tidak untuk sovereign cloud.
- Prompt limit 10.000 karakter; cache 24 jam untuk prompt identik pada model tak berubah;
  tombol **clear chat** mengosongkan konteks.

Keyakinan: Tinggi. Sumber: [Power BI Copilot overview](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-introduction) (ms.date 2026-08-24).

### 6.4 BigQuery Gemini

- Halaman yang tersedia mendokumentasikan **Gemini assist** untuk menulis/menjelaskan query,
  Gemini untuk data preparation, dan **conversational analytics agent** (data agents + conversations).
- **Tidak ditemukan** padanan CoCo yang setara (agentic coding di UI SQL workspace dengan model
  approval allow/deny). Tidak diklaim ada.

Keyakinan: Sedang (karena sebagian navigasi doc gagal/terpotong). Sumber:
[Write queries with Gemini](https://cloud.google.com/bigquery/docs/write-sql-gemini).

### 6.5 Tabel perbandingan ringkas

| Engine | Kemampuan | Eksekusi tool | Consent model | UI placement |
|---|---|---|---|---|
| **Snowflake CoCo** | coding + admin + data + ML + dbt + notebook | **Ya**, SQL/DDL/browser/file | allow once / session / always allow; default/bypass | panel kanan Snowsight; Desktop IDE; CLI |
| **Cortex Agents** | orchestrasi multi-tool | **Ya** (engine-side) | privilege per tool, bukan UI allow/deny | REST/API, Snowflake Intelligence |
| **Cortex Analyst** | text-to-SQL via semantic view | **Tidak** | N/A | embed via REST |
| **Databricks Genie** | Genie One (konsumen), Genie Code (dev), Genie Agents (kurasi) | Ya (Genie Code) | tidak dispesifikasikan di halaman ini | workspace Databricks + mobile |
| **Power BI Copilot** | Q&A report, DAX, buat report, verified answers | Terbatas (DAX/report) | tenant admin switch | **panel kanan** report; standalone; apps |
| **BigQuery Gemini** | assist SQL, data prep | Terbatas | N/A | console |

---

## 7. Yang belum terverifikasi / belum diketahui

Daftar ini adalah deliverable eksplisit dari workstream dan **tidak boleh** diisi dengan asumsi:

1. **Expansion akronim "CoCo"** — tidak terdokumentasi di seluruh halaman yang diakses.
2. **Tanggal GA / launch produk CoCo** — tidak ada. Changelog hanya memberi tanggal per-fitur.
   Artefak paling awal `1.0.6` (2026-02-04) bukan klaim tanggal launch.
3. **Release notes CoCo Desktop** — halaman `.../cortex-code-desktop/release-notes.md` mengembalikan
   **404**, sehingga versi dan tanggal GA Desktop belum terverifikasi.
4. **Apakah CoCo merender result set SQL ke worksheet atau ke grid panel di Snowsight** — tidak
   terdokumentasi. Yang ada hanya: SQL dapat dieksekusi atau disalin, dan diff dapat di-apply ke
   script.
5. **Mekanisme persistensi sesi Snowsight** (di mana state percakapan disimpan, apa yang di-remember
   antar sesi) — tidak terdokumentasi, hanya "context across the session".
6. **Schema tool-call internal CoCo** (nama tool, payload) — tidak dipublikasikan.
7. **Padanan agentic BigQuery yang setara CoCo** — tidak ditemukan; hanya Gemini assist biasa.
8. **Apakah "Always Allow" Snowsight persisten lintas browser/device atau hanya per browser** —
   hanya "remembered per browser" yang disebut.
9. **Apakah ada halaman docs.starrocks.io resmi untuk MCP server** — `docs.starrocks.io/docs/developers/mcp/`
   mengembalikan 404. Repo `StarRocks/mcp-server-starrocks` ada (Apache-2.0, v0.4.0 2026-05-05), tapi
   tanpa halaman dokumentasi kanonik yang terverifikasi.

---

## 8. Keputusan yang dibutuhkan dari team lead (workstream ini)

1. **Apakah Nova menargetkan paritas CoCo penuh (coding + admin + dbt + browser) atau subset
   "SQL assistant + query tool + skills" saja?** Paritas penuh adalah multi-fase dan menyentuh
   hampir semua modul backend; subset jauh lebih murah dan langsung berguna.
2. **Apakah Nova mengadopsi model consent bertingkat CoCo (allow once / session / always allow)
   atau cukup ask-per-call + session?** "Always allow" persisten menuntut tabel state baru di
   `NOVA_SYSTEM` dan model scope yang eksplisit — lihat dokumen arsitektur & query-tool.
3. **Apakah trace LLM (prompt/response) boleh disimpan di `NOVA_SYSTEM`?** Snowflake memisahkan
   product observability dari account audit; Nova harus memutuskan satu tabel atau dua. Ini juga
   berbatasan dengan "tidak ada credential di riwayat percakapan".

---

## Provenance

Semua URL diakses **2026-09-18**.

| Klaim | Sumber |
|---|---|
| CoCo = produk; tiga permukaan; edition; billing | cortex-code.md |
| Panel kanan Snowsight; kemampuan; prompt contoh; sandbox model; skills; security; `@` context | cortex-code-snowsight.md |
| Permission mode Desktop (allow once/session/always/reject; always-prompt tools) | cortex-code-desktop/permission-modes.md |
| Permission CLI, risk levels, SQL categories, permission cache, sandbox, managed settings, checklist security | security.md |
| Skills (SKILL.md, sumber, publish, catalog, settings path) | cortex-code-desktop/skills.md |
| Changelog CLI + versi | changelog.md |
| Changelog Snowsight + tanggal GA consent SQL (29 Apr) & approval modes (16 Sep) | cortex-code-snowsight/changelog.md |
| Observability & view usage | observability.md |
| Cortex Agents (loop, tools, thread, RBAC) | cortex-agents.md |
| Cortex Analyst (semantic views, multi-turn limit, RBAC) | cortex-analyst.md |
| Databricks Genie (One/Agents/Code, Unity Catalog, pricing) | docs.databricks.com/aws/en/genie/ |
| Power BI Copilot (pane kanan, standalone, verified answers, requirements) | learn.microsoft.com/power-bi/create-reports/copilot-introduction |
| BigQuery Gemini assist | cloud.google.com/bigquery/docs/write-sql-gemini |
| StarRocks MCP server repo (Apache-2.0, v0.4.0, commit 2026-09-08) | github.com/StarRocks/mcp-server-starrocks |
