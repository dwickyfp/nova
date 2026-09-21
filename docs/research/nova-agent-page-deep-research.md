# Riset Mendalam: Nova "Agent" Page

> **Workstream:** lanjutan NOVA-61 (Agentic Assistant) menuju **Agent Platform**.
> **Status:** riset + desain, **bukan** implementasi. Tidak ada kode produksi yang disentuh.
> **Engine pin:** StarRocks 4.1.x. **Tanggal akses sumber web:** 2026-09-20.
> **Aturan:** tidak ada secret/credential di dokumen ini. Semua klaim faktual punya sumber +
> tingkat keyakinan; yang tidak terverifikasi ditandai eksplisit.

---

## 0. Ringkasan eksekutif

User meminta **Agent page** yang terinspirasi **Snowflake Cowork / Cortex Agents**: user bisa
membuat agent, mengisi *instructions* dan *response output*, memasang **default tools** dan
**default skills**, menambah skill sendiri, lalu **query database via semantic layer** (setara
Cortex Analyst + Cortex Search), dengan **HITL** dan **render chart di chat**.

Temuan inti:

1. **Nova sudah punya fondasinya.** NOVA-61 sudah terimplementasi di `main`: modul backend
   `backend/app/modules/assistant/` (loop, SSE, consent, tool registry, skill library) dan
   frontend `frontend/src/features/assistant/`. Yang diminta user adalah **evolusi**, bukan
   greenfield: dari **satu assistant tertanam** menjadi **platform banyak agent yang bisa
   dikonfigurasi user**, plus **semantic layer** dan **chart**.
2. **Standar semantic = Apache Ossie (incubating)** — dulu **Open Semantic Interchange (OSI)**.
   YAML, Apache-2.0, vendor-neutral; mendefinisikan `datasets`, `fields`, `metrics`,
   `dimensions`, `relationships`, `ai_context`. Ini **cocok persis** sebagai format semantic
   layer Nova, dan **Snowflake adalah salah satu peserta** — jadi sejalan dengan inspirasi user.
3. **Engine agentic: rekomendasi tetap loop sendiri (bukan framework).** Riset 2026 menunjukkan
   framework matang (LangGraph, Pydantic AI, Strands, OpenAI Agents SDK) tapi semuanya
   memperkenalkan bentuk state + tool contract permanen. NOVA-61 sudah membuktikan loop sendiri
   cukup, dan kebutuhan baru (banyak agent, semantic tool, chart) **tidak** menuntut graph
   durable. Framework dicatat sebagai revisit-trigger.
4. **Semantic querying bisa dibangun native di atas StarRocks**, tanpa engine baru: Ossie YAML →
   prompt grounding (Cortex-Analyst-style) → LLM menghasilkan SQL → dijalankan lewat
   `QueryService` yang sudah ada (guard + audit + redaksi otomatis).
5. **Chart output diselesaikan dengan Vega-Lite spec**, mengikuti pola persis Snowflake
   (`data_to_chart` tool mengembalikan `chart_spec` JSON string; event `response.chart`).
   Frontend merender Vega-Lite, bukan menyerahkan chart ke LLM dalam bentuk gambar.
6. **Semua invariant Nova tetap berlaku**: delegate-first (koneksi user), tanpa credential di
   UI/state, single database (`NOVA_SYSTEM`), `@stage` sacred, ACCOUNTADMIN immutable.

**Rekomendasi bentuk:** **Phase 11 — Agent Platform (bounded)**, dibangun di atas NOVA-61,
dengan 4 blok: (A) Agent registry + builder UI, (B) Semantic layer (Ossie) + semantic tools,
(C) Chart + rich artifacts di chat, (D) HITL bertingkat per-agent.

---

## 1. Apa yang sudah ada (fakta kode di `main`)

Ini penting: user meminta sesuatu yang **tumpang tindih** dengan NOVA-61. Jangan bangun ulang.

| Lapisan | Yang sudah ada | Bukti |
|---|---|---|
| Backend loop | `AssistantLoop.run()` — plan → act → observe → answer, iteration cap 8, time budget 60s | `backend/app/modules/assistant/service.py:85,149` |
| SSE events | `text_delta`, `thinking`, `plan`, `tool_call`, `tool_status`, `done`, `error`, `ping` | `backend/app/modules/assistant/events.py:31-40` |
| Consent | state machine per-thread, in-memory, read-only grant (E2b) | `backend/app/modules/assistant/consent.py`, `state.py` |
| Tools | `load_skill` (pure) + `query_execute` (delegate-first, read-only allowlist) | `backend/app/modules/assistant/tools/` |
| Skills | `skill_library/*.md` (11 skill), frontmatter catalog + progressive disclosure | `backend/app/modules/assistant/skill_library/`, `skill_registry.py` |
| System prompt | persona "Nove", authoring-vs-executing, scope boundary, writing style anti-slop | `service.py:440-528` |
| API | thread CRUD, grant, messages (SSE), tool-call decision | `backend/app/modules/assistant/router.py:119-375` |
| Frontend | dock/panel/toggle, transcript, composer, turn hook, event parser | `frontend/src/features/assistant/*` |
| Chart lib | **recharts 3.8.1** sudah ada | `frontend/package.json` |
| Markdown | **react-markdown 10.1** sudah ada | `frontend/package.json` |

**Konsekuensi desain:** Agent page = **registry agent (CRUD)** + **builder** + **semantic tools**
+ **chart artifact** + **HITL bertingkat**, di atas loop dan transport yang sudah terbukti.

---

## 2. Semantic layer: Apache Ossie

### 2.1 Apa itu (terverifikasi)

