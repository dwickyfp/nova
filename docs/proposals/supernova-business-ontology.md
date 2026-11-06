# Supernova Business Ontology — analisis dan rencana implementasi

**Status:** Fase 1 (ontology core backend), Fase 2 (Studio ontology-aware), Fase 3 (halaman Business Ontology di Console), Fase 4 (Signal dan Incident), Fase 5 (jembatan News ↔ Studio) dan Fase 6 (learning dan signifikansi) sudah diimplementasikan, lihat [arsitektur](../arch-18-business-ontology.md). Fase 7 ditunda.
**Branch:** `feat/supernova-ontology` (dari `origin/main` @ `1ecb13c7`)
**Sumber:** *Supernova Business Ontology v1.1* (6 Nov 2026), dibandingkan dengan source Nova saat ini.

Dokumen ini menerjemahkan blueprint SBO ke arsitektur Nova yang sebenarnya.
Blueprint ditulis dari ZIP yang terpotong. Di bawah ini setiap klaim sudah
dicek terhadap kode di branch ini, dan setiap bagian yang bertentangan dengan
invariant di `AGENTS.md` sudah disesuaikan.

---

## 1. Ringkasan

Semua file yang dirujuk blueprint ada di repo, dan sebagian besar fondasinya
sudah lebih matang daripada yang diasumsikan blueprint:

- Ada publish semantic view dengan CAS dan fingerprint.
- Ada store yang revisioned dengan optimistic concurrency.
- Reader proof untuk News sudah dijalankan per pembaca.
- Mission sudah memiliki anchor, release pin, dan reauthorization saat resume.
- Ada review KnowledgeRevision.
- Ada action gateway dengan approval, fence, dan readback.

Yang benar-benar **belum ada** ada lima:

1. **Lapisan makna bisnis yang kanonis.** Belum ada domain, concept berversi,
   typed relation, representation, stewardship, atau release ontology. Yang
   ada sekarang hanya `synonyms` dan `owner_domain` di dalam JSON definisi
   semantic view, ditambah `resolve_metric` yang terbatas pada satu view.
2. **Identitas event bersama** antara `Story` (Newsroom) dan `NewsItem` (monitor).
3. **Handoff dari News ke Studio yang membawa referensi aman.** Tombol "Ask
   Studio about this" saat ini hanya mengirim teks headline sebagai prompt.
4. **InvestigationUpdate yang direview** dan ditampilkan kembali di News.
5. **Promosi pengetahuan ke ontology** (proposal → steward → release).

Rekomendasinya: bangun nomor 1 dahulu sebagai vertical slice yang berdiri
sendiri (ontology core → resolver di Studio), lalu nomor 2–4 (jembatan News ↔
Agent), lalu nomor 5. Object/action runtime (Bab 11/23 blueprint) **ditunda**,
karena action gateway yang ada sudah cukup untuk pilot.

---

## 2. Verifikasi baseline: klaim blueprint vs kode

