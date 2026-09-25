# Spec Implementasi — Nova Agent Studio (Phase 12)

> **Status:** implemented. Core Agent Studio, semantic tools, Nova Studio streaming,
> ordered artifacts, consent, observability, and eval gates are landed.
> **Engine pin:** StarRocks 4.1.x. **Backend:** FastAPI + Python 3.11. **Frontend:** React 19 + Vite + TanStack + shadcn/ui.
> **Basis riset:** `docs/research/nova-agent-page-deep-research.md` (semantic/Ossie, engine agentic,
> Cortex Agents, HITL, chart).
> **Basis kode:** NOVA-61 yang sudah landing di `main` (`backend/app/modules/assistant/`,
> `frontend/src/features/assistant/`).
> **Aturan:** tidak ada credential di UI/state/log. Semua invariant AGENTS.md berlaku.

> **Catatan evolusi (Semantic Views 2.0):** bagian yang menyebut menu
> `/agents/semantic`, tabel semantic model, dan `semantic_model_ids` merekam
> rancangan Phase 12. Kontrak produk saat ini ada di
> [Module 28: Intelligence Foundation](../28-intelligence-foundation.md):
> **Semantic Views** adalah satu-satunya objek semantik yang dibuat, diaktifkan,
> dan dipilih oleh Agent Studio. Route lama hanya mengarahkan pengguna ke
> halaman Semantic Views; data lama dimigrasikan tanpa mengubah ID.

---

## 0. Ringkasan keputusan

| # | Keputusan | Nilai |
|---|---|---|
| AG-N1 | Nama fitur chat full-page (padanan CoWork) | **Nova Studio** |
| AG-N2 | Struktur sidebar | **AI & ML → Agent** (list+builder) dan **AI & ML → Semantic** (model semantik) |
| AG-N3 | Permukaan Nova Studio | **Tab baru** (full-page chat), bukan panel dock |
| AG-N4 | Nama objek agent | `agent_id` (stabil, immutable) + `name` (tampilan) di `NOVA_SYSTEM.CONFIG_AGENTS` |
| AG-N5 | Semantic standard | **Apache Ossie** (pin `0.1.1`, stabil; `0.2.0.dev0` draft ditolak fail-closed) |
| AG-N6 | Engine agent | **Loop sendiri** (perluas loop NOVA-61), tanpa framework |
| AG-N7 | Chart | **Vega-Lite v5** via `vega-embed`, event `chart` mengikuti Cortex |
| AG-N8 | HITL default | `auto_read_only`; opsi `ask_every_tool`; **tanpa** bypass di v1 |
| AG-N9 | Isolasi modul | Agent/semantic di modul baru `backend/app/modules/agents/`; loop assistant tidak diubah strukturnya |
| AG-N10 | Fase | **Phase 12** (Phase 11 sudah dipakai Migration Connector) |

**Perubahan penting vs riset awal:** NOVA-61 ternyata sudah **mempersist** thread/message ke
`NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS/_MESSAGES` (`repository.py:27,44`) — jadi E5b, bukan E5a.
Rencana ini **reuse** repository itu untuk Nova Studio, ditambah kolom `agent_id`.

---

## 1. Peta produk (apa yang dibangun)

```
Sidebar
└── AI & ML  (Brain)
    ├── ML Models          (sudah ada)  /ml-models
    ├── Agent              (BARU)       /agents          → Agent Studio: list + builder
    └── Semantic           (BARU)       /agents/semantic → Semantic model builder (Ossie)
                                        /agents/studio   → Nova Studio (tab baru, full-page chat)
```

Tiga permukaan (mengikuti pola Snowflake: Agent Studio + Semantic + CoWork):

1. **Agent Studio** (`/agents`) — daftar agent, mirip "All agents" pada screenshot. Kolom:
   Agent, Database, # Requests, Updated, Created By. Filter: Database, search, created-by.
   Tombol **Create agent** membuka modal (Database & schema, object name, display name).
2. **Agent Builder** (`/agents/$agentId`) — editor: Instructions (response + orchestration),
   Response output (format/style), Model, Budget, Default tools, Default skills, Semantic model,
   Sample questions. Tab "Test" menyematkan preview chat.
3. **Semantic** (`/agents/semantic`) — list semantic model + builder Ossie (datasets, fields,
   metrics, relationships, ai_context, verified queries) dengan validasi + import `.ossie.yaml`.
4. **Nova Studio** (`/agents/studio`) — tab baru full-page: sidebar kiri (New chat, Automations,
   Artifacts, Capabilities, Search), composer tengah, greeting + sample questions, pemilih agent.