- **Nama resmi:** **Apache Ossie (incubating)**. Sebelumnya **Open Semantic Interchange (OSI)**.
  Masuk Apache Incubator pada 10 Juli 2026. Lisensi **Apache-2.0**. ~2.1k star.
  Sumber: `https://ossie.apache.org/`, `https://github.com/apache/ossie`,
  `https://ossie.apache.org/updates/ossie-enters-apache-incubator/` — **keyakinan tinggi**.
- **Tujuan:** standar pertukaran **semantic model** lintas alat AI/BI, YAML, vendor-neutral,
  "single source of truth" untuk metrik/dimensi. Sumber: halaman utama Ossie — **tinggi**.
- **Versi spec saat akses:** `0.2.0.dev0` (DRAFT; skema masih bisa berubah) dan `0.1.1`
  (2025-12-11) rilis awal. Sumber: `core-spec/spec.md` — **tinggi**.

### 2.2 Skema inti (terverifikasi dari spec)

Dokumen = **satu** semantic model. Field dokumen: `version` (req), `name` (req),
`description`, `ai_context`, `datasets` (req, non-empty), `relationships`, `metrics`,
`custom_extensions`.

**Built-in classes:**

| Class | Field kunci | Sumber |
|---|---|---|
| **Dataset** | `name`, `source` (db.schema.table atau query), `primary_key`, `unique_keys`, `description`, `ai_context`, `fields`, `custom_extensions` | spec §Datasets |
| **Field** | `name`, `expression` (req), `dimension`, `label`, `description`, `datatype`, `ai_context`, `custom_extensions` | spec §Fields |
| **Relationship** | `name`, `from`, `to`, `from_columns[]`, `to_columns[]` (simple & composite) | spec §Relationships |
| **Metric** | `name`, `expression` (req), `description`, `datatype`, `ai_context` (bisa cross-dataset) | spec §Metrics |
| **Dimension** | metadata `{ is_time: bool }` (role flag, independen dari `datatype`) | spec §Dimension Object |
| **Expression** | `{ dialects: [{ dialect, expression }] }`; dialek termasuk `ANSI_SQL`, `SNOWFLAKE`, `BIGQUERY`, `DATABRICKS`, `DAX`, … | spec §Expression Object |
| **Data types** | `String, Integer, Decimal, Float, Boolean, Date, Time, DateTime, DateTimeTz, Opaque` | spec §Data types |
| **ai_context** | string **atau** objek `{ instructions, synonyms[], examples[] }` | spec §AI Context Structure |
| **custom_extensions** | `[{ vendor_name, data(JSON string) }]`; mis. `DBT`, `SNOWFLAKE` | spec §Custom Extensions |

**Catatan versi penting:** `0.2.0.dev0` **breaking** — dokumen standalone punya model di root;
array `semantic_model` **dihapus**. Dokumen lama harus dimigrasikan. Karena spec masih draft,
Nova harus **mem-pin versi** yang didukung dan **menolak** bentuk yang tidak dikenal
(fail-closed), bukan menebak.

**Poin krusial untuk Nova:** Ossie adalah **format pertukaran**, **bukan execution engine**.
Spec tidak mendefinisikan cara mengubah metric/dimension menjadi SQL. Ini persis celah yang
disebut analisis pihak ketiga (`colrows.com/blogs/open-semantic-interchange`) — dan celah itu
**harus Nova isi sendiri** (lihat §4).

### 2.3 Bentuk Ossie yang dipakai Nova (usulan)

Simpan sebagai file `.ossie.yaml` di workspace/stage (versioned), bukan tabel. Contoh minimum
yang mutatis mutandis dipetakan ke StarRocks:

```yaml
version: 0.2.0.dev0
name: sales_analytics
description: Sales and customer analytics
ai_context:
  instructions: "Use for revenue and customer questions"
datasets:
  - name: orders
    source: analytics.public.orders
    primary_key: [order_id]
    fields:
      - name: order_date
        expression: { dialects: [{ dialect: ANSI_SQL, expression: order_date }] }
        datatype: Date
        dimension: { is_time: true }
      - name: amount
        expression: { dialects: [{ dialect: ANSI_SQL, expression: amount }] }
relationships:
  - name: orders_to_customers
    from: orders
    to: customers
    from_columns: [customer_id]
    to_columns: [id]
metrics:
  - name: total_revenue
    expression: { dialects: [{ dialect: ANSI_SQL, expression: "SUM(orders.amount)" }] }
    ai_context: { synonyms: ["revenue", "total sales"] }
```

**Aturan mapping Nova:**
- `dialect` yang diutamakan: **`ANSI_SQL`** (StarRocks kompatibel). Tulis converter/dialect note
  untuk perbedaan StarRocks bila ada; jangan asumsikan ANSI = StarRocks penuh.
- `source` = `database.schema.table` Nova (termasuk `@stage` bila memungkinkan? → **tidak** di
  v1; semantic model mengacu tabel fisik).
- `primary_key`/`unique_keys`/`relationships` = metadata join; **tidak** dieksekusi engine.
- Ekstensi Nova ditulis di `custom_extensions` dengan `vendor_name: NOVA` (mis. kompatibilitas
  `@stage`, hint agregasi), **tidak** mengubah core spec.

---

## 3. Engine agentic: pilihan & rekomendasi

### 3.1 Temuan riset 2026 (terverifikasi via PyPI/GitHub API + docs resmi)