| Klaim blueprint | Fakta di repo | Implikasi |
|---|---|---|
| Semantic Views punya version/publish/validation | `CONFIG_SEMANTIC_VIEWS` dan `CONFIG_SEMANTIC_VIEW_VERSIONS` dengan status DRAFT→VALIDATED→ACTIVE→DEPRECATED. Fingerprint = sha256 IR kanonis. Publish memakai `metadata_lock`, CAS pada `active_version`, dan audit (`semantic_views.py:865-1037`) | Representation cukup mem-pin `SemanticRef(view_id, version, fingerprint)` (`contracts.py:73`) |
| Metric expression fingerprint | Fingerprint per metric sudah ada: `_claim_fingerprint` (`context_graph.py:466`) | Dipakai ulang untuk mendeteksi drift representation |
| Resolver metric | `resolve_metric` (`context_graph.py:213`) mencocokkan nama/synonym dan mendeteksi konflik `authority=canonical` + `owner_domain`, tetapi **hanya di dalam satu view** | Inilah gap G01. Resolver SBO menjadi lapisan lintas view di atasnya |
| ContextGraph scoped | Node ID = fingerprint(principal, role, sec-ctx, …) | Sesuai blueprint: jangan dijadikan ID concept |
| Endpoint "concepts" | `POST /intelligence/context/concepts` sudah ada, tetapi ini *user-statement context node* (domain/system/deployment/incident), bukan concept bisnis | **Ada tabrakan nama.** Concept SBO harus memakai prefix route dan istilah UI yang berbeda |
| Search role-filtered | `search.py` hanya memeriksa akses per index, lalu membaca proyeksi dengan `execute_system` tanpa filter per baris (`:892`) | Concept search **tidak boleh** memakai index ini. Filter visibilitas harus dilakukan di service ontology |
| Autopilot discovery | `autopilot.py` membuat proposal semantic (metric, synonym, …) di `CONFIG_AGENT_RULE_PROPOSALS` dengan alur pending→preview→approve | Dipakai ulang sebagai *producer* draft concept |
| Reader proof News | `newsroom_service.story(id, user)` menjalankan ulang proof sebagai pembaca, hasilnya dicache per user/role/sec-ctx/epoch, dan mengembalikan 404 + audit DENIED (`newsroom.py:1062-1137`) | Handoff harus melewati jalur ini |
| NewsItem/Investigation/Decision/Outcome | Status dan `causal_status` sesuai blueprint. Ada `POST /news/{id}/investigate`, `automatic_investigation`, dan link ke Mission | Model incident tidak perlu dibangun dua kali |
| Mission anchors | `SemanticAnchor`, `MissionReleasePin`, create yang idempoten (uuid5 + `operation_id`), `_reauthorize_resume` | Tambahkan anchor ontology/incident |
| Release manifest agent | `_capture_manifest` mem-pin semantic_views, tools, prompt, dan sebagainya (`releases.py:254`) | Release ontology menjadi dependency baru di manifest |
| Learning | `KnowledgeRevision` (HYPOTHESIS…VERIFIED, PRIVATE/DOMAIN) dengan review yang idempoten dan diaudit | `knowledge_propose` = alur yang sama, ditambah target "ontology proposal" |
| Action gateway | 13 status, approval, consent, dispatch fence at-most-once, verifikasi readback | Fase object/action cukup menambah adapter |
| Outbox | **Tidak ada.** Pola yang dipakai: tulis ke NOVA_SYSTEM dulu → Redis Stream → reconciler (`task_orchestration/`) | Lihat keputusan D4 |
| Migration | Tidak ada runner. Schema dibuat oleh `docker/init-nova.sql` dan `ensure_*_schema()` saat startup (`main.py:153-357`) | Tabel baru mengikuti pola `ensure_schema` |
| Tenant | **Tidak ada `tenant_id`.** Satu deployment = satu tenant. Isolasi berdasarkan principal/role/sec-ctx | Lihat keputusan D2 |

---

## 3. Bagian blueprint yang bertentangan dengan invariant Nova

Bagian-bagian ini **harus diubah**, bukan disalin apa adanya:

| Blueprint | Invariant `AGENTS.md` | Penyesuaian |
|---|---|---|
| Feature flag di banyak tempat: `sbo_agent_preflight`, `sbo_news_signal_adapter`, `incident_auto_correlation`, `object_actions`, "shadow mode", kill switch per tenant (§12.4, §31, §33.2) | *New features are active by default*: tidak boleh ada flag `*_ENABLED` atau mode opt-in tanpa permintaan eksplisit | Fitur langsung aktif dan hanya digating oleh **prasyarat nyata**: resolver baru membatasi pemilihan metric bila ada release ontology yang terpublish dan concept terkait punya representation VALIDATED. Tanpa itu, perilaku tetap seperti sekarang. Ini gating prasyarat, bukan flag. "Shadow comparison" diganti eval trajectory dan Quality Lab |
| `tenant_id` di semua tabel dan API | Tidak boleh menciptakan konsep baru tanpa owner. Nova single-tenant | Hilangkan `tenant_id`. Scope = deployment. Kunci logis menjadi `(home_domain, qualified_slug)` |
| Tabel `sbo_*` / `be_*` dan "dedicated metadata store bila perlu" | Metadata relasional durable hanya di `NOVA_SYSTEM`, dengan nama flat `CONFIG_*` | `NOVA_SYSTEM.CONFIG_ONTOLOGY_*` dan `CONFIG_INTELLIGENCE_SIGNALS/INCIDENTS` dengan pola revisioned JSON milik `IntelligenceRepository` |
| Transactional outbox, `be_outbox` | Jangan membuat scheduler atau queue paralel | ID signal deterministik dari `(story_id, revision, metric)`, upsert idempoten, ditambah job reconciler di task orchestration (handler `intelligence.signals`) |
| Role baru: "Domain steward", "Release manager", "Security admin" | StarRocks mengautentikasi, Ranger mengotorisasi, tidak boleh ada user store lain. `SHOW GRANTS` boleh dipakai untuk role-marker | Steward = **role StarRocks** yang ditunjuk sebagai `steward_role` milik sebuah domain. Persetujuan butuh active role = steward role tersebut (role-marker lewat `SHOW GRANTS`). Release manager = role marker yang sama atau `ACCOUNTADMIN`. Ranger tetap mengatur akses data |
| Signed review / "independent signer" | Gunakan audit yang ada | Review = record berversi + `write_audit_log` dengan before/after fingerprint. "Signature" = fingerprint manifest + identitas reviewer dari sesi, bukan PKI |
| Graph DB, vector index | Jangan menambah infrastruktur paralel | Adjacency relasional dengan traversal terbatas (depth ≤2). Pencarian leksikal (nama, alias). Vector ditunda |
| Modul `business_events/` terpisah dengan state machine incident sendiri | Jangan membuat subsistem paralel bila owner sudah ada | Signal/Incident menjadi record kind baru di modul `intelligence` (repository, audit, dan router yang sama), bukan modul baru |
| Nama tool `sbo.resolve`, `news.incident_open` | Nama tool di registry memakai snake_case (`context_graph`, `decision_lab`) | `ontology_resolve`, `ontology_explain`, `incident_context`, `investigation_update`, `knowledge_propose` |

---

## 4. Keputusan arsitektur (pengganti ADR-001..012)

| # | Keputusan | Pilihan yang direkomendasikan |
|---|---|---|
| D1 | Lokasi modul | `backend/app/modules/ontology/` untuk lapisan makna. Event/incident diperluas di `intelligence/` |
| D2 | Tenant | Single-tenant per deployment, tanpa `tenant_id` |
| D3 | Penyimpanan | `NOVA_SYSTEM.CONFIG_ONTOLOGY_{DOMAINS,CONCEPTS,RELATIONS,REPRESENTATIONS,PROPOSALS,RELEASES}` dengan pola append-revision `(id, revision, operation_id) + payload JSON`. Alias `current` release memakai CAS seperti `active_version` semantic view |
| D4 | Pengiriman event | Tidak ada outbox. ID deterministik + upsert idempoten + reconciler. Klaim yang dipakai: at-least-once, tanpa efek ganda |
| D5 | Identitas concept | ULID immutable + `qualified_name` (`Finance.NetRevenue`). Ganti label = versi baru, ID tetap |
| D6 | Otoritas angka | Semantic Views tetap satu-satunya jalur komputasi. Ontology hanya **membatasi** view/metric yang boleh dipakai planner |
| D7 | Steward | Role StarRocks per domain (role-marker). Equivalence lintas domain butuh dua steward |
| D8 | Release | Manifest immutable = daftar `(member, version, fingerprint)`. Publish = CAS pada alias. Rollback = memindahkan alias ke release sebelumnya |
| D9 | Visibilitas concept | Domain punya `visibility_roles`. Filter dilakukan **sebelum** ranking dan hitungan, dengan 404 yang tidak membocorkan keberadaan resource |
| D10 | Korelasi incident | Aturan deterministik: concept yang terhubung edge approved + population fingerprint + jendela waktu yang selaras. Tanpa label kausal |
| D11 | Learning | Proposal → review steward → release. Agent hanya bisa *propose* |
| D12 | Object runtime | Ditunda sampai Fase 5 selesai |