---

## 2. Arsitektur backend

### 2.1 Modul baru

```
backend/app/modules/agents/
├── __init__.py
├── router.py            # /api/v1/agents/* (+ semantic, skills, studio runs)
├── schemas.py           # Pydantic: AgentView, AgentCreateRequest, ...
├── repository.py        # NOVA_SYSTEM.CONFIG_AGENTS / CONFIG_SEMANTIC_MODELS / CONFIG_AGENT_SKILLS
├── service.py           # AgentService: CRUD + resolusi agent → prompt + tool registry
├── prompt.py            # Rakit system prompt dari agent config (instructions/response/tools/skills)
├── semantic/
│   ├── __init__.py
│   ├── ossie.py         # Parser + validator Ossie (pin versi, fail-closed)
│   ├── grounding.py     # Semantic model → prompt grounding (Cortex Analyst style)
│   └── verified.py      # Verified queries (question→SQL) per semantic model
├── tools/
│   ├── semantic_query.py    # text-to-SQL via semantic model → QueryService
│   ├── semantic_search.py   # full-text native StarRocks (MATCH_ANY/MATCH_ALL), tanpa LLM
│   └── data_to_chart.py     # rows/columns + intent → Vega-Lite v5 spec + fallback
└── studio.py            # Orkestrasi "run" Nova Studio per agent
```

**Kenapa modul terpisah, bukan menambah di `assistant/`:** loop `assistant/` sudah stabil dan
lulus spec NOVA-61; menaruh registry agent di dalamnya mengubah kontrak internal yang dipakai
test. Modul `agents/` **memanggil** `AssistantLoop` (komposisi), bukan memodifikasinya.

### 2.2 Integrasi dengan loop yang ada (mekanisme konkret)

`AssistantLoop` (`assistant/service.py:85`) sudah menerima:
- `provider: AssistantProviderClient` — reuse apa adanya.
- `registry: ToolRegistry` — **di-inject per agent**, berisi default tools agent + `load_skill`.
- `system_prompt: str` — **di-rakit per agent** dari `prompt.py` (menggantikan persona global "Nove").
- `max_iterations`, `time_budget_seconds` — diambil dari `budget_seconds` agent.

Alur satu turn Nova Studio:

```
POST /api/v1/agents/{agent_id}/threads/{thread_id}/messages   (SSE)
  → AgentService.load(agent_id)                      # config agent
  → build_registry(agent.default_tools)              # ToolRegistry per agent
  → build_system_prompt(agent, skills_catalog)       # prompt.py
  → AssistantLoop(provider, registry, system_prompt, budget).run(...)
      → plan → load_skill → semantic_query/query_execute/data_to_chart → answer
  → SSE frames (existing) + `table` + `chart` (baru)
  → persist ke CONFIG_ASSISTANT_MESSAGES (existing repo, + agent_id)
```

**Tidak ada perubahan pada `LoopContext`, consent, atau event existing** — hanya penambahan
dua event baru (`table`, `chart`) di `assistant/events.py` dan penanganan content blocks.

### 2.3 Registri tool (per agent)

| Tool | Klasifikasi | Butuh consent | Sumber |
|---|---|---|---|
| `load_skill` | `read_only` | tidak | existing `tools/load_skill.py` |
| `query_execute` | `read_only` (allowlist) | ya bila belum grant | existing `tools/query_execute.py` |
| `semantic_query` | `read_only` | mengikuti policy agent | baru |
| `semantic_search` | `read_only` | mengikuti policy agent | baru (native FTS) |
| `data_to_chart` | `read_only` | tidak (pure transform) | baru |

`build_registry(agent)` mengembalikan `ToolRegistry` berisi subset tools yang dipilih user +
`load_skill` **selalu**. Tool yang tidak dipilih **tidak terdaftar**, jadi model tidak bisa
memanggilnya (bukan sekadar prompt).

### 2.4 Semantic layer

**Parser/validator (`semantic/ossie.py`):**
- Terima YAML/JSON; deteksi `version`.
- **Terima hanya `0.1.1`** di v1 (spec stabil). `0.2.0.dev0` (draft, breaking, model di root
  tanpa array) → ditolak dengan pesan migrasi yang jelas. Fail-closed.
- Validasi: `datasets` non-empty; setiap dataset punya `source`; expression punya dialect
  `ANSI_SQL`; relationship `from_columns`/`to_columns` sama panjang (aturan spec).