| Framework | Versi (2026-09) | Lisensi | Plan | Tool | Stream | HITL interrupt/resume | Durable | Provider-agnostic | Bobot |
|---|---|---|---|---|---|---|---|---|---|
| **LangGraph** | 1.2.11 | MIT | graf, bukan primitive | ✅ | ✅ `stream_events()` | ✅ `interrupt()`+`Command(resume)` | ✅ checkpointer | ✅ | sedang |
| **Pydantic AI** | 2.46.0 | MIT | `planning` capability | ✅ | ✅ | ✅ deferred tools | ✅ Temporal/DBOS/dll | ✅ | ringan (`-slim` 9 dep) |
| **OpenAI Agents SDK** | 0.22.3 | MIT | reasoning model | ✅ | ✅ | ✅ `needs_approval`+`RunState` | ⚠ app-owned | ⚠ OpenAI-first | berat (56 dep) |
| **Google ADK** | 2.9.2 | Apache-2.0 | graph + LLM | ✅ | ✅ `/run_sse` | ✅ `require_confirmation` | ⚠ | ✅ | sangat berat (259 dep) |
| **LlamaIndex** | 0.14.24 | MIT | ReAct/Function | ✅ | ✅ | ✅ `InputRequiredEvent` | ⚠ manual | ✅ | modular |
| **CrewAI** | 1.15.22 | MIT | `reasoning=True` | ✅ | ✅ `StreamFrame` | ✅ `@human_feedback` | ✅ `@persist` | ✅ (LiteLLM) | sedang |
| **MS Agent Framework** | 1.19.0 | MIT | Harness planning | ✅ | ✅ | ✅ | ⚠ | ✅ | sedang |
| **Strands Agents** | 1.56.0 | Apache-2.0 | model-driven | ✅ | ✅ async | ✅ `interrupt()` | ✅ session snapshot | ✅ | ringan (13) |
| **AutoGen** | 0.7.5 | MIT/⚠ CC-BY repo | group chat | ✅ | ⚠ | ⚠ | ❌ | ✅ | — (superseded) |
| **Mastra** | 1.67.0 | Apache-2.0 | agent+workflow | ✅ | ✅ | ✅ `requireApproval` | ✅ | ✅ | **TypeScript — bukan Python** |
| **Temporal** | 1.33.0 | MIT | bukan agent | — | ✅ | ✅ signal | ✅ inti | ✅ | butuh server |

Sumber: PyPI JSON, GitHub API, docs masing-masing (lihat §11). **Keyakinan tinggi** untuk
versi/lisensi; **sedang** untuk klaim fitur (granularitas doc berbeda).

**Agent Skills standard (SKILL.md)** — terverifikasi dari `agentskills.io/specification` +
docs Anthropic:
- Frontmatter: `name` (req, 1-64, kebab-case), `description` (req, ≤1024, sebutkan *apa* +
  *kapan*), `license`, `compatibility` (≤500), `metadata` (map string), `allowed-tools`
  (eksperimental).
- **Progressive disclosure 3 level:** metadata (~100 token, selalu) → body SKILL.md (<5000 token,
  saat di-aktifkan) → resources (`scripts/`, `references/`, `assets/`, saat perlu).
- Diimplementasikan Google ADK (`SkillToolset`), Strands (`AgentSkills`), Pydantic AI
  (`defer_loading`), OpenAI Agents SDK (sandbox Skills).

### 3.2 Rekomendasi: **tetap loop sendiri**

Alasan:
1. **Kontinuitas.** Nova sudah punya loop yang lulus spec NOVA-61 (bounded, consent, SSE, skill).
   Mengganti ke framework = membuang aset + risiko regresi pada invariant credential/audit.
2. **Kebutuhan baru tidak menuntut graph.** Multi-agent builder + semantic tool + chart adalah
   **penambahan tool dan konfigurasi**, bukan orkestrasi multi-node durable. Loop dengan
   `max_iterations` + tool registry dinamis **sudah cukup**.
3. **Credential/audit.** Loop Nova memanggil `QueryService` in-process pada koneksi user
   (delegate-first). Framework asing menambah lapisan yang bisa mem-bypass pipeline tanpa terlihat.
4. **Anggaran.** Dependency baru (langchain-core, 56-259 dep) tidak sepadan untuk kebutuhan ini.

**Revisit trigger (eksplisit):** jika muncul kebutuhan nyata (a) multi-agent hand-off yang
durable-resumable lintas restart, (b) checkpointing percakapan panjang dengan compaction
otomatis, atau (c) paralelisme subgraph. Saat itu, kandidat pertama **Pydantic AI** (paling tipis,
typed, cocok Pydantic Nova) atau **LangGraph** (interrupt/resume paling eksplisit).

### 3.3 Bentuk loop Nova yang diperluas (habit agentic yang user minta)

User menyebut 4 habit: **Planning → Load skill → Call tools → Output**. Loop Nova yang ada sudah
mengikuti bentuk ini (`events.plan` → `load_skill` → `query_execute` → `answer`). Yang **ditambah**
untuk Agent page:

```
1. PLANNING
   - Agent config (instructions + response_style + default_tools + default_skills) di-inject
     ke system prompt, menggantikan persona global "Nove".
   - Emit `plan` frame dari template agent (bukan hardcoded Nova phases).
2. LOAD SKILL  (progressive disclosure, Anthropic-compatible)
   - Katalog skill agent = default_skills(agent) ∪ user_skills(user) ∪ builtin.
   - Model memanggil `load_skill(name)`; body di-load on demand.
3. CALL TOOLS  (registry per-agent)
   - Semantic tools: `semantic_query` (Analyst-style), `semantic_search` (Search-style),
     `data_to_chart` (Vega-Lite).
   - Data tools: `query_execute` (read-only, read-only allowlist) — existing.
   - Setiap tool punya `classification` (read_only/destructive/denied) → HITL.
4. OUTPUT
   - Text delta + rich content blocks: `table`, `chart`, `citation`, `sql`.
   - `data_to_chart` mengembalikan `chart_spec` (Vega-Lite JSON) → event `chart`.
```

