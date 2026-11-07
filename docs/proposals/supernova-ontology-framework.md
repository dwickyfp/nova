# Proposal: Supernova Ontology Framework

Status: konsep, 7 November 2026. Melanjutkan [auto-ontology](supernova-auto-ontology.md) (fase 1–3 sudah diimplementasikan) dan mengganti arah fase 4–5. Riset lengkap dengan sumbernya: [docs/research/supernova-ontology](../research/supernova-ontology/README.md).

## 1. Tujuan

Supernova menjadi framework ontologi milik Nova yang matang dan akurasinya tinggi untuk data platform on-premise. Targetnya bersaing dengan Snowflake dan Databricks dalam memberi konteks bisnis yang benar kepada agent.

Ada tiga syarat:

1. **Berbasis data, bukan asumsi.** Setiap klaim (entitas, join, peran, rumus, sinonim) membawa bukti terukur dan probabilitas benar yang terkalibrasi.
2. **Minim manusia.** Mesin memutuskan sendiri bila bukti kuat. Manusia hanya melihat item yang benar-benar ragu, diurutkan menurut dampaknya.
3. **Akurasinya terlihat.** Halaman utama menampilkan metrik akurasi, cakupan, kalibrasi, drift, dan otomatisasi. Teks dan tabel pindah ke tab lain.

Framework ini harus general. SerbaData (retail lapangan) adalah kasus uji pertama, bukan dasar desain.

## 2. Kondisi sekarang, diukur

Snapshot stack dev, 7 November 2026:

| Ukuran | Nilai | Catatan |
|---|---|---|
| Semantic View terpublikasi | 4 | `retail_sales`, `outlet_stock`, `delivery_routes`, `nova_business_360` |
| Item derived | 345 | 31 entitas, 74 dimensi, 59 metrik, 181 relasi |
| Metrik derived yang sudah resmi | 33 / 59 | Satu binding (GrossMargin) sedang `STALE` karena rumusnya baru dibetulkan |
| Konsep resmi | 42 | 34 metrik, 8 entitas |
| Usulan ENGINE | 15 | 9 diterapkan, 3 ditolak steward, 3 ditarik. Presisi kasar 9/12 = 75% |
| Distribusi confidence | 0,9 / 0,95 / 1,0 | **Konstanta per aturan, bukan hasil data, dan tidak pernah diuji terhadap hasil nyata** |

Kelemahan yang harus ditutup:

- **Confidence adalah asumsi.** Nilai 0,9 tidak berarti 90% benar, dan tidak ada yang mengukurnya.
- **Tidak ada profil data per kolom.** Join, peran, dan grain hanya dibaca dari deklarasi Semantic View. Nova tidak memeriksa apakah nilainya memang beririsan atau unik. Yang ada hanya `QUALITY_TABLE_STATS` per tabel.
- **Riwayat query belum dipakai** untuk menemukan join, metrik, atau sinonim. Datanya sudah ada di `USAGE_QUERY_STATS` dan Query Autopilot families.
- **Tidak ada gold set dan skor akurasi per release.** Uji LLM-as-a-judge dilakukan manual per sesi, tidak berulang, dan tidak menjadi gerbang release.
- **Steward masih mengetik dan membaca teks.** Halaman Business Ontology berisi tabel dan paragraf.

## 3. Lanskap: apa yang sudah dikirim pesaing

| Kemampuan | Snowflake | Databricks | Palantir | Fabric IQ | dbt | AtScale |
|---|---|---|---|---|---|---|
| Join dan entitas otomatis dari data | Diusulkan (Semantic View Autopilot: kardinalitas + history) | Manual | Manual | Diusulkan (ontology agent) | Manual | Manual |
| Metrik diusulkan dari pemakaian | Diusulkan (clustering definisi mayoritas) | – | – | Diusulkan | – | – |
| Verified query | Ada + diusulkan dari history | Ada (contoh SQL) | – | Verified answers | – | – |
| Benchmark dan evaluasi bawaan | Evaluasi dengan verified query | **Terkuat**: 500 pertanyaan, pencocokan hasil, LLM judge | – | – | Repo benchmark terbuka | Leaderboard TPC-DS |
| Belajar dari pemakaian | Ya, lewat saran | **Tidak** (dokumentasinya menyatakan begitu) | – | – | – | – |
| Drift data dan makna | Saran, pantau usage | Preview (anomali data) | – | Preview | – | – |
| Abstain di luar cakupan | – | – | – | – | Ya | – |
| On-premise | Tidak | Tidak | Jarang | Tidak | Core open source | Ya |