- Normalisasi ke dataclass internal (`SemanticModel`, `Dataset`, `Field`, `Metric`,
  `Relationship`) — tidak menyimpan YAML mentah di state runtime.
- **Tidak ada kredensial** yang boleh ada di YAML; jalankan `contains_credential_shape`.

**Grounding (`semantic/grounding.py`):** rakit blok prompt dari model: dataset, field
(name, description, synonyms, examples), metric, relationship. **Hanya metadata**, tanpa baris
data. Ini yang membuat `semantic_query` akurat.

**Verified queries (`semantic/verified.py`):** pasangan `question → sql` yang di-verifikasi user.
Disuntik sebagai few-shot saat pertanyaan mirip (pola Cortex VQR). Disimpan di
`CONFIG_SEMANTIC_MODELS.definition` (JSON) atau tabel terpisah bila tumbuh.

### 2.4b `semantic_search` — NATIVE StarRocks full-text (revisi 2026-09-20)

**Keputusan:** `semantic_search` v1 memakai **full-text inverted index native StarRocks**, bukan
LLM rerank dan bukan vector store. LLM rerank **dibatalkan dari v1**.

**Dasar (terverifikasi pada engine 4.1.4 di repo ini):** `docs/24-advanced-indexes.md` +
`backend/tests/integration/test_inverted_index_l3.py`:

- Inverted index GIN native ada; **built-in implementation sejak 4.1** (bitmap-based, dukung
  shared-data; CLucene tidak).
- Predicate: `<col> MATCH_ANY 'kata1 kata2'`, `<col> MATCH_ALL '...'`, `<col> MATCH 'kata'` /
  `MATCH 'data%'`. **Predicate, bukan function**; keyword **spasi**, bukan koma.
- Pushdown-only (harus di `WHERE`), kolom wajib `STRING NOT NULL`.
- Preconditions: `enable_experimental_gin=true`; `replicated_storage=false`; schema change
  asynchronous. Cek via `GET /api/v1/indexes/capabilities`.
- **`ORDER BY score` (relevance/BM25) tidak tersedia** di jalur predicate 4.1.4 → hasil
  **filtered, not ranked** (`docs/gap-analysis.md:210,216`).
- Parser valid: `none|english|chinese|standard|unicode` (`ngram` **invalid**).

**Bentuk tool:**

```python
# backend/app/modules/agents/tools/semantic_search.py  (ilustratif)
# 1. Ambil kolom teks searchable dari semantic model (Ossie field/ai_context).
# 2. Bangun predicate MATCH_ANY (mode "any") atau MATCH_ALL (mode "all").
#    Term di-lowercase; keyword = string literal (bukan expression).
# 3. Jalankan lewat QueryService.execute_statements pada koneksi user.
# 4. Kembalikan baris tanpa peringkat + catatan "unranked".
```

**Aturan:**
- **Tanpa LLM rerank di v1.** Karena tidak ada skor, LLM rerank tidak punya input skor; menambah
  rerank LLM menambah biaya tanpa dasar.
- **Tanpa klaim paritas Cortex Search.** Nyatakan di UI/tool description: "keyword full-text,
  hasil tidak diperingkat".
- Bila kolom belum GIN-indexed, `semantic_search` **gagal jelas** dan menyarankan membuat index
  lewat `/api/v1/indexes/create` (atau menawarkan pembuatan bila caller punya hak).
- **Reuse** modul `indexes/` yang ada: `validate_fulltext_predicate`
  (`backend/app/modules/indexes/router.py:214`).

**v2 (revisit-trigger):** LLM rerank atas kandidat terbatas, atau vector search, hanya bila
korpus nyata membuktikan hasil tak-berperingkat kurang.

### 2.5 Chart

`data_to_chart(rows, columns, intent)`:
1. Model memilih mark + encoding → Vega-Lite v5 JSON.
2. **Fallback deterministik** bila model gagal: dimensi waktu → `line`, kategori+nilai → `bar`,
   satu nilai → `text`. Jangan pernah mengirim spec rusak.
3. **Sanitasi spec:** whitelist `mark` (`bar/line/point/area/arc`), tolak `expr`/`signal`
   arbitrary, batasi ukuran. Ini titik XSS — harus diuji.
4. Kembalikan `{ chart_spec: <json string> }` → event `chart`.

---

## 3. Skema database (`NOVA_SYSTEM.CONFIG_*`)

Semua Primary Key tables, pola sama dengan `CONFIG_ASSISTANT_THREADS`. **Tidak ada kolom
statement/result/credential.**