---

## 4. Semantic querying: cortex-analyst-style di atas StarRocks

Ini bagian paling "baru" dan paling berisiko. Snowflake menyembunyikan mesin ini; Nova harus
membangunnya. Desain bertahap:

### 4.1 `semantic_query` (Analyst-style text-to-SQL)

**Input:** `{ question, semantic_model_ref }`
**Langkah backend (deterministik + LLM):**
1. **Load & validasi** Ossie YAML (fail-closed pada versi/bentuk tak dikenal). Validasi:
   `datasets` non-empty; `source` resolvable; expression dialect ada.
2. **Build grounding prompt** dari model: dataset + field + metric (name, description,
   `ai_context.synonyms`, `examples`) + relationships. **Hanya metadata**, tanpa baris data.
3. **Verified queries** (opsional, mengikuti Cortex VQR): pasangan question→SQL yang
   di-verifikasi user, disimpan per semantic model; digunakan sebagai few-shot saat pertanyaan
   mirip. Snowflake membuktikan ini menaikkan akurasi. Sumber: docs Cortex Analyst VQR — **tinggi**.
4. **LLM menghasilkan SQL** dengan aturan: hanya pakai nama logis dari model; dialect ANSI_SQL;
   kembalikan juga `explanation` + `confidence`.
5. **Eksekusi lewat `QueryService.execute_statements`** pada koneksi user → guard + audit +
   redaksi otomatis. Tidak pernah koneksi sendiri.
6. **Kembalikan** `{ sql, rows, columns, explanation, verified_query_used }` sebagai content
   block, dan event `tool_result`.

**Batas yang harus jujur:** LLM bisa salah menulis SQL. Karena itu (a) verified queries penting,
(b) user melihat SQL sebelum/sesudah (transparansi), (c) read-only + audit tetap berlaku.

### 4.2 `semantic_search` (Search-style, unstructured) — **native StarRocks, tanpa LLM**

**Revisi berdasarkan verifikasi engine (2026-09-20).** Pendekatan awal "keyword LIKE + LLM
rerank" diganti dengan **full-text inverted index native StarRocks**. Sumber terkuat bukan
dokumentasi vendor, melainkan **test Nova sendiri terhadap engine 4.1.4**:
`backend/tests/integration/test_inverted_index_l3.py`, dirangkum di `docs/24-advanced-indexes.md`.

**Fakta terverifikasi pada engine 4.1.4** (`docs/24-advanced-indexes.md`):

| Fakta | Detail |
|---|---|
| Inverted index native | GIN, sejak 3.3; **built-in implementation sejak v4.1** (bitmap-based, mendukung shared-data; CLucene tidak). |
| Predicate | `<col> MATCH 'kata'`, `<col> MATCH_ANY 'kata1 kata2'`, `<col> MATCH_ALL 'kata1 kata2'` — **predicate, bukan function**; keyword dipisah **spasi**, bukan koma. |
| Pushdown-only | Harus di `WHERE` pada kolom GIN; di luar itu error 1064. |
| Tipe kolom | Wajib `STRING`/VARCHAR/CHAR **NOT NULL**. |
| Preconditions | `enable_experimental_gin=true` (FE config, default off di 4.1.4); `replicated_storage=false`; schema change **asynchronous** (~14 s), ALTER kedua saat in-flight ditolak. |
| Parser | allow-list 4.1: `none` (default), `english`, `chinese`, `standard`, `unicode`. **`ngram` BUKAN nama parser yang valid.** |
| Case | Tokenisasi english/standard me-lowercase; keyword wajib lowercase. |
| **Relevance scoring** | **`ORDER BY score` TIDAK tersedia di jalur predicate 4.1.4.** Hasil **filtered, not ranked**. Tercatat `DEFER` di `docs/gap-analysis.md:210,216`. |

**Konsekuensi untuk desain:** karena BM25/`ORDER BY score` **tidak ada** di engine ini, **LLM
rerank tidak punya skor untuk dipakai**. Maka:

- **v1 = NATIVE ONLY, tanpa LLM:** `semantic_search(query)` memecah query menjadi term, menjalankan
  `MATCH_ANY` (atau `MATCH_ALL` untuk mode ketat) pada kolom teks yang diindeks, lalu mengembalikan
  baris **tanpa peringkat**. Urutan ditentukan aplikasi (mis. `LIMIT` + urutan deterministik),
  bukan skor.
- **LLM rerank = opsional, hanya bila terbukti perlu.** Ini **bukan** bagian v1. Kalau korpus
  besar dan hasil tak berperingkat menyulitkan, opsi lanjutan adalah memakai LLM **sebagai
  ranker atas kandidat terbatas** (bukan pencari) — tetapi itu keputusan v2 dengan data nyata.
- **Vector search** (setara Cortex Search penuh) tetap di luar cakupan sampai engine mendukung
  scoring/vector path yang dapat diandalkan.

**Mengapa ini lebih baik:** memakai kemampuan engine (bukan membangun mesin pencari sendiri),
lebih sederhana, **tanpa biaya LLM per query**, dan konsisten dengan API `/api/v1/indexes` yang
sudah ada di Nova.

**Integrasi Nova yang sudah tersedia (reuse, bukan bangun baru):**
- `GET /api/v1/indexes/capabilities` — cek precondition sebelum menawarkan search.
- `POST /api/v1/indexes/create` — buat GIN index pada kolom teks.
- `POST /api/v1/indexes/validate-predicate` — validasi bentuk predicate.
- `backend/app/modules/indexes/router.py:214` `validate_fulltext_predicate`.