---

## 5. Rencana bertahap

Setiap fase berakhir dengan perubahan yang bisa dipakai dan dites. Setiap fase
juga memenuhi validasi CI yang relevan: unit + coverage, eval scorecard, ruff
pada file yang diubah, dan mypy. Fase yang menyentuh DB/auth juga butuh
integration seeded di stack `docker` yang sedang berjalan. Fase frontend butuh
`pnpm lint/build/test:coverage`.

### Fase 1 — Ontology core (backend)

**Tujuan:** domain, concept, relation, dan representation yang berversi, dengan
alur proposal → review → release.

- `ontology/contracts.py`: Pydantic `Contract` (extra=forbid, seperti `intelligence/contracts.py`) untuk `Domain`, `Concept`, `ConceptVersion`, `Relation`, `Representation`, `Proposal`, `Release`. Kinds: `TERM, BUSINESS_ENTITY, METRIC, DIMENSION, MEASURE, POLICY, GOAL`. Kinds lain menyusul.
- `ontology/schema.py` + `ensure_schema()` dipanggil dari `main.py`, dengan DDL yang dicerminkan di `docker/init-nova.sql`.
- `ontology/repository.py`: memakai ulang `IntelligenceRepository.save` (optimistic revision + `metadata_lock`) atau mengekstrak helper bersama tanpa menduplikasi.
- `ontology/relations.py`: registry tipe dalam kode (`IS_A, HAS_PART, DERIVES, MEASURES, APPLIES_TO, EQUIVALENT_TO, RELATED_TO, AFFECTS_GOAL`) dengan aturan domain/range, deteksi siklus untuk `IS_A`, dan dual-steward untuk `EQUIVALENT_TO`.
- `ontology/representations.py`: binding concept → `SemanticRef` + metric + `_claim_fingerprint`, dengan grain, unit, dan time semantics. Validasi memakai IR yang terpublish. Status `CANDIDATE|VALIDATED|STALE|REVOKED`.
- **Drift:** hook di `semantic_views.publish` (setelah `_relink`) menandai representation menjadi STALE bila fingerprint metric berubah.
- `ontology/stewardship.py`: proposal terhadap `base_release_id`, review yang idempoten (`operation_id` + digest, sama seperti `memory.review`), dan audit.
- `ontology/releases.py`: prepare (validasi siklus, representation, dan konflik) → publish CAS alias → rollback.
- `ontology/bootstrap.py`: membuat concept DRAFT dan representation CANDIDATE dari view yang terpublish (metric, synonym, `owner_domain`).
- `ontology/router.py` dipasang di `/ontology`: concepts (resolve/get/related/representations), proposals, reviews, releases.
- **Test:** SBO-001..007, OPS-002, siklus `IS_A`, persetujuan sepihak `EQUIVALENT_TO`, rename dengan ID tetap, rollback, dan concept tersembunyi yang tidak bocor.

**Gate:** ID immutable, publish/rollback atomik lewat CAS, review wajib, dan tidak ada nama concept yang bocor antarrole.

### Fase 2 — Studio jadi ontology-aware

- `ontology/resolver.py`:
  1. Exact `qualified_name`.
  2. Alias.
  3. Leksikal.
  4. Filter visibilitas.
  5. Deteksi ambiguitas.
  6. Pemilihan representation (purpose, status VALIDATED, `authorize_semantic` milik caller).