```sql
-- Agent
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENTS (
    agent_id                  VARCHAR(64) NOT NULL,
    owner_name                VARCHAR(128) NOT NULL,
    database_name             VARCHAR(128),
    schema_name               VARCHAR(128),
    name                      VARCHAR(128) NOT NULL,   -- display name
    description               TEXT,
    avatar                    VARCHAR(256),
    color                     VARCHAR(32),
    model_provider_id         VARCHAR(64),
    model_name                VARCHAR(128),
    instructions_response     TEXT,
    instructions_orchestration TEXT,
    response_style            VARCHAR(64),             -- ringkas|detail|tabel
    sample_questions          JSON,
    budget_seconds            INT,
    budget_tokens             INT,
    tool_not_accessible       VARCHAR(16),             -- accept|reject
    default_tools             JSON,
    default_skills            JSON,
    policy                    VARCHAR(32),             -- auto_read_only|ask_every_tool
    semantic_model_id         VARCHAR(64),
    visibility                VARCHAR(16),             -- private|shared
    created_at                DATETIME NOT NULL,
    updated_at                DATETIME NOT NULL
) PRIMARY KEY(agent_id)
DISTRIBUTED BY HASH(agent_id) BUCKETS 1
PROPERTIES("replication_num"="1","enable_persistent_index"="true");

-- Semantic model
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_SEMANTIC_MODELS (
    semantic_model_id VARCHAR(64) NOT NULL,
    owner_name        VARCHAR(128) NOT NULL,
    name              VARCHAR(128) NOT NULL,
    description       TEXT,
    database_name     VARCHAR(128),
    schema_name       VARCHAR(128),
    ossie_version     VARCHAR(32) NOT NULL,   -- pin, mis. 0.1.1
    definition        JSON,                   -- hasil parse (metadata saja)
    source_file_id    VARCHAR(64),            -- referensi file .ossie.yaml
    created_at        DATETIME NOT NULL,
    updated_at        DATETIME NOT NULL
) PRIMARY KEY(semantic_model_id) ...;

-- Skill user-defined (SKILL.md)
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_SKILLS (
    skill_id    VARCHAR(64) NOT NULL,
    owner_name  VARCHAR(128) NOT NULL,
    name        VARCHAR(128) NOT NULL,        -- kebab-case
    description VARCHAR(1024),
    body        TEXT,                          -- di-screening credential-shape
    scope       VARCHAR(16),                   -- user|global
    created_at  DATETIME NOT NULL,
    updated_at  DATETIME NOT NULL
) PRIMARY KEY(skill_id) ...;
```

**Perubahan kecil pada tabel assistant yang ada:** tambah kolom
`agent_id VARCHAR(64)` di `CONFIG_ASSISTANT_THREADS` (nullable; `NULL` = assistant lama).
Migrasi aditif, `ALTER TABLE ... ADD COLUMN`, tidak breaking.

**Repository:** `agents/repository.py` menambahkan `ensure_schema()` untuk tiga DDL di atas;
dipanggil di `main.py` lifespan (pola sama dengan `assistant_repository.ensure_schema()` di
`main.py:~112`).

---

## 4. API surface

```
# Agent CRUD
GET    /api/v1/agents                         list (filter: database, search, created_by)
POST   /api/v1/agents                         create
GET    /api/v1/agents/{agent_id}              detail
PUT    /api/v1/agents/{agent_id}              update
DELETE /api/v1/agents/{agent_id}              delete

# Semantic models
GET    /api/v1/agents/semantic-models
POST   /api/v1/agents/semantic-models         create (body: ossie yaml/json)
GET    /api/v1/agents/semantic-models/{id}
PUT    /api/v1/agents/semantic-models/{id}
DELETE /api/v1/agents/semantic-models/{id}
POST   /api/v1/agents/semantic-models/validate  validasi tanpa simpan (dipakai editor)

# Skills (user-defined, SKILL.md compatible)
GET    /api/v1/agents/skills
POST   /api/v1/agents/skills
DELETE /api/v1/agents/skills/{skill_id}

# Nova Studio runs (reuse pola thread assistant)
GET    /api/v1/agents/{agent_id}/threads
POST   /api/v1/agents/{agent_id}/threads
GET    /api/v1/agents/{agent_id}/threads/{thread_id}
DELETE /api/v1/agents/{agent_id}/threads/{thread_id}
POST   /api/v1/agents/{agent_id}/threads/{thread_id}/messages   # SSE
POST   /api/v1/agents/tool-calls/{tool_call_id}/decision        # HITL
PUT    /api/v1/agents/{agent_id}/threads/{thread_id}/grant
```