**Cortex Search** (pembanding) tetap melakukan vector + keyword + semantic rerank dengan hak
owner. Sumber: docs Cortex Search — **tinggi**. Nova **tidak** mengklaim setara; `semantic_search`
v1 = keyword full-text native, dan itu disebut apa adanya.

### 4.3 Mengapa bukan Cortex Analyst API / MCP StarRocks

- Cortex Analyst = API Snowflake; tidak berlaku di StarRocks.
- `StarRocks/mcp-server-starrocks` ada (Apache-2.0) tapi **melewati pipeline Nova** (tanpa
  guard/audit/redaksi) → **dilarang** sebagai jalur eksekusi (konsisten dengan NOVA-61 §5).

---

## 5. Chart rendering di chat

### 5.1 Pola Snowflake (terverifikasi)

- Tool **`data_to_chart`** (`type: data_to_chart`) membuat visualisasi dari data hasil tool lain.
- Event streaming **`response.chart`**: `{ content_index, tool_use_id, chart_spec }`.
- **`chart_spec` adalah string JSON** berisi **Vega-Lite v5**; ada juga `ChartContent`.
- Ada event `response.table` dengan `result_set` (skema ResultSet SQL API) dan `title`.
Sumber: `cortex-agents-run.md`, `cortex-agents-manage.md` — **keyakinan tinggi**.

### 5.2 Desain Nova

**Prinsip: chart adalah artifact terstruktur, bukan gambar, bukan markdown.**

- Tool `data_to_chart(rows, columns, intent) -> chart_spec(Vega-Lite v5 JSON)`.
  - Model memilih tipe chart (bar/line/pie/scatter) + encoding dari data yang sudah diambil
    (biasanya dari `semantic_query`/`query_execute` dalam turn yang sama).
  - **Fallback deterministik:** jika LLM gagal, backend membuat Vega-Lite sederhana
    (dimensi waktu → line, kategori+nilai → bar). Jangan pernah mengirim chart rusak.
- **Event SSE baru:** `chart` `{ content_index, tool_use_id, chart_spec }` — **meniru persis**
  bentuk Snowflake agar mudah dipetakan.
- **Frontend:** render Vega-Lite. Pilihan library:
  - `vega-embed` + `vega-lite` (paling langsung, sesuai spec).
  - atau konversi ke **recharts** (sudah ada) untuk chart sederhana; tapi ini kehilangan
    generalitas Vega-Lite. Rekomendasi: **vega-embed** untuk artifact, recharts tetap untuk
    dashboard internal.
- **Keamanan render:** Vega-Lite punya ekspresi; render di **sandbox iframe** atau dengan
  `actions:false` + sanitasi spec (whitelist mark/encoding, tolak `expr` arbitrary). Ini
  titik rawan XSS yang harus diuji, bukan diasumsikan aman.
- **Tabel:** event `table` dengan `{ title, columns, rows }`; frontend pakai komponen tabel Nova
  (tanstack-table sudah ada).

**Kenapa Vega-Lite, bukan recharts langsung untuk chat:** karena (a) identik dengan standar
industri yang user maksud, (b) model LLM sangat mahir menghasilkan Vega-Lite JSON, (c) spec
portable dan bisa diekspor. recharts adalah React component tree — LLM tidak bisa
menghasilkannya dengan andal.

---

## 6. HITL (Human-in-the-loop)

### 6.1 Apa yang Snowflake dokumentasikan (terverifikasi)

- Cortex Agents: tool use memancarkan `response.tool_use` dengan field `permission`
  (`ToolUsePermission`, contoh `["Allow Once","Deny"]`), plus konsep `is_elicitation` (agent
  meminta klarifikasi) dan "analytical search menyajikan rencana untuk direview sebelum eksekusi".
- **Penting:** Snowflake **tidak** mendokumentasikan** kelas read-only-auto/write-approval yang
  eksplisit** untuk Cortex Agents API (hanya contoh `Allow Once`/`Deny`). Jadi model bertingkat
  yang dipakai NOVA-61 berasal dari CoCo (Desktop/CLI/Snowsight), bukan dari API Agents.
  Sumber: `cortex-agents-run.md`; CoCo `permission-modes.md` — **tinggi** untuk CoCo, **sedang**
  untuk klaim Agents.
- Model CoCo (dokumented): **Allow once / Allow for session / Always allow / Reject**; SQL
  dikategorikan READ_ONLY (auto) vs WRITE/USE_ROLE (prompt); tool tertentu **selalu** prompt.
  Sumber: `agentic-assistant-snowflake-coco.md` §4.2-4.3 — **tinggi**.

### 6.2 Desain HITL Nova (diperluas dari NOVA-61)

NOVA-61 sudah punya: state `pending → approved|denied`, grant per-conversation in-memory,
read-only only. Untuk Agent page, tambahkan **konfigurasi per-agent** (tetap fail-closed):

| Tingkat | Perilaku | Default |
|---|---|---|
| `auto_read_only` | tool `read_only` auto-run; `destructive`/`denied` selalu prompt | **default agent** |
| `ask_every_tool` | semua tool prompt (kecuali `load_skill`) | opsi |
| `never` (bypass) | **tidak disediakan di v1** (NOVA-61 §5: dunia DDL/admin) | — |

**Aturan yang tidak boleh dilanggar:**
- `destructive` **tidak pernah** auto-approve, walau agent dikonfigurasi `auto_read_only`.
- `semantic_query` dan `query_execute` keduanya read-only → bisa auto; **tetap** lewat guard.
- Grant tetap **in-memory, per-conversation** di v1 (E2b). Persistensi = keputusan lanjutan
  (butuh tabel grant yang **tidak bisa** memuat SQL — lihat NOVA-61 T-B3).
- **Elicitation** (agent bertanya balik) adalah output text, bukan tool; tidak butuh consent.