Angka akurasi yang dipublikasikan, hampir semuanya diukur sendiri oleh vendor:

| Sumber | Tanpa semantik | Dengan semantik |
|---|---|---|
| Snowflake Cortex Analyst (150 pertanyaan internal) | GPT-4o 51% | 90%+ |
| Databricks Genie (tugas internal, 2026) | 32% | >90% |
| dbt (benchmark terbuka, 11 × 20 run) | 64,5–90% | 98–100% dalam cakupan, abstain di luar cakupan |
| AtScale (TPC-DS, 40 pertanyaan) | 20% | 92,5% |
| data.world (skema asuransi) | 16,7% | 54,2%, dan 72,6% dengan pemeriksaan ontologi |
| Studi independen arXiv 2604.25149 (100 pertanyaan) | 45,5–50,5% | 67,7–68,7% |

**Celah yang bisa diisi Nova.** Tidak ada vendor yang menggabungkan empat hal ini sekaligus:

1. usulan otomatis dari statistik dan pemakaian;
2. benchmark berulang dengan LLM judge;
3. confidence terkalibrasi yang menentukan kapan mesin boleh memutuskan sendiri;
4. semantik yang tunduk pada kebijakan Ranger (row filter dan mask berlaku juga pada sampel dan pencocokan nilai), on-premise.

Snowflake mengusulkan tapi lemah di evaluasi. Databricks kuat di evaluasi tapi tidak belajar. Yang lain manual atau cloud-only. Ditambah skor akurasi per release yang transparan dan bisa direproduksi, itu posisi Supernova.

## 4. Prinsip

1. **Bukti dulu, model kemudian.** Statistik dan struktur adalah sumber kebenaran. LLM hanya memberi nama, menjelaskan, dan menengahi kasus ambigu. LLM tidak pernah menjadi sumber confidence, karena confidence yang ia nyatakan sendiri terlalu percaya diri.
2. **Setiap klaim berupa probabilitas terkalibrasi.** "0,92" harus berarti sekitar 92% klaim dengan skor itu benar. Ini diukur dengan ECE dan diagram reliabilitas.
3. **Manusia hanya di pita ragu.** Klaim dengan probabilitas tinggi diputuskan mesin. Yang menengah masuk antrian aktif, diurutkan menurut dampak (pemakaian dan sentralitas). Setiap keputusan steward menjadi label untuk melatih ulang.
4. **Semua yang ditampilkan bisa diukur, dan semua ukuran punya interval kepercayaan.** Bentuknya "akurasi 96% (93–98%, n=240, diverifikasi 3 hari lalu)", bukan "akurasi tinggi".
5. **Patuh kebijakan.** Profil data dijalankan di bawah principal yang berwenang. Yang disimpan hanya sketch dan statistik, nilai mentah tidak keluar dari StarRocks. Kolom yang di-mask atau difilter tidak menyumbang sampel.
6. **General.** Tidak ada aturan khusus satu domain. Kosakata tipe dan aturan berlaku untuk retail, keuangan, operasi, dan domain lain.
7. **Terbuka.** Impor dan ekspor Open Semantic Interchange (OSI), plus API/MCP untuk agent.

## 5. Arsitektur