Semua route `Depends(get_current_user)`. Unknown/foreign id → **404** (jangan 403).

### 4.1 Event contract (SSE) — tambahan atas NOVA-61

Existing: `text_delta`, `thinking`, `plan`, `tool_call`, `tool_status`, `done`, `error`, `ping`.

Setiap frame dalam satu turn membawa `run_id` dan `sequence` monotonik. Setiap
blok jawaban membawa `content_index` dan `content_id`; arrival time tidak pernah
menentukan urutan tampilan.

**Baru:**

| `event:` | Payload | Kapan |
|---|---|---|
| `table` | `{ content_index, title, columns, rows }` | tool mengembalikan result set |
| `chart` | `{ content_index, tool_use_id, chart_spec }` | `data_to_chart` selesai (`chart_spec` = Vega-Lite v5 JSON string) |
| `citation` | `{ content_index, title, source, snippet }` | `semantic_search` mengembalikan sumber |
| `tool_progress` | `{ tool_call_id, stage, text, sql_preview? }` | lifecycle faktual tool, termasuk generated/validated/executing SQL |
| `content_block_done` | `{ content_index, content_id }` | blok ditutup; blok dengan index lebih tinggi boleh ditampilkan |

Frontend `events.ts::parseAssistantEvent` diperluas dengan case ini; frame tak dikenal tetap
di-ignore (kontrak lama dipertahankan).

Artifact hasil tool masuk response compositor lebih dahulu, bukan langsung ke
DOM. Default-nya prose diikuti artifact. Marker internal `[[NOVA_ARTIFACT]]`
memungkinkan agent menaruh artifact sebelum atau di antara paragraf. Marker
tidak pernah dikirim ke client. Frontend hanya membuka prefix `content_index`
yang kontigu dan menahan index berikutnya sampai blok sebelumnya ditutup.

---

## 5. Frontend

### 5.1 Struktur file

```
frontend/src/
├── routes/_authenticated/
│   ├── agents/index.tsx                 → Agent Studio list
│   ├── agents/$agentId.tsx              → Agent Builder (tab: Configure | Test)
│   ├── agents/semantic/index.tsx        → Semantic list
│   ├── agents/semantic/$modelId.tsx     → Semantic builder
│   └── agents/studio.tsx                → Nova Studio (full-page chat)
├── features/agents/
│   ├── index.tsx                        # AgentStudio (list + create modal)
│   ├── agent-builder/                   # instructions, tools, skills, semantic picker
│   ├── semantic/                        # ossie editor (datasets/fields/metrics/relations)
│   ├── studio/                          # Nova Studio shell
│   │   ├── studio-shell.tsx             # sidebar + main
│   │   ├── studio-sidebar.tsx           # New chat, Automations, Artifacts, Capabilities, Search
│   │   ├── studio-composer.tsx
│   │   ├── studio-greeting.tsx
│   │   └── blocks/                      # TextBlock, TableBlock, ChartBlock, CitationBlock
│   └── api.ts                           # typed API client khusus agents
└── components/layout/data/sidebar-data.ts   # tambah 2 item di grup AI & ML
```

### 5.2 Sidebar (perubahan kecil, presisi)

Di `sidebar-data.ts`, grup **AI & ML** (baris 54-63) menjadi:

```ts
{
  title: 'AI & ML',
  icon: Brain,
  items: [
    { title: 'ML Models', url: '/ml-models', icon: Brain },
    { title: 'Agent',    url: '/agents',    icon: Bot },     // BARU
    { title: 'Semantic', url: '/agents/semantic', icon: Blocks }, // BARU
  ],
},
```

Ikon mengikuti aturan DESIGN.md: `Bot` = LLM/agent. Untuk Semantic gunakan ikon non-konflik
(mis. `Blocks`/`Network`). Nova Studio **tidak** masuk sidebar (dibuka sebagai tab).

### 5.3 Agent Studio list (`/agents`)

Mengikuti screenshot: header "Agent Studio" + `Documentation` / `Settings` / `Create agent`;
tab `All agents` | `Nova Studio agents`; toolbar `Database ▾`, `Search agent`, `Created by ▾`,
refresh; tabel kolom `AGENT`, `# OF REQUESTS`, `UPDATED`, `ADDED TO STUDIO`. Skeleton loading
(shimmer) seperti screenshot. "Create agent" modal: Database & schema, object name, display name.

### 5.4 Nova Studio (tab baru, full-page)