**Wire contract** mengikuti pola NOVA-61: approval lewat HTTP terpisah
(`POST /assistant/tool-calls/{id}/decision`) agar tidak half-duplex di SSE. Untuk Agent page,
endpoint pindah ke prefix `/agents/...` (lihat §7).

---

## 7. Desain halaman Agent

### 7.1 Struktur navigasi

Nova punya sidebar + `authenticated-layout.tsx`. Agent page adalah **halaman utama baru**
(mis. `/agents`), bukan sekadar panel kanan. Dua permukaan:

1. **Agents list** — daftar agent yang bisa dibuat user; kartu menampilkan `PROFILE`
   (`display_name`, `avatar`, `color`) mengikuti konsep Snowflake.
2. **Agent detail / builder** — form + chat preview.

### 7.2 Builder (inspirasi CREATE AGENT Snowflake)

Petakan spec Snowflake → form Nova:

| Snowflake | Nova | Catatan |
|---|---|---|
| `PROFILE.display_name/avatar/color` | nama, ikon, warna agent | metadata UI |
| `instructions.response` | **Response instructions** (textarea) | gaya/persona jawaban |
| `instructions.orchestration` | **Orchestration instructions** (textarea) | aturan pemilihan tool |
| `instructions.sample_questions[]` | **Sample questions** (list) | empty-state chips |
| `models.orchestration` | **Model selector** | reuse `CONFIG_AI_PROVIDERS`/`CONFIG_AI_MODELS` |
| `orchestration.budget {seconds,tokens}` | **Budget** (detik, token) | guardrail biaya |
| `orchestration.capabilities.analytical_search` | toggle semantic search | |
| `orchestration.tool_not_accessible` | `accept`/`reject` | default `accept` |
| `tools[].tool_spec` | **Default tools** (checkbox list) | `semantic_query`, `semantic_search`, `data_to_chart`, `query_execute`, `load_skill` |
| `tool_resources.<tool>.semantic_view` | **Semantic model** picker | file `.ossie.yaml` |
| skills | **Default skills** (multi-select) + **user skills** (upload `.md`) | SKILL.md compatible |

### 7.3 Chat / run surface

- Transcript dengan content blocks: text, `thinking` (plan/act/observe), **tool call card**,
  **table**, **chart**, **citation**, SQL block.
- Composer dengan mention: `@semantic_model`, `@table`, `@stage` (mengikuti CoCo `@`).
- Model selector, Stop (AbortController), "Reset permissions".
- Thread list per agent.

### 7.4 ASCII mockup (daftar + builder + chat)

```
┌ Agents ──────────────────────────────────────────────┬──────────────────────────────┐
│ [+ New agent]                                         │  Revenue Analyst        ⋮    │
│                                                       │  ────────────────────────    │
│ ┌───────────────────────────┐  ┌───────────────────┐  │  You: revenue by region Q3   │
│ │ ● Revenue Analyst         │  │ ● Support Copilot │  │                              │
│ │   sales_analytics         │  │   docs.ossie      │  │  ▸ plan  Understand, query,  │
│ │   3 tools · 5 skills      │  │   2 tools         │  │          answer       [done] │
│ └───────────────────────────┘  └───────────────────┘  │  ▸ skill load: metric-qa     │
│                                                       │  ▸ tool  semantic_query      │
│ ── Builder ─────────────────────────────────────────  │     ┌──────────────────────┐   │
│ Response instructions  [______________________________]│     │ SELECT region, SUM() │   │
│ Orchestration instr.   [______________________________]│     │ confidence 0.92      │   │
│ Model [anthropic/claude ▾]  Budget [30s] [16k tokens] │     └──────────────────────┘   │
│ Default tools:  [x] semantic_query [x] data_to_chart  │  ▸ table 8 rows             │
│                 [ ] query_execute  [x] load_skill     │  ▸ chart ┌──────────────┐   │
│ Semantic model: [sales_analytics.ossie.yaml ▾]        │          │ ▁▃▅▆ bar       │   │
│ Default skills: [metric-qa] [time-series] [+ user…]   │          └──────────────┘   │
│ Sample questions: [+]                                 │  Revenue rose 12% QoQ …      │
│                            [Save agent] [Test ▶]      │  ┌───────────────┐           │
│                                                       │  │ Ask a question│  ↑        │
└───────────────────────────────────────────────────────┴──────────────────────────────┘
```

Design-system Nova wajib dipatuhi: `flex-1 + min-h-0`, `overflow-x-auto`, komponen `components/ui/*`,
tanpa hardcoded height. Gunakan skill **antislop-ui** saat mengerjakan UI.

---

## 8. Penyimpanan state (Single Database)

Mengikuti AGENTS.md §3 dan §7. **Semua** di `NOVA_SYSTEM.CONFIG` sebagai Primary Key tables.

Usulan tabel (nama = usulan, belum ada):