- Hook di `semantic_query.py` antara `_resolve_model` (:177) dan `generate_plan` (:224): concept yang terselesaikan **membatasi** view dan metric. Bila ambigu, keluarkan `CLARIFICATION_REQUIRED` (jalur :232 yang sudah ada). Bila concept approved tapi tidak punya representation yang valid, kembalikan `NO_AUTHORIZED_REPRESENTATION` dan **jangan** fallback ke metric yang namanya mirip.
- Tool baru `ontology_resolve` dan `ontology_explain`. Keduanya read-only dan butuh consent, didaftarkan di `agents/registry.py` `KNOWN_TOOLS`, `tool_catalog.BUILTIN_TOOLS`, dan `AGENT_BUNDLEABLE_TOOLS`. Tool ini menyerap sebagian peran `context_graph` untuk term lintas view. `context_graph` tetap dipertahankan.
- Release manifest: dependency `ontology_release` (id + manifest hash). `assert_tool_contracts` dan `authorize_release_pin` memvalidasinya.
- Mission: `BusinessContextAnchor(concept_id, version, release_id)` sebagai field opsional yang tetap bisa membaca Mission lama.
- **Eval:** trajectory Finance.Revenue vs Sales.Revenue (klarifikasi), representation stale (fail closed), concept tersembunyi, dan resume setelah release berubah. Tambahkan scorer `concept_resolution` di Quality Lab.

**Gate:** golden set pilot ≥95% resolusi benar atau klarifikasi yang benar, nol fallback ke metric yang salah, dan eval scorecard lulus.

### Fase 3 — UI steward ontology (frontend)

Dibangun sebagai halaman Console `AI & ML → Business Ontology`, di sebelah Semantic Views, karena pekerjaan steward dan release adalah governance, bukan percakapan Studio. Isinya: Domains, Concepts (definisi, alias, versi, diff), Relations, Representations (status + link ke metric view), Proposals (inbox + review), dan Releases (impact, publish, rollback). Jangan memakai istilah "concept" bila sudah dipakai oleh context panel. Ikuti `DESIGN.md` dan `frontend/AGENTS.md`. Graph explorer opsional.

### Fase 4 — Signal dan Incident (backend, News tetap seperti sekarang)

- Record kind baru di `intelligence`, yaitu `signals` dan `incidents`, memakai `engine_schema` dan `IntelligenceRepository`.
- Adapter Story → Signal setelah `run_cycle` menyimpan edition. ID = fingerprint(story_id, revision, metric). Signal mem-bind concept lewat representation. Bila tidak terikat, tandai `ONTOLOGY_UNBOUND` dan buat proposal mapping.
- Adapter NewsItem → Signal di `run_monitor`.
- Correlator deterministik (D10) → Incident. Lifecycle mengikuti blueprint §6.3. Transisi memakai `expected_revision` + `operation_id`.
- Proyeksi per pembaca: setiap signal sumber dibuktikan ulang lewat `newsroom_service.story` / NewsItem entitlement. Signal tersembunyi tidak dihitung dan tidak disebut.
- Reconciler: handler `intelligence.signals` di task orchestration.
- **Test:** NEWS-001..005, OPS-001.

**Catatan implementasi (berbeda dari rencana di atas):**

- **Penyimpanan.** Signal dan incident disimpan di tabel revisi bersama (seperti tabel ontology), bukan di `IntelligenceRepository`. Repository itu mengikat setiap record ke satu principal dan role, sehingga pembaca lain tidak akan pernah bisa melihat incident yang sama.
- **ID signal.** ID diturunkan dari sumber dan digest proof-nya, bukan dari revisi Story. Revisi yang hanya mengubah narasi tetap menjadi signal yang sama.
- **Tanpa proposal mapping otomatis.** Signal yang tidak terikat hanya ditandai `ONTOLOGY_UNBOUND`. Concept baru membutuhkan domain dan definisi yang belum diketahui pada saat deteksi.
- **Tanpa handler reconciler terpisah.** Setiap cycle News mencatat ulang semua story di edisinya, dan setiap run monitor mencatat ulang deteksinya. Cycle berikutnya otomatis menyelesaikan pencatatan yang sempat terputus.

