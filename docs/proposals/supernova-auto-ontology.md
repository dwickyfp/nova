# Proposal: Supernova Auto-Ontology

Status: Fase 1–3 diimplementasikan 7 November 2026 ([derived layer](../arch-18-business-ontology.md#derived-layer), [drift](../arch-18-business-ontology.md#drift), [promosi](../arch-18-business-ontology.md#promotion), [lint makna](../arch-18-business-ontology.md#meaning-checks), [nama dari Nova](../arch-18-business-ontology.md#names-from-nova)); fase 4–5 belum. Lint fase 3 belum memprofil data (hari tutup, cardinality) karena butuh eksekusi query terotorisasi; itu menunggu keputusan 5.3.

Tujuan: business ontology yang dibangun dan dirawat oleh engine Nova, bukan diketik manusia: lapisan semantik berbasis graf di atas data yang membuat query lebih deterministik, konteks untuk agent lebih baik, dan jawaban bisa dijelaskan. Manusia tetap memutuskan *makna resmi*, tapi pekerjaannya berubah dari menulis menjadi meninjau bukti.

Referensi: [NVIDIA auto-ontology @ c447003](https://github.com/NVIDIA/auto-ontology/tree/c447003f72a822301e927196a4ecb5b597c308d5) dan [arsitektur SBO saat ini](../arch-18-business-ontology.md).

## 1. Kondisi sekarang

SBO punya model governance yang matang: domain, sepuluh jenis konsep, relasi bertipe, binding metrik yang dipin dengan claim fingerprint, proposal dengan steward, release immutable, dan drift. Kelemahannya ada di sisi pengisian:

| Bagian | Sekarang | Masalah |
|---|---|---|
| Konsep | Diketik manusia; `bootstrap` membuat satu konsep METRIC per metrik publik | Tidak ada entitas (Outlet, Produk, Salesman), dimensi, atau definisi yang bermakna; definisi bootstrap berbunyi "a steward must describe its meaning" |
| Relasi | Diketik manusia | Release lab saat ini: 11 konsep, 0 relasi. Graf tidak bisa ditelusuri |
| Alias | Diketik steward | Pertanyaan berbahasa Indonesia gagal kecuali steward menambahkan alias |
| Resolusi Studio | Leksikal, hanya METRIC/MEASURE | Konsep dimensi dan entitas tidak pernah ikut menggrounding pertanyaan |
| Drift | Perubahan apa pun pada claim membuat binding `STALE` | Republish `delivery_routes` yang hanya mengganti nama dimensi waktu (`route_date` di atas `plan_date`) memblokir dua konsep sampai steward meninjau |
| Pembelajaran | `knowledge_propose` dari koreksi pengguna | Penolakan Studio, query terverifikasi, dan pola pemakaian tidak menjadi usulan |

Bahan untuk otomatisasi sebenarnya sudah ada dan punya pemilik:

- Semantic View yang terpublikasi: dataset, grain, field, relationship berkardinalitas, metrik, sinonim, sample value (`app/modules/agents/semantic/ir.py`).
- `context_graph.project_semantic_view` dan `project_usage_context` (`app/modules/intelligence/context_graph.py`).
- Sumber belajar yang sudah diotorisasi ulang: `semantic_usage`, `verified_query`, `mission_deliverable`, `decision`, `outcome`, `context_query_pattern` (`app/modules/agents/semantic/learning_sources.py`).
- `entities.py` (registry entitas bisnis), `retrieval.py` (rank fusion deterministik), Ossie import/export, `create_semantic_view` (desain view dengan LLM).

## 2. Pelajaran dari NVIDIA auto-ontology

Yang layak diadopsi:

1. **Profiling deterministik dulu, LLM kemudian.** Sample value, `is_unique`, distinct kecil, dan format tanggal dikumpulkan sebelum model dipanggil. Setiap keluaran LLM berupa struktur (temperature 0) dan setiap nama yang dikembalikan dicek terhadap daftar kandidat.
2. **Dua jenis sisi yang berbeda:** kolom *adalah* atribut sebuah Term (`HAS_ATTRIBUTE`) versus kolom *mereferensikan* atribut Term lain (`SEMANTIC_FK`). Ini memisahkan "Outlet punya kota" dari "Order menunjuk ke Outlet".
3. **Bukti struktural harus setuju dengan LLM.** Tabel junction hanya ditandai bila jawaban LLM dan bukti struktural (minimal dua peran FK) cocok. FK hasil inferensi dipangkas bila nilainya tidak beririsan dengan target.
4. **Join path ditemukan BFS di graf, dengan penelusuran balik hanya dari primary key**, sehingga dua FK ke target yang sama tidak menghasilkan join palsu.
5. **Dedup query history yang mengabaikan literal** (sqlglot, urutan kolom tidak penting) sebagai dasar pola pemakaian.
6. **Permukaan MCP read-only yang dikurasi** (`search_terms`, `get_term`, `check_answerable`).

Yang jangan ditiru:

- Tidak ada confidence atau bukti yang disimpan pada sisi hasil inferensi.
- Sertifikasi hanya bendera tanpa efek runtime; tidak ada versi, review, atau release. Reset penuh menghapus kurasi.
- Ranking pemakaian query history mati (`_usage_score` selalu 0); sisi join dari query log ditulis tapi tidak pernah dibaca.
- Metrik hanyalah string SQL bebas tanpa grain, unit, atau additivity.
- SQL akhirnya tetap ditulis LLM; ontologi hanya menjadi petunjuk di prompt.

Kesimpulan: auto-ontology kuat di *mengisi* graf secara otomatis, lemah di *mempercayai* isinya. SBO kebalikannya. Desain di bawah menggabungkan keduanya.

## 3. Desain

### 3.1 Dua lapisan makna

| Lapisan | Isi | Dibuat oleh | Dipakai Studio sebagai |
|---|---|---|---|
| **Derived** (baru) | Entitas, dimensi, metrik, relasi, alias, join path, definisi draf, masing-masing dengan bukti dan confidence | Ontology compiler, otomatis dan terus-menerus | Konteks dan kandidat, selalu diberi label "turunan, belum resmi" |
| **Official** (SBO sekarang) | Definisi yang disetujui steward dan dirilis | Promosi dari lapisan derived, atau usulan manual | Grounding yang mengikat: binding wajib, penolakan bila tidak ada representasi |

Lapisan derived tidak pernah menimpa makna resmi. Bila keduanya bertentangan, makna resmi menang dan konflik dicatat sebagai temuan. Ini mengikuti `authority_precedence` yang sudah ada di `context_graph`.

### 3.2 Ontology compiler

Satu pipeline per Semantic View yang terpublikasi, dijalankan saat publish dan secara terjadwal (scheduler yang sudah ada), idempoten per versi view.

1. **Harvest (deterministik).** Baca IR view: dataset dan grain, field, relationship dan kardinalitas, metrik dan ekspresinya, sinonim, sample value. Tambahkan statistik profil lewat query terotorisasi milik owner view (bukan root): cardinality, null rate, uniqueness, nilai distinct kecil, rentang tanggal.
2. **Infer struktur (deterministik).**
   - Dataset dengan grain → konsep `BUSINESS_ENTITY` (Outlet, Produk, Pegawai). Dataset yang dipakai dengan peran berbeda (`employees` sebagai salesman dan driver) → peran, bukan entitas ganda.
   - Field dimensi non-identifier → `DIMENSION` yang `APPLIES_TO` entitasnya.
   - Relationship many-to-one → `HAS_PART`/`APPLIES_TO` antar entitas, dengan bukti kardinalitas dan value overlap.
   - Metrik → `METRIC` yang `MEASURES` entitas base dataset-nya. Ekspresi metrik diparse: metrik yang dibangun dari metrik lain → `DERIVES` (misalnya `net_revenue` dari `sales_before_returns` dan `returned_value`).
   - Metrik dengan claim fingerprint sama di view berbeda → kandidat `EQUIVALENT_TO`.
3. **Enrich (LLM, divalidasi).** Definisi bisnis dari ekspresi, deskripsi, dan sample. Alias multibahasa, termasuk Bahasa Indonesia sehari-hari ("omzet", "penjualan bersih"). Klasifikasi nama yang ambigu. Setiap keluaran wajib menyebut id katalog yang ada, lolos validator, dan ditolak bila mengklaim kausalitas.
4. **Lint makna (deterministik).** Aturan yang menemukan kesalahan seperti yang muncul di review SerbaData:
   - metrik penjualan di atas tabel yang punya kolom status dengan nilai `Returned`/`Cancelled` tanpa memperhitungkannya;
   - metrik rasio yang dinyatakan additive;
   - dimensi identifier yang ditawarkan sebagai breakdown;
   - data harian dengan hari yang tidak pernah muncul (hari tutup).
5. **Simpan dengan bukti.** Setiap item derived menyimpan `evidence[]` (jenis sumber, referensi, metode `deterministic|llm|usage`, skor), `confidence`, versi compiler, dan fingerprint input. Kompilasi ulang hanya menulis revisi baru bila fingerprint berubah.

### 3.3 Promosi ke makna resmi

Steward tidak lagi menulis. Mereka meninjau satu antrian berisi diff dan bukti, lalu menyetujui per batch. Tiga jalur:

| Jenis perubahan | Jalur |
|---|---|
| Re-pin binding karena drift **kosmetik**: SQL hasil kompilasi metrik identik setelah normalisasi (ganti nama dimensi waktu, deskripsi, sinonim) | Otomatis, diaudit, tanpa steward. Ini menghilangkan kasus `DistanceTravelled` di atas |
| Alias baru, entitas atau dimensi baru, relasi struktural dengan bukti deterministik, definisi draf untuk konsep tanpa definisi | Usulan batch dari compiler (`source=ENGINE`), satu klik per domain, dengan bukti di tiap baris |
| Perubahan definisi metrik, `EQUIVALENT_TO` lintas domain, deprecate, merge | Usulan biasa, steward wajib |

Aturan yang tidak berubah: pengusul tidak pernah meninjau usulannya sendiri, release tetap oleh `ACCOUNTADMIN`, dan konsep yang terlihat tidak pernah memberi akses data.

### 3.4 Belajar dari pemakaian

Sumber yang sudah ada di `learning_sources.py`, ditambah sinyal Studio:

- **Penolakan Studio** (`NO_AUTHORIZED_REPRESENTATION`, metrik turunan yang tidak tersedia, istilah yang tidak ter-resolve) menjadi usulan dengan contoh pertanyaan. Contoh nyata: "rata-rata selisih jarak aktual vs rencana per driver" → metrik `avg_distance_gap_km`.
- **Query terverifikasi dan pola pertanyaan** menjadi alias dan relasi `RELATED_TO`, dengan frekuensi sebagai bukti (dedup yang mengabaikan literal seperti auto-ontology, tanpa menyimpan nilai filter).
- **Koreksi pengguna** lewat `knowledge_propose` tetap seperti sekarang.

Semua pembelajaran hanya menghasilkan usulan atau item derived. Tidak ada yang mengubah makna resmi tanpa review.

### 3.5 Konteks dan query untuk agent

1. **Resolusi:** leksikal pada makna resmi (seperti sekarang) lalu kandidat dari lapisan derived dengan fusi peringkat deterministik (`retrieval.py`). Embedding boleh mengusulkan kandidat, tidak pernah memutuskan makna resmi.
2. **Konteks graf:** dari konsep yang ter-resolve, telusuri secara terbatas (`traverse_context`) ke entitas, dimensi yang berlaku, metrik terkait, dan join path, lalu bentuk paket konteks ringkas untuk planner. Ini menggantikan katalog metrik penuh di prompt.
3. **Query deterministik:** planner Semantic View tetap satu-satunya penyusun SQL. Ontologi membatasi metrik, dimensi, dan join yang boleh dipakai, dan rencana yang melanggarnya ditolak sebelum dieksekusi.
4. **Penjelasan:** setiap hasil membawa `business_context` (release, konsep resmi dan derived yang dipakai, bukti, join path), sehingga UI bisa menunjukkan "omzet = setelah diskon dan retur (resmi, ont_rel_0002)" atau "Driver → Rute (turunan, confidence 0,92)".

### 3.6 Penyimpanan

Tabel append-only baru di `NOVA_SYSTEM`, mengikuti pola `CONFIG_ONTOLOGY_*`: `CONFIG_ONTOLOGY_DERIVED_ITEMS` (konsep, relasi, alias derived, dengan bukti, confidence, versi compiler) dan `CONFIG_ONTOLOGY_COMPILATIONS` (riwayat run, fingerprint input, metrik kualitas). Tidak ada database graf baru: graf ditelusuri dari tabel, seperti `context_graph` dan auto-ontology.

## 4. Fase

| Fase | Isi | Kriteria selesai |
|---|---|---|
| 1 | Compiler deterministik dari Semantic View: entitas, dimensi, metrik, relasi struktural, `DERIVES` dari ekspresi; tabel derived dengan bukti; tab "Derived" di Business Ontology | Untuk tiga view lab: entitas Outlet, Produk, Pegawai (peran salesman dan driver), relasi antar mereka, dan graf yang bisa ditelusuri, tanpa input manusia |
| 2 | Klasifikasi drift kosmetik vs semantik dengan re-pin otomatis; antrian review batch dengan bukti | Republish `delivery_routes` yang sama seperti hari ini tidak membuat binding `STALE` |
| 3 | Enrichment LLM (definisi, alias multibahasa) dengan validator; lint makna | Pertanyaan berbahasa Indonesia ter-resolve tanpa alias manual; lint menemukan `net_revenue` yang mengikutkan retur |
| 4 | Konteks graf untuk planner Studio, provenance di jawaban, kandidat derived dalam resolusi | Skor judge 11 pertanyaan SerbaData naik; setiap jawaban menyebut konsep dan sumbernya |
| 5 | Belajar dari penolakan, query terverifikasi, dan pola pertanyaan | Penolakan "selisih jarak per driver" menghasilkan usulan metrik dalam satu siklus |

Evaluasi: set judge tetap (pertanyaan, jawaban SQL acuan, konsep yang diharapkan) dijalankan di Quality Lab per fase, ditambah metrik kualitas ontologi: cakupan field dan metrik, relasi yang lolos value overlap, alias yang ter-resolve, dan drift yang ditangani otomatis.

## 5. Keputusan yang perlu diambil

1. Apakah item derived boleh dipakai Studio sebagai konteks sebelum disetujui, dengan label "turunan"? Rekomendasi: ya; tanpa ini sistem tidak terasa pintar.
2. Apakah re-pin drift kosmetik boleh otomatis tanpa steward? Rekomendasi: ya, karena SQL yang dihasilkan terbukti identik.
3. Siapa owner eksekusi profiling: owner Semantic View (rekomendasi) atau service principal yang diotorisasi eksplisit?