```sql
-- 1 baris per agent
CREATE TABLE NOVA_SYSTEM.CONFIG_AGENTS (
  agent_id        VARCHAR(64) NOT NULL,
  name            VARCHAR(255) NOT NULL,
  description     TEXT,
  -- PROFILE
  display_name    VARCHAR(255),
  avatar          VARCHAR(255),
  color           VARCHAR(32),
  -- spec
  model_provider_id VARCHAR(64),
  model_name      VARCHAR(128),
  instructions_response      TEXT,
  instructions_orchestration TEXT,
  sample_questions JSON,        -- array of strings
  budget_seconds  INT,
  budget_tokens   INT,
  tool_not_accessible VARCHAR(16),   -- accept|reject
  default_tools   JSON,          -- [tool_name,...]
  tool_resources  JSON,          -- {tool_name: {semantic_model_id,...}}
  default_skills  JSON,          -- [skill_name,...]
  semantic_model_id VARCHAR(64),
  visibility      VARCHAR(16),   -- private|shared
  owner_name      VARCHAR(255) NOT NULL,
  created_at, updated_at
) PRIMARY KEY(agent_id) PROPERTIES("enable_persistent_index"="true");

-- 1 baris per semantic model (isi YAML di file/JSON, BUKAN kredensial)
CREATE TABLE NOVA_SYSTEM.CONFIG_SEMANTIC_MODELS (
  semantic_model_id VARCHAR(64) NOT NULL,
  name        VARCHAR(255) NOT NULL,
  database_name, schema_name,
  ossie_version VARCHAR(32),     -- pin versi yang didukung
  definition  JSON,              -- hasil parse/validasi Ossie (bukan raw secret)
  source_file_id VARCHAR(64),    -- referensi file .ossie.yaml di workspace
  owner_name  VARCHAR(255),
  created_at, updated_at
) PRIMARY KEY(semantic_model_id) ...;

-- skill user-defined (selain builtin di package)
CREATE TABLE NOVA_SYSTEM.CONFIG_AGENT_SKILLS (
  skill_id VARCHAR(64) NOT NULL,
  name     VARCHAR(128) NOT NULL,   -- kebab-case, match SKILL.md
  description VARCHAR(1024),
  body     TEXT,                    -- isi SKILL.md (screened credential-shape)
  scope    VARCHAR(16),             -- user|global
  owner_name VARCHAR(255),
  created_at, updated_at
) PRIMARY KEY(skill_id) ...;
```

**Aturan credential (wajib, diuji):** **tidak ada kolom** yang boleh memuat statement, result
rows, password, token, API key, atau nilai credential-shaped. `definition` semantic model hanya
metadata. Skill body di-screening dengan `contains_credential_shape`. Uji dengan scan
credential-shape setelah siklus penuh.

**Thread/run history:** v1 tetap **in-memory** (E5a) seperti NOVA-61; persistensi thread adalah
keputusan lanjutan. **Agent definition** yang baru **dipersist** (karena user membuatnya).

---

## 9. API surface (usulan)

```
# Agent CRUD
GET    /api/v1/agents
POST   /api/v1/agents
GET    /api/v1/agents/{agent_id}
PUT    /api/v1/agents/{agent_id}
DELETE /api/v1/agents/{agent_id}

# Semantic models
GET    /api/v1/agents/semantic-models
POST   /api/v1/agents/semantic-models        # upload/validasi .ossie.yaml
GET    /api/v1/agents/semantic-models/{id}
DELETE /api/v1/agents/semantic-models/{id}

# Skills (user-defined)
GET    /api/v1/agents/skills
POST   /api/v1/agents/skills                 # SKILL.md compatible
DELETE /api/v1/agents/skills/{skill_id}

# Runs (per agent) — reuse pola thread NOVA-61
POST   /api/v1/agents/{agent_id}/threads
POST   /api/v1/agents/{agent_id}/threads/{thread_id}/messages   # SSE
DELETE /api/v1/agents/{agent_id}/threads/{thread_id}
POST   /api/v1/agents/tool-calls/{tool_call_id}/decision        # HITL
```

Event contract (SSE) = NOVA-61 §4 **plus**:
- `content_block` `{ index, type: table|chart|citation|sql, payload }` **atau** event terpisah
  `table` / `chart` (mengikuti Snowflake: `table`, `chart`) — pilih **event terpisah** agar
  parsimoni dan mudah dipetakan.
- `response.chart` Nova → `chart { content_index, tool_use_id, chart_spec }`.

---

## 10. Backlog bertahap (PR-sized)

| Stage | Task | Isi | Depends |
|---|---|---|---|
| **A** | `NA11-A0` spec | Bekukan keputusan AG1-AG7 (lihat §12), event contract, skema tabel | — |
| **B** | `NA11-B1` Agent registry backend | CRUD agent + persist `CONFIG_AGENTS` + injection ke system prompt | A0 |
| **C** | `NA11-C1` Semantic parser | Parse + validasi Ossie YAML (pin versi, fail-closed) | A0 |
| **C** | `NA11-C2` `semantic_query` tool | Grounding prompt + LLM→SQL + eksekusi via `QueryService` + verified queries | C1, existing query svc |
| **C** | `NA11-C3` `semantic_search` tool | **Native StarRocks full-text** (`MATCH_ANY`/`MATCH_ALL` via GIN), tanpa LLM | C1, indexes API |
| **D** | `NA11-D1` `data_to_chart` tool | LLM→Vega-Lite v5 + fallback deterministik | B1 |
| **D** | `NA11-D2` Chart/table renderer FE | vega-embed di sandbox, event `chart`/`table` | D1 |
| **E** | `NA11-E1` Builder UI | Form agent + semantic picker + skill manager | B1 |
| **E** | `NA11-E2` Agents list page | daftar + PROFILE + sample questions | E1 |
| **F** | `NA11-F1` HITL per-agent | `auto_read_only`/`ask_every_tool` + tool call card | B1 |
| **F** | `NA11-F2` Audit & budget | reuse AUDIT_LOG, correlation id, budget per-agent | B1, C2 |
| **G** | `NA11-G1` User skills upload | SKILL.md parse + screening + catalog merge | B1 |
| **G** | `NA11-G2` Verified queries editor | pasangan question→SQL per semantic model | C2 |

**Urutan:** A0 → B1 → (C dan D paralel) → E → F → G. Shared module baru:
`backend/app/modules/agents/` (jangan menaruh registry agent di dalam `assistant/` agar
loop assistant tetap stabil); tool baru masuk ke registry assistant yang sudah ada.