```text
L0 Observasi      Profil kolom (NDV, null, rentang, pola, MinHash/HLL)   Semantic View IR
                  Riwayat query (join, agregat, alias)   Jejak Studio   Keputusan steward
        │
L1 Detektor       join/IND · kunci & grain · tipe kolom · peran · DERIVES · ekuivalensi
(labeling fn)     · sinonim (alias SQL, embedding, LLM) · kandidat metrik dari pemakaian
                  · definisi bertentangan · drift
        │
L2 Fusi           label model (weak supervision) → classifier terkalibrasi per jenis klaim
                  → p(benar) + daftar bukti
        │
L3 Keputusan      p ≥ τ_tinggi: terima otomatis (diaudit sampel) · pita ragu: antrian aktif
                  · p ≤ τ_rendah: tolak otomatis
        │
L4 Ontologi resmi konsep, relasi, binding, release (SBO yang ada)
        │
L5 Evaluasi       gold set · panel judge · estimasi akurasi sampel · eksekusi Studio
                  · kalibrasi · gerbang regresi release
        │
L6 Penyajian      konteks graf untuk Studio · indeks nilai patuh kebijakan · abstain di luar cakupan
```

### 5.1 L0: observasi

- **Profiler kolom (baru).**
  - Untuk setiap kolom di dataset yang dipakai Semantic View, dihitung di StarRocks dengan fungsi bawaan (`approx_count_distinct`/HLL, null rate, min/max, pola karakter):
    - NDV dan rasio unik;
    - null rate, rentang, dan kelas pola;
    - sketch MinHash nilai, untuk menghitung irisan antar kolom tanpa memindahkan nilai.
  - Disimpan sebagai statistik dan sketch berversi di `NOVA_SYSTEM`.
  - Eksekusi memakai principal yang diputuskan di [§10](#10-keputusan-yang-perlu-diambil). Bukan root, dan tidak pernah dengan kredensial pemanggil lain.
- **Penambang riwayat query.** Sumbernya `USAGE_QUERY_STATS` dan Query Autopilot families. Query di-parse dengan grammar StarRocks yang sudah ada, lalu diambil:
  - pasangan join dan frekuensinya;
  - fingerprint agregat yang mengabaikan literal;
  - alias kolom (`AS omzet`);
  - kolom GROUP BY;
  - jumlah pengguna dan peran berbeda.
- **Jejak Studio.**
  - Pertanyaan yang tidak ter-resolve atau dijawab abstain menjadi kandidat sinonim dan metrik yang hilang.
  - Jawaban yang dinilai judge menjadi label.
- **Keputusan steward.** Setiap approve, reject, dan pengajuan ulang tanpa satu kata menjadi label positif atau negatif per klaim.

### 5.2 L1: detektor

Setiap detektor adalah *labeling function*: ia memberi suara benar, salah, atau abstain untuk satu klaim, beserta fiturnya.

| Klaim | Detektor dan fitur |
|---|---|
| A.x mereferensikan B.y (join/FK) | Containment dua arah dari MinHash, keunikan sisi target, kompatibilitas tipe, kemiripan nama dan embedding, frekuensi join di riwayat query, deklarasi Semantic View. Seleksi global dengan arborescence seperti Auto-BI agar graf join konsisten. |
| Kardinalitas | Rasio NDV per sisi: 1:1, N:1, atau M:N |
| Dataset adalah entitas dengan grain K | Keunikan K (NDV = jumlah baris), deklarasi grain, kolom yang menjadi target join |
| Peran (salesman, driver) | Kolom FK dengan nama berbeda ke target yang sama, plus tipe kolom |
| Tipe kolom semantik | Validator pola (email, telepon, kode pos, mata uang), statistik nilai, LLM zero-shot atas kosakata tipe Nova sendiri dari sampel yang diizinkan kebijakan |
| DERIVES dan ekuivalensi | Parse rumus (sudah ada) + clustering fingerprint agregat dari riwayat query |
| Sinonim | Alias SQL, rename BI, co-occurrence di query, kemiripan embedding (`ai_ml/embeddings`), usulan LLM yang lolos validator (sudah ada) |
| Kandidat metrik | Klaster agregat yang sering dipakai banyak pengguna tapi belum ada di Semantic View |
| Definisi bertentangan | Klaster riwayat dengan nama sama tetapi rumus berbeda; usulkan definisi mayoritas seperti Snowflake SVA, dengan bukti sebarannya |
| Drift | Perubahan profil (NDV, null, rentang) dan perubahan makna rumus (sudah ada untuk binding) |

### 5.3 L2: fusi dan kalibrasi

1. **Data label masih sedikit.**
   - Pakai *label model* weak supervision (gaya Snorkel). Model ini mempelajari akurasi dan korelasi tiap detektor tanpa jawaban benar.
   - Label awal datang dari gold set struktural:
     - skema TPC-H dan TPC-DS, yang join-nya diketahui;
     - relasi yang sudah dideklarasikan di Semantic View;
     - keputusan steward yang sudah ada.
2. **Sudah ada sekitar 200–500 label per jenis klaim.**
   - Pakai regresi logistik per jenis klaim (kalibrasi Platt), lalu isotonic di atas 1.000 label.
   - Bobotnya bisa dibaca, jadi bisa dijelaskan kepada steward.
3. **Setiap klaim menyimpan** `p`, versi model, dan daftar bukti dengan kontribusinya. Contoh: "containment 0,98 (+2,1), join 412× di riwayat (+1,4), nama berbeda (−0,3)".
4. **Kalibrasi diukur** setiap kali model dilatih ulang, dengan ECE, Brier, dan diagram reliabilitas.

### 5.4 L3: keputusan otomatis

- **τ_tinggi** dipilih dari data held-out agar presisi stratum yang diterima otomatis ≥ 0,97. Stratum ini diaudit dengan sampel acak yang dinilai judge dan subsampel manusia. Bila presisi turun, τ dinaikkan otomatis.
- **Pita ragu** masuk antrian steward, diurutkan menurut `(ketidakpastian × dampak)`. Dampak dihitung dari pemakaian di query dan Studio serta sentralitas di graf.
- **Yang tidak boleh diputuskan otomatis, berapa pun nilai p-nya:**
  - definisi dan rumus metrik bisnis;
  - EQUIVALENT_TO lintas domain;
  - deprecate dan merge.

  Ini keputusan makna, bukan fakta struktural. Mesin menyiapkan bukti dan usulan, manusia memutuskan.
- **Yang boleh otomatis:**
  - join dan kardinalitas;
  - entitas dan grain;
  - peran;
  - DERIVES yang terbukti dari rumus;
  - sinonim ber-p tinggi yang tidak bentrok;
  - re-pin drift kosmetik (sudah ada).

### 5.5 L5: evaluasi, termasuk LLM-as-a-judge

**Gold set per domain:**

- 150–300 pertanyaan bisnis dengan SQL yang diverifikasi steward;
- 2–4 frasa per maksud;
- sekitar 15% tidak bisa dijawab dan sekitar 10% ambigu;
- versinya mengikuti release.

SQL gold tidak pernah masuk konteks agent. Pertanyaan SerbaData dari uji judge sebelumnya menjadi set pertama.

**Ukuran pada tiap release:**

| Lapisan | Cara |
|---|---|
| Akurasi klaim ontologi | Sampel bertingkat per konsep (Gao et al., VLDB 2019), dilabeli judge dan subsampel manusia. Hasilnya p̂ dengan interval Wilson 95%. |
| Akurasi jawaban Studio | Hasil query dijalankan dengan pengguna dan role yang sama, lalu dibandingkan dengan gold. Toleransinya mengikuti Genie: kolom superset, urutan bebas, 4 angka penting. |
| Keandalan | Skor gaya TrustSQL: +1 untuk jawaban benar dan abstain atau klarifikasi yang tepat; −c untuk jawaban salah dan jawaban atas pertanyaan yang tak bisa dijawab. Harus mengalahkan baseline "abstain semua". |
| Dukungan klaim | Setiap angka di teks jawaban harus bisa dilacak ke baris hasil. Ini diperiksa judge dengan referensi. |

**Protokol judge:**

- Rubrik gaya Prometheus dengan deskripsi per skor dan sub-kriteria biner: entitas benar, metrik benar, filter dan grain waktu benar, klaim didukung.
- Judge menulis alasan dulu, baru vonis.
- Judge dari keluarga model berbeda dari agent. Untuk gerbang release, panel 3 judge (PoLL) dengan suara mayoritas.
- Kesepakatan judge–manusia dilaporkan sebagai Cohen's κ dan harus ≥ 0,8.
- Hemat biaya: judge hanya dipanggil bila eksekusi tidak cocok, cocoknya kebetulan, atau jawaban berisi klaim teks. Vonis di-cache per (pertanyaan, hash SQL, versi data).

**Gerbang release:** release diblokir bila

- ada item gold yang dulu lulus sekarang gagal tanpa waiver;
- akurasi eksekusi turun melebihi interval kepercayaannya;
- skor keandalan turun;
- ECE > 0,05;
- κ judge < 0,8.

### 5.6 L6: penyajian ke agent

Ini fase lanjut yang dibangun di atas L0–L5:

- **Resolusi konsep** memakai graf dan peran. Contoh: "penjualan per sopir" harus ditolak atau diklarifikasi, karena order terhubung ke salesman, bukan driver.
- **Paket konteks graf** untuk planner Studio.
- **Indeks nilai patuh kebijakan** ("FL" → "Florida", "Jaksel" → "Jakarta Selatan"), dengan row filter dan mask berlaku.
- **Abstain** bila pertanyaan di luar cakupan release.

## 6. Machine learning: apa yang dipakai

| Metode | Untuk | Bukti publikasi | Keputusan |
|---|---|---|---|
| Penemuan join statistik (IND, MinHash/LSH Ensemble) + ranker logistik/GBM + seleksi global (Auto-BI) | Graf join, kardinalitas | Auto-BI F1 > 0,9 pada lebih dari 100 ribu model BI nyata; hybrid statistik + LLM presisi 78–100% | **Sekarang**, prioritas tertinggi |
| Weak supervision (label model) + kalibrasi Platt/isotonic + active learning | Confidence dan rute keputusan | Metode standar; kualitasnya bergantung pada gold set | **Sekarang**, tulang punggung |
| Penambangan riwayat query (fingerprint agregat, frekuensi join, alias) | Join, metrik, sinonim, definisi bertentangan | Pola Snowflake SVA; AskData (BIRD 81,95%) | **Sekarang** |
| Embedding teks (sudah ada di `ai_ml/embeddings`) | Sinonim, kemiripan nama | Standar | **Sekarang**, sebagai fitur |
| LLM zero-shot tipe kolom (gaya ArcheType) | Tipe semantik kolom | 85,97 weighted F1 di SOTAB | **Sekarang**, sebagai fitur, atas kosakata Nova |
| Model tipe kolom terlatih (Sherlock, DoDuo) | Tipe semantik | ≈0,89–0,93 F1, tetapi turun tajam di data enterprise | **Nanti**, setelah ada label |
| Entity resolution (Splink/ZeroER, LLM untuk kasus ambigu) | Bukti dua dataset berisi entitas yang sama | ZeroER mendekati supervised; GPT-4 mengalahkan Ditto di 3 dari 5 benchmark | **Nanti** |
| Embedding KG / GNN (TransE, RotatE, R-GCN) | Prediksi relasi | MRR ≈0,33–0,36 di graf besar dan padat | **Tidak.** Graf enterprise terlalu kecil dan jarang, hasilnya tidak bisa dijelaskan, dan statistik struktural lebih baik. |

Semua komponen "sekarang" berjalan di CPU:

- statistik lewat SQL StarRocks;
- sketch di Python (`datasketch`, atau hash di StarRocks);
- ranker dan kalibrasi lewat ML engine Nova yang sudah ada;
- LLM lewat provider yang dikonfigurasi, hanya untuk nama dan penengah.

## 7. Metrik produk

Metrik di tabel ini mengisi halaman utama.

| Kelompok | Metrik | Rumus | Target awal |
|---|---|---|---|
| Akurasi | Akurasi ontologi | p̂ dari sampel bertingkat yang dinilai, interval Wilson 95% | ≥ 95%, lebar setengah interval ≤ 3 poin |
| Akurasi | Akurasi jawaban Studio | Jawaban gold yang hasil eksekusinya cocok ÷ semua yang bisa dijawab | Tren naik; dibandingkan tanpa ontologi |
| Akurasi | Keandalan | Skor TrustSQL | > baseline abstain-semua |
| Kalibrasi | ECE, Brier | 10 bin; mean (p−y)² | ECE ≤ 0,05 |
| Cakupan | Grounding kolom | Kolom yang benar-benar dipakai query dan sudah terpetakan ke konsep resmi ÷ semua kolom yang dipakai, dibobot pemakaian | ≥ 90% |
| Cakupan | Pertanyaan terjawab | Pertanyaan gold dan produksi yang semua konsepnya ada ÷ semua | ≥ 85% |
| Cakupan | Kelengkapan definisi | Skor berbobot: definisi, pemilik, rumus, sinonim, unit | 100% untuk konsep bersertifikat |
| Otomatisasi | Diputuskan mesin | Klaim yang diputuskan tanpa manusia ÷ semua, **selalu ditampilkan bersama presisi stratum otomatis** | Naik selama presisi ≥ 97% |
| Otomatisasi | Beban steward | Item di antrian; median waktu keputusan; menit per 100 klaim | Turun |
| Drift | Basi | Binding atau klaim yang sumbernya berubah setelah validasi terakhir | 0 item bersertifikat basi > 7 hari |
| Drift | Pergeseran akurasi | Δp̂ antar-release dengan uji dua proporsi | Tidak turun signifikan |
| Pemakaian | Jawaban ber-grounding | Jawaban Studio yang memakai konsep resmi ÷ semua jawaban; pengguna aktif mingguan | Naik |
| Judge | Kesepakatan | κ judge–manusia | ≥ 0,8 |

## 8. UI: metrik dulu, tabel di tab lain

Halaman Business Ontology dibuka di tab baru **Overview**, berupa dasbor kesehatan ontologi dengan bahasa visual seperti referensi. Tab yang ada tetap, berganti urutan: Overview · Review · Concepts · Evidence · Releases · Domains.

Angka di sketsa ini hanya ilustrasi tata letak, bukan hasil ukur. Dasbor yang dibangun hanya menampilkan angka yang dihitung. Kartu yang belum punya data menampilkan "belum diukur" beserta langkah untuk mengukurnya.

```text
┌ Ontology accuracy ─────┐ ┌ Answer accuracy ───────┐ ┌ Accuracy per release ───────────────┐
│ 96%  (93–98%)  +1.2    │ │ 88%  Studio, 240 Qs    │ │  ▁▂▃▅▆▇  ont_rel_0001 … 0006        │
│ n=240 · judged 3d ago  │ │ vs 61% without ontology│ │  — ontology  — answers  ┄ gate 95%   │
│ ▁▂▃▄▅▆▇ per release    │ │ ▁▂▄▅▆▇ per release     │ │  hover: release, n, CI, regressions │
│ View sample        ›   │ │ View failed questions ›│ │                                     │
└────────────────────────┘ └────────────────────────┘ └─────────────────────────────────────┘
┌ Decided by Nova ───────┐ ┌ Calibration ───────────┐ ┌ Coverage by domain ─────────────────┐
│ 78%  of 412 claims     │ │ ECE 0.03   Brier 0.06  │ │ Penjualan   ████████▉ 94%           │
│ ████████░░ auto·queue  │ │ reliability ╱ diagram  │ │ Persediaan  ███████▌  81%           │
│ auto precision 98.1%   │ │                        │ │ Operasional ██████    67%  ⚠ 9 gaps │
│ Review 9 uncertain  ›  │ │ View by claim type   › │ │ columns · questions · definitions   │
└────────────────────────┘ └────────────────────────┘ └─────────────────────────────────────┘
┌ Nova activity · 24h ───────────┐ ┌ Evidence mix ─────────────┐ ┌ Drift & health ─────────────┐
│ 37 decisions  2 need review    │ │ heatmap: claim type ×     │ │ 1 stale binding (GrossMargin)│
│ ● Linked route_stops→outlets   │ │ detector (containment,    │ │ 2 profiles shifted (NDV)     │
│   containment 0.99 · auto      │ │ query log, name, LLM)     │ │ 0 meaning regressions        │
│ ● Proposed "jarak tempuh"      │ │ shows where accuracy comes│ │ last profile 2h ago          │
│   p=0.71 · queued              │ │ from                      │ │                              │
│ View all activity          ›   │ └───────────────────────────┘ └──────────────────────────────┘
└────────────────────────────────┘
```

Aturan desain:

- **Setiap angka punya pembanding dan interval kepercayaan**: n, tanggal verifikasi, dan delta terhadap release sebelumnya.
- **Setiap kartu punya satu aksi menuju bukti**: sampel yang dinilai, pertanyaan gagal, atau item di pita ragu.
- **Tidak ada paragraf di Overview.** Penjelasan muncul sebagai tooltip dan di halaman bukti.
- **Grafik mengikuti [DESIGN.md](../../DESIGN.md) dan skill dataviz.** Warna berasal dari token Nova, dengan mode terang dan gelap.
- **Review** berbentuk antrian kartu klaim, bukan dokumen usulan. Tiap kartu menunjukkan klaim, p, bukti terbesar beserta kontribusinya, dan dampaknya. Aksi terima atau tolak ada per klaim, bukan per usulan, sehingga kasus "tolak semua karena satu kata" hilang.
- **Tabel** pindah ke Concepts (daftar dan detail) dan Evidence (klaim dengan filter).

## 9. Rencana fase dan kriteria selesai

Setiap fase diuji di SerbaData dan minimal satu skema umum (TPC-H/TPC-DS) supaya tidak terikat satu domain. Setiap uji dinilai LLM-as-a-judge dengan protokol di §5.5.

| Fase | Isi | Kriteria selesai yang terukur |
|---|---|---|
| A. Fondasi ukur | Gold set SerbaData (≥ 150 pertanyaan) dan gold join TPC-H/TPC-DS; harness evaluasi (eksekusi + panel judge + κ); penyimpanan skor per release; **Overview metrik dulu** dengan angka nyata | Skor akurasi jawaban dan ontologi tampil dengan interval kepercayaan; κ judge–manusia ≥ 0,8 pada 100 item; dasbor tidak memuat angka tiruan |
| B. Bukti data | Profiler kolom (NDV, null, pola, MinHash) di bawah principal berwenang; penambang riwayat query (join, agregat, alias) | Profil seluruh kolom view lab dalam < 5 menit; join TPC-H/TPC-DS dan SerbaData ditemukan dengan F1 ≥ 0,9 terhadap gold |
| C. Fusi terkalibrasi | Label model + classifier per jenis klaim; p dan bukti per klaim; ganti confidence konstan | ECE ≤ 0,05 pada data held-out; diagram reliabilitas tampil |
| D. Keputusan otomatis | τ per jenis klaim, audit sampel, antrian aktif per klaim (Review baru) | ≥ 70% klaim struktural diputuskan mesin dengan presisi terukur ≥ 97%; menit steward per 100 klaim turun ≥ 50% |
| E. Grounding Studio | Resolusi berbasis graf dan peran, paket konteks, indeks nilai patuh kebijakan, abstain | Akurasi jawaban gold naik signifikan (uji dua proporsi) dibanding tanpa ontologi; skor keandalan > baseline abstain |
| F. Belajar terus | Kandidat metrik dan sinonim dari pertanyaan gagal; definisi bertentangan; drift profil; OSI impor/ekspor | Penolakan "selisih jarak per driver" menjadi usulan metrik dalam satu siklus; ekspor OSI bisa dibaca ulang tanpa kehilangan informasi |

Fase A didahulukan karena tanpa alat ukur, klaim "akurat" adalah asumsi lagi.

## 10. Keputusan yang perlu diambil

1. **Principal untuk profiling.**
   - Pilihan yang direkomendasikan: service principal yang diotorisasi eksplisit untuk dataset di balik Semantic View yang terpublikasi, dengan audit dan hanya menyimpan sketch.
   - Alternatifnya owner Semantic View, tetapi profil lalu bergantung pada sesi orang.
2. **Cakupan keputusan otomatis.** Rekomendasinya sesuai §5.4: hanya klaim struktural. Makna metrik tetap diputuskan manusia.
3. **Model judge.**
   - Rekomendasi: panel 3 model dari keluarga berbeda dari agent.
   - On-premise penuh memerlukan judge terbuka (misalnya keluarga Prometheus), dan biayanya perlu dicek.
4. **Penyusun gold set.** Rekomendasi: Nova menyusun draf dari riwayat query dan pertanyaan Studio, judge memeriksa, dan steward hanya memverifikasi sampel.

## 11. Di luar cakupan

- **Aksi dan writeback operasional** (pola Palantir dan Fabric operations agent) tetap di runtime aksi Nova yang terpisah (fase 7 proposal SBO). Framework ini hanya memberi konteks dan bukti.
- **Embedding KG dan GNN**, sampai ada katalog multi-tenant yang cukup besar (lihat §6).
- **Penulisan ulang SQL oleh LLM.** Planner Semantic View tetap satu-satunya penyusun SQL.

## 12. Risiko

- **Gold set yang salah membuat semua metrik menyesatkan.** Audit menemukan 52,8% galat anotasi di BIRD Mini-Dev, jadi gold set juga diaudit sampel.
- **Judge bias** (lenient, posisi, panjang jawaban). Ditangani dengan panel, rubrik biner, κ, dan subsampel manusia.
- **Biaya profiling di tabel besar.** Pakai sampling bertingkat dan sketch inkremental per partisi baru.
- **Overfitting ke SerbaData.** Selalu ada skema umum di setiap kriteria selesai.
- **Kebijakan.** Profil yang melanggar row filter dan mask adalah kebocoran. Uji penerimaan Ranger wajib di Fase B.

## Sumber utama

- [Snowflake Semantic View Autopilot](https://www.snowflake.com/en/blog/semantic-view-autopilot/), [verified query suggestions](https://docs.snowflake.com/en/user-guide/views-semantic/verified-query-suggestions), [Cortex Analyst evaluations](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations)
- [Databricks Genie benchmarks](https://docs.databricks.com/aws/en/genie/benchmarks), [knowledge store](https://docs.databricks.com/en/genie/knowledge-store), [metric views](https://docs.databricks.com/aws/en/business-semantics/metric-views)
- [Microsoft Fabric IQ](https://learn.microsoft.com/en-us/fabric/iq/overview), [Palantir Foundry](https://palantir.com/docs/foundry/platform-overview/overview/), [Open Semantic Interchange](https://www.getdbt.com/blog/the-osi-spec-updates)
- [dbt semantic layer vs text-to-SQL 2026](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026), [data.world KG benchmark](https://arxiv.org/abs/2311.07509), [semantic layer benchmark 2026](https://arxiv.org/abs/2604.25149)
- [Auto-BI](https://arxiv.org/abs/2306.12515), [Scalable Join Inference](https://arxiv.org/abs/2603.04176), [Metanome](https://github.com/HPI-Information-Systems/metanome), [Snorkel](https://github.com/snorkel-team/snorkel), [ArcheType](https://par.nsf.gov/biblio/10540025)
- [Efficient KG Accuracy Evaluation](https://arxiv.org/abs/1907.09657), [calibration (Guo et al.)](https://arxiv.org/abs/1706.04599), [TrustSQL](https://arxiv.org/abs/2403.15879), [FLEX](https://arxiv.org/abs/2409.19014), [Judging the judges](https://arxiv.org/abs/2406.12624), [PoLL](https://arxiv.org/abs/2404.18796), [Prometheus 2](https://arxiv.org/abs/2405.01535)