Mekanisme tab baru: tombol "Add to Studio"/"Open in Studio" dari list/builder memanggil
`window.open('/agents/studio?agent=<id>', '_blank', 'noopener')` — benar-benar **tab browser
baru** (permintaan user), bukan tab internal. Route `/agents/studio` membaca `?agent=` dan
`?thread=`.

Layout:
- Sidebar kiri: `New chat`, `Automations`, `Artifacts`, `Capabilities`, `Search`, footer user.
- Tengah: greeting ("Good evening, <name> — What insights can I help with?"), composer dengan
  attachment `+`, `Extended thinking` toggle, mic (opsional), dan banner "No agents available"
  bila user belum punya akses agent.
- Pemilih agent di composer (dropdown) mengikuti agent yang boleh dipakai user.

### 5.5 Render blok di chat

- Text: `react-markdown` (sudah ada) + komponen `markdown.tsx` existing.
- Thinking: komponen plan/step existing.
- Tool call card: existing (status, SQL preview, allow/deny, always-allow).
- **Table**: tanstack-table (sudah ada).
- **Chart**: `vega-embed` + `vega-lite` — **dependency baru** (belum ada di package.json).
  Render di container dengan `actions:false`, spec disanitasi backend.
- **Citation**: kartu kecil + tautan sumber.

---

## 6. Mekanisme integrasi (ringkas alur)

### 6.1 Membuat agent → dipakai di Studio

```
Builder (FE) → POST /api/v1/agents
  → AgentService.create() → validate (tools/skills/semantic ids ada) → CONFIG_AGENTS
Studio (FE) → GET /api/v1/agents?available=1 → pilih agent
  → POST /agents/{id}/threads → POST /agents/{id}/threads/{tid}/messages (SSE)
```

### 6.2 Semantic query end-to-end

```
Model panggil semantic_query(question, semantic_model_id)
  → grounding.py rakit prompt dari Ossie metadata (+ verified queries few-shot)
  → LLM → SQL (+ explanation, confidence)
  → QueryService.execute_statements(sql, user conn)   # guard+audit+redaksi otomatis
  → ToolOutcome + events.table (+ chart bila diminta)
```

### 6.3 HITL

Sama seperti NOVA-61: tool `read_only` auto-run bila `policy=auto_read_only` atau grant aktif;
`destructive` selalu prompt; approval lewat `POST /agents/tool-calls/{id}/decision`. Tidak ada
bypass.

### 6.4 Audit & budget

Semua eksekusi `semantic_query`/`query_execute` tetap lewat `QueryService` → baris
`NOVA_SYSTEM.AUDIT_LOG` dengan `session_id` = `thread_id` (korelasi). Budget per turn dari
agent (`budget_seconds`/`budget_tokens`) menggantikan default hardcoded loop.

---

## 7. Backlog bertahap (PR-sized)

Status: ✅ landed · 🟡 sebagian · ⬜ belum.

| Stage | ID | Task | Isi | Depends | Ukuran | Status |
|---|---|---|---|---|---|---|
| **A** | `N12-A0` | Spec freeze | Dokumen ini + keputusan AG-N1..N10 | — | S | ✅ |
| **B** | `N12-B1` | Modul + skema | `modules/agents/` skeleton, 3 DDL, `ensure_schema` di lifespan, kolom `agent_id` di threads | A0 | M | ✅ |
| **B** | `N12-B2` | Agent CRUD + prompt | `service.py`, `prompt.py`, `router.py` CRUD, `build_registry(agent)` | B1 | M | ✅ |
| **C** | `N12-C1` | Ossie parser | `semantic/ossie.py` + validate endpoint + test fail-closed | A0 | M | ✅ |
| **C** | `N12-C2` | Semantic CRUD + grounding | repo, router, `grounding.py` | C1 | M | ✅ |
| **C** | `N12-C3` | `semantic_query` tool | LLM→SQL + `QueryService` + verified queries | C2 | M | ✅ |
| **C** | `N12-C4` | `semantic_search` tool | **Native full-text `MATCH_ANY`/`MATCH_ALL`** via GIN; tanpa LLM/vektor; reuse `/api/v1/indexes` | C2 | M | ✅ |
| **D** | `N12-D1` | `data_to_chart` + events | Vega-Lite + fallback + sanitasi; event `table`/`chart`/`citation` | B2 | M | ✅ |
| **D** | `N12-D2` | FE chart/table block | `vega-embed` + sanitasi + blok tabel/citation | D1 | M | ✅ |
| **E** | `N12-E1` | Sidebar + routes | 2 item AI&ML, 5 route file | B2 | S | ✅ |
| **E** | `N12-E2` | Agent Studio list | list + filter + create modal + skeleton | E1 | M | ✅ |
| **E** | `N12-E3` | Agent Builder | instructions/response/tools/skills/semantic + tab Test | E2 | L | ✅ |
| **E** | `N12-E4` | Semantic builder UI | editor Ossie + validasi inline + import file | E1 | L | ✅ |
| **F** | `N12-F1` | Nova Studio shell | tab baru, sidebar, greeting, composer, agent picker | E1 | L | ✅ |
| **F** | `N12-F2` | Studio chat wiring | SSE per agent, transcript, stop, blocks | F1,D2 | M | ✅ |
| **G** | `N12-G1` | User skills upload | SKILL.md parse + screening + merge ke catalog | B2 | M | ✅ |
| **G** | `N12-G2` | HITL policy + audit | `auto_read_only`/`ask_every_tool`, korelasi audit, budget | F2 | M | ✅ |