### Fase 5 — Jembatan News ↔ Studio

- `POST /intelligence/stories/{id}/investigate`:
  1. Proof pembaca.
  2. Incident.
  3. Mission dibuat atau dilanjutkan secara idempoten (`operation_id` = hash(story, revision, principal, role)).
  4. Thread Studio berisi **referensi**, bukan teks headline.
- Tool `incident_context` (read-only) dan `investigation_update` (`session_change`, consent). Update ditolak bila ref evidence/query tidak terverifikasi.
- Review update → timeline incident di News: tombol "Ask Studio about this" diganti "Investigate in Studio", ditambah timeline dan badge concept di `story-panel.tsx`.
- **Test:** STUDIO-001..005, NEWS-004, role diturunkan di tengah alur, dan eval trajectory untuk kedua tool.

**Catatan implementasi Fase 5:**

- **Siapa yang melihat temuan.** Temuan investigasi hanya ditampilkan ke pembaca dengan active role yang sama dengan penulisnya, yang juga bisa membuktikan semua signal yang dilihat penulis. Reviewer adalah pembaca lain di role itu. Alasannya: angka di temuan dihitung di bawah data policy role penulis.
- **Agent yang menerima handoff.** Handoff membuka agent Smart. Karena itu `incident_context` dan `investigation_update` ditambahkan ke tool root Smart, selain bisa dipilih untuk agent lain.

### Fase 6 — Learning dan significance

- `knowledge_propose`: dari outcome, feedback, atau koreksi menjadi proposal ontology (Fase 1). Taksonomi feedback diperluas dari like/dislike secara aditif.
- Significance score deterministik untuk ranking News, dengan bobot sebagai hipotesis yang dikalibrasi lewat label.
- **Test:** LEARN-001/002, STUDIO-004.

**Catatan implementasi Fase 6:**

- **Taksonomi feedback belum diperluas.** Feedback News tetap like/dislike, dan dislike hanya mengubah urutan berita untuk pembaca itu. Laporan seperti "faktanya salah" atau "duplikat" belum dibuat.
- **Uji langsung dengan model default** sudah dilakukan untuk empat alur: grounding, klarifikasi ambiguitas, definisi resmi, dan proposal alias. Skor signifikansi belum diuji langsung karena stack dev belum punya News aktif.

### Fase 7 (ditunda) — Object dan action runtime

Pilot Customer → escalation tiket lewat adapter baru di `actions.py`. Fase ini baru dikerjakan setelah Fase 5 terbukti end-to-end.

---

## 6. Risiko utama

| Risiko | Mitigasi |
|---|---|
| Kebocoran nama concept lewat search, count, atau traversal | Filter di service sebelum ranking. Tidak memakai index `search.py`. Test negatif di setiap endpoint |
| Representation salah, sehingga angka finance salah | Representation baru dipakai setelah VALIDATED. Drift otomatis menjadi STALE. Tanpa fallback berdasarkan nama |
| Tidak ada transaksi multi-tabel di StarRocks | Urutan tulis yang bisa di-replay, ID deterministik, dan reconciler, mengikuti pola semantic publish |
| Steward backlog | Bootstrap dari view yang terpublish. Pilot cukup satu domain dengan 20–30 concept |
| Scope creep ke ERP/object graph | Fase 7 ditunda dan dikunci oleh gate Fase 5 |
| Tabrakan istilah "concept" dengan context panel | Istilah UI dan route yang berbeda |

## 7. Pertanyaan terbuka untuk product owner

1. Domain pilot dan 20–30 concept pertama. Blueprint menyarankan Finance / Customer Health. Data demo apa yang tersedia di stack lokal?
2. Role StarRocks mana yang menjadi steward per domain? Apakah `ACCOUNTADMIN` boleh menjadi fallback release manager?
3. Apakah Fase 3 (UI) dikerjakan paralel dengan Fase 2, atau setelahnya?