---

## 11. Sumber (provenance)

| Klaim | Sumber | Diakses |
|---|---|---|
| Ossie = OSI, Apache-2.0, masuk incubator 2026-07-10 | `ossie.apache.org`, `github.com/apache/ossie`, `ossie.apache.org/updates/ossie-enters-apache-incubator/` | 2026-09-20 |
| Skema Ossie (datasets/fields/metrics/relationships/ai_context, versi 0.2.0.dev0) | `raw.githubusercontent.com/apache/ossie/main/core-spec/spec.md` | 2026-09-20 |
| CREATE AGENT DDL, spec YAML, tools, budget, tool_not_accessible | `docs.snowflake.com/en/sql-reference/sql/create-agent.md`, `.../cortex-agents-manage.md`, `.../cortex-agents-inaccessible-tool-handling.md` | 2026-09-20 |
| Thread/Run/SSE event list, `response.chart`, timeouts | `docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-run.md`, `.../cortex-agents-rest-api.md` | 2026-09-20 |
| Cortex Analyst semantic view YAML, verified queries, multi-turn limit | `.../views-semantic/semantic-view-yaml-spec.md`, `.../views-semantic/verified-query-repository.md`, `.../cortex-analyst.md`, `.../cortex-analyst/rest-api.md` | 2026-09-20 |
| Cortex Search hybrid retrieval, tool binding | `.../cortex-search/cortex-search-overview.md`, `.../cortex-search/cortex-search-agents.md` | 2026-09-20 |
| Chart = Vega-Lite v5, `chart_spec`, `data_to_chart` | `cortex-agents-run.md`, `cortex-agents-manage.md` | 2026-09-20 |
| Framework versi/lisensi/fitur | PyPI JSON API, GitHub API, docs resmi tiap framework (LangGraph, Pydantic AI, OpenAI Agents SDK, Google ADK, LlamaIndex, CrewAI, MS Agent Framework, Strands, Mastra, Temporal) | 2026-09-20 |
| Agent Skills SKILL.md spec + progressive disclosure | `agentskills.io/specification`, `docs.claude.com/en/docs/agents-and-tools/agent-skills/overview` | 2026-09-20 |
| CoCo consent model (allow once/session/always, read-only auto) | `docs/research/agentic-assistant-snowflake-coco.md` (riset NOVA-61) | 2026-09-18 |
| Kode Nova (loop, events, tools, registry, skills) | `backend/app/modules/assistant/*` @ `main` | 2026-09-20 |

---

## 12. Keputusan yang dibutuhkan dari user (AG1-AG7)

1. **AG1 — Posisi produk:** resmikan **Phase 11 "Agent Platform"** di atas NOVA-61
   (rekomendasi), atau tetap satu assistant tertanam?
2. **AG2 — Semantic standard:** pakai **Apache Ossie** sebagai format kanonik (rekomendasi),
   atau format internal Nova? Jika Ossie, **pin versi 0.1.1** (stabil) atau ikut `0.2.0.dev0`
   (draft, breaking)?
3. **AG3 — Engine agent:** **tetap loop sendiri** (rekomendasi) atau adopsi framework
   (Pydantic AI / LangGraph)?
4. **AG4 — Semantic search:** rilis v1 **native StarRocks full-text** (`MATCH_ANY`/`MATCH_ALL`,
   tanpa LLM, tanpa ranking) — **direvisi & direkomendasikan** setelah verifikasi engine;
   LLM rerank/vector ditunda ke v2.
5. **AG5 — Chart lib:** **vega-embed + Vega-Lite** untuk artifact chat (rekomendasi), atau
   paksa ke recharts yang sudah ada?
6. **AG6 — HITL:** default agent `auto_read_only` + opsi `ask_every_tool`, **tanpa** bypass
   (rekomendasi), atau sediakan bypass?
7. **AG7 — Persistensi:** agent definition **dipersist** di `NOVA_SYSTEM` (rekomendasi), thread
   tetap in-memory (E5a NOVA-61) sampai ada keputusan retention?

---

## 13. Yang belum terverifikasi / jangan dikarang

- **Kemampuan vector/full-text index StarRocks 4.1.x** untuk `semantic_search` — **SUDAH
  DICEK** (2026-09-20): full-text inverted index native **ada** (GIN, built-in sejak 4.1);
  **relevance scoring (`ORDER BY score`) TIDAK ada** di jalur predicate 4.1.4 (terverifikasi di
  `docs/24-advanced-indexes.md` + `docs/gap-analysis.md:210`). Vector index ada di StarRocks
  tetapi **belum** diverifikasi untuk Nova — di luar cakupan v1.
- **Skema `ToolUsePermission`/`ToolChoice` Cortex Agents lengkap** — doc terpotong; hanya contoh
  `Allow Once`/`Deny` yang terverifikasi.
- **Apakah `data_to_chart` butuh approval** di Snowflake — tidak terdokumentasi.
- **`cortex-analyst/semantic-model-spec`** (URL) — 404; spec yang terverifikasi adalah
  `views-semantic/semantic-view-yaml-spec.md`.
- **Panel/section persistence Snowsight** dan lokasi render result set CoCo — tidak terdokumentasi.
- **Tanggal GA CoCo** — tidak ada.
- **AutoGen** konflik lisensi (repo CC-BY-4.0 vs package MIT) — jangan adopsi tanpa review legal.
- **Ossie 0.2.0.dev0** masih **DRAFT**; skema bisa berubah — alasan mem-pin versi.
- **Beban nyata loop multi-agent** pada pool FastAPI — belum diukur (revisit trigger sidecar,
  sama seperti NOVA-61).