**Urutan:** A0 → B1 → B2 → (C1→C2→C3/C4) ∥ (D1→D2) → E1→(E2,E3,E4) → F1→F2 → G.
Backend (B/C/D) dan frontend (E/F) bisa jalan paralel setelah event contract dibekukan di A0.

### 7.1 Yang sudah dibangun (N12-A0, N12-B1, N12-C1)

```
backend/app/modules/agents/
├── __init__.py              # aturan desain modul
├── repository.py            # 3 DDL + CRUD owner-scoped
├── schemas.py               # AgentView/Create/Update, Semantic*, Skill*
├── router.py                # REST /api/v1/agents/*
└── semantic/
    ├── __init__.py
    └── ossie.py             # parser + validator Ossie (pin 0.1.1, fail-closed)
```

Terdaftar di `backend/app/main.py` (import + `include_router` + `ensure_schema`).
Migrasi aditif: `agent_id` di `CONFIG_ASSISTANT_THREADS` (`THREADS_AGENT_ID_DDL`).

Tes: `tests/unit/test_agents_ossie_parser.py` (14) + `tests/unit/test_agents_repository.py` (5).
Suite unit penuh hijau (3020 passed).

**Pitfall yang ditemukan & diperbaiki:** route dengan segmen literal (`/semantic-models`,
`/skills`) **wajib** dideklarasikan **sebelum** route dinamis `/{agent_id}`. FastAPI mencocokkan
sesuai urutan deklarasi; kalau tidak, `/agents/semantic-models` akan ditangkap sebagai
`agent_id="semantic-models"`. Diimplementasikan dengan blok komentar eksplisit di `router.py`.

### 7.2 Status akhir implementasi (backend + agent E2E)

Sudah dibangun dan **terverifikasi terhadap engine 4.1.x live + provider LLM live (Kenari /
deepseek-v4-1-flash)**:

```
backend/app/modules/agents/
├── __init__.py              # aturan desain modul
├── repository.py            # 3 DDL + CRUD owner-scoped
├── schemas.py               # AgentView/Create/Update, Semantic*, Skill*
├── router.py                # CRUD + thread + SSE run + HITL decision
├── service.py               # compose agent → registry + prompt + budget
├── prompt.py                # system prompt per agent (core contract tetap)
├── registry.py              # tool registry per agent (seleksi struktural)
├── semantic/
│   ├── ossie.py             # parser + validator (pin 0.1.1, fail-closed)
│   └── grounding.py         # Ossie metadata → prompt text-to-SQL
├── tools/
│   ├── semantic_query.py    # LLM→SQL → QueryService (guard+audit+redaksi)
│   ├── semantic_search.py   # native StarRocks full-text (MATCH_ANY/ALL)
│   └── data_to_chart.py     # Vega-Lite v5 + fallback + sanitasi
└── examples/
    ├── nova_sales.ossie.yaml # semantic model siap pakai (NOVA_DEMO/NOVA_CATALOG)
    └── seed_demo.py          # seed model + agent demo
```

Frontend: `frontend/src/features/agents/` (list, builder, semantic, Nova Studio chat,
chart-block) + route `/agents`, `/agents/$agentId`, `/agents/semantic`, `/agents/studio`
+ entri sidebar **AI & ML → Agent / Semantic**. `vega-embed` 7.2.0 + `vega-lite` 6.4.3
(BSD-3-Clause), di-load **lazy** hanya saat ada chart.

**Verifikasi E2E (live):**
- Sample: `NOVA_DEMO`/`NOVA_CATALOG` dimuat ulang dari `docker/init-nova.sql`.
- Semantic model `nova_sales` (4 datasets, 4 metrics, 3 relationships) → valid 0.1.1.
- Agent **Revenue Analyst** (semantic_query + data_to_chart + load_skill).
- 7 pertanyaan bisnis → **7/7 benar**, angka cocok dengan engine (total IDR 175,184,000).
- Loop penuh mengeluarkan frame: `plan → thinking → tool_call → table → chart → done`.
- Pertanyaan di luar model → **ditolak dengan jujur** (bukan halusinasi).

**Bug nyata yang ditemukan lewat E2E dan diperbaiki:**

| Bug | Gejala | Perbaikan |
|---|---|---|
| `Decimal`/`date` tak serializable | `table`/`chart` frame crash (`TypeError`) | `events._json_default` meng-coerce Decimal→str, date→ISO |
| Pertanyaan tak terjawab = tool gagal | turn berhenti dengan error, padahal model benar menolak | `semantic_query` kembalikan `ok=True` + penjelasan |
| Computed field dipakai sebagai kolom | `c.full_name` → error engine | grounding tandai `COMPUTED` + inline ekspresi |
| Duplikasi teks jawaban | narasi pra-tool ikut ter-stream, teks berulang | buffer teks per iterasi; hanya rilis saat tanpa tool call |
| Tool call duplikat | model memanggil tool yang sama 2× | `_call_fingerprint` cache per turn |

Tes: `tests/unit/test_agents_ossie_parser.py` (14) + `test_agents_repository.py` (5) +
`test_agents_tools.py` (12). Frontend: 588 tes hijau, build produksi hijau.


---

## 8. Guardrails & acceptance

| Area | Aturan | Uji |
|---|---|---|
| Credential | Tidak ada statement/result/password di tabel, event, prompt, log | scan credential-shape setelah siklus penuh |
| Semantic YAML | Tidak boleh memuat credential | `contains_credential_shape` saat validasi |
| Eksekusi | `semantic_query`/`query_execute` **wajib** lewat `QueryService` | tool tidak import asyncmy/socket; test |
| Ossie versi | Hanya `0.1.1`; versi lain ditolak | test parser |
| Chart XSS | Spec disanitasi; `actions:false`; sandbox | test payload jahat (expr/signal) |
| FTS | `semantic_search` hanya `MATCH*` pushdown pada kolom GIN `STRING NOT NULL`; lowercase; predicate bukan function | test bentuk predicate + precondition |
| HITL | Destructive tidak pernah auto; tanpa bypass | test policy |
| ACCOUNTADMIN | Guard existing tidak berubah | test guard |
| Audit | Setiap eksekusi punya baris AUDIT_LOG | test korelasi thread_id |

---

## 9. Yang tidak ada di v1 (revisit-trigger)

- Framework agentic (LangGraph/Pydantic AI) — loop sendiri cukup.
- **LLM rerank untuk `semantic_search`** — dibatalkan dari v1: engine 4.1.4 **tidak punya
  relevance scoring**, jadi tidak ada skor untuk di-rerank. Revisit bila korpus nyata menuntut.
- Vector store / hybrid search penuh — v1 **native full-text** (`MATCH_ANY`/`MATCH_ALL`).
  Vector index StarRocks ada tetapi **belum diverifikasi untuk Nova**; jangan dijanjikan.
- `Bypass approvals`.
- Multi-agent hand-off / durable graph.
- Sidecar `nova-agent`.
- Automations & Artifacts penuh di Nova Studio (v1: item sidebar ada, fungsional minimal).

---

## 10. Status verifikasi

| Klaim | Status |
|---|---|
| Squad pola repo/DDL/router Nova | terverifikasi dari kode `main` |
| Thread persistence sudah ada (E5b) | terverifikasi (`repository.py:27,44`) |
| Phase 11 terpakai Migration Connector | terverifikasi (`main.py`) |
| Ossie 0.1.1 stabil / 0.2.0.dev0 draft breaking | terverifikasi (spec Ossie) |
| Vega-Lite + `response.chart` di Cortex | terverifikasi (docs Cortex Agents) |
| StarRocks full-text inverted index native | **terverifikasi** via `docs/24-advanced-indexes.md` + test L3 engine 4.1.4 |
| Relevance scoring/BM25 di 4.1.4 | **terverifikasi TIDAK ada** (`docs/gap-analysis.md:210,216`) → LLM rerank dibatalkan |
| StarRocks vector index untuk Nova | **belum diverifikasi** — di luar cakupan v1 |
| `vega-embed` version/license | **belum diverifikasi** — cek PyPI/npm saat N12-D2 |
