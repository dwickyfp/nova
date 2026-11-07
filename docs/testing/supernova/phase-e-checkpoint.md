# Checkpoint Phase E: grounding Studio — ambiguitas dan jendela data

Tanggal: 8 November 2026. Lanjutan dari [checkpoint A](phase-a-checkpoint.md) (temuan F1–F8). Riset: [04-ambiguity-and-coverage](../../research/supernova-ontology/04-ambiguity-and-coverage.md).

Semua angka diukur di stack dev (proyek `docker`), release `ont_rel_0005`, oleh `nova_admin`.

- **Gold set:** serbadata v2 (157 kasus aktif) dan serbadata_business v1 (173 kasus aktif).
- **Mode run:** tanpa petunjuk view (`view_hint: false`), seperti agent Studio yang memegang beberapa view. Setiap gold set dijalankan dengan dan tanpa ontology.
- **Interval:** Wilson 95%.
- **Uji perbandingan:** McNemar eksak, berpasangan per kasus.

## Yang diubah

| Temuan | Perubahan | Kode |
|---|---|---|
| **F3** — Periode di luar data dijawab dengan hasil kosong | Bila hasil berwaktu kosong, atau kolom pembandingnya kosong, hari pertama dan terakhir metrik dibaca lewat dua plan governed sebagai pemanggil. Periode yang seluruhnya di luar jendela menjadi `OUTSIDE_DATA_WINDOW` dengan rentang yang tersedia. Periode yang belum dimulai ditolak sebelum ada query. | `agents/semantic/coverage.py`, `semantic_query.py` |
| **F2** — Kata ukuran samar dijawab dengan tebakan | Planner mencatat kata ukuran yang cocok untuk beberapa metrik sebagai *unresolved* beserta kandidatnya. Alias resmi menang atas kesamaran. | `model_planner.py` |
| **F2** — Rencana kosong menjadi error | Rencana yang tidak menamai apa pun menjadi klarifikasi. Rencana yang hanya berisi konsep unresolved yang material sah, karena tidak pernah dieksekusi. | `model_planner.py`, `planning.py` |
| Skor | Router yang menjawab "tidak ada view yang bisa" dicatat sebagai `abstained`, tidak lagi `failed`. Run lama dinilai ulang dengan aturan yang sama sebelum dibandingkan. | `evaluation/service.py` |

Aturan leksikal untuk kesamaran juga diuji offline pada 285 pertanyaan gold, lalu **ditolak**. Konfigurasi terbaiknya hanya menangkap 7 dari 28 pertanyaan ambigu, tetapi meminta klarifikasi pada 25 dari 257 pertanyaan yang jelas. Rinciannya di catatan riset 04.

## Hasil final (sebelum → sesudah, berpasangan)

Run lama ada di [checkpoint A, bagian "Pemilihan view tanpa petunjuk"](phase-a-checkpoint.md). Run final:

| Gold set dan mode | Run lama → run final |
|---|---|
| serbadata_business, dengan ontology | `evn_f42d…` → `evn_9c42…` |
| serbadata_business, tanpa ontology | `evn_1628…` → `evn_8095…` |
| serbadata, dengan ontology | `evn_c428…` → `evn_6a18…` |
| serbadata, tanpa ontology | `evn_b10c…` → `evn_3787…` |

### Gabungan empat run

| Ukuran | Sebelum | Sesudah | McNemar |
|---|---|---|---|
| Pertanyaan ambigu diklarifikasi | 11/56 = 19,6% (11,3–31,8%) | **30/56 = 53,6%** (40,7–66,0%) | 19 bertambah, 0 hilang, **p = 3,8×10⁻⁶** |
| Pertanyaan unanswerable ditolak atau diklarifikasi | 72/90 = 80,0% (70,6–87,0%) | **84/90 = 93,3%** (86,2–96,9%) | 13 bertambah, 1 hilang, **p = 0,0018** |
| Periode di luar data ditolak | 5/18 | **18/18** | |
| Pertanyaan answerable benar | 485/514 = 94,4% (92,0–96,0%) | 486/514 = 94,6% (92,2–96,2%) | 9 bertambah, 8 hilang, p = 1,0 (tidak berubah) |
| Pertanyaan jelas yang diminta klarifikasi | 7 | 6 | |

### Per run

| Gold set dan mode | Answerable | Ambigu | Unanswerable | Reliabilitas (TrustSQL) |
|---|---|---|---|---|
| business, dengan ontology | 130 → 131 /134 | 3 → 7 /14 | 20 → 23 /25 | 0,775 → **0,861** |
| business, tanpa ontology | 120 → 125 /134 | 3 → 9 /14 | 21 → 23 /25 | 0,728 → **0,855** |
| serbadata, dengan ontology | 117 → 114 /123 | 3 → 7 /14 | 15 → 19 /20 | 0,732 → **0,815** |
| serbadata, tanpa ontology | 118 → 116 /123 | 2 → 7 /14 | 16 → 19 /20 | 0,739 → **0,815** |

Run antara (`evn_c955…`, `evn_218f…`, `evn_82b1…`, `evn_96de…`) dijalankan sebelum guard rencana-kosong dipasang. Hasilnya searah: ambigu 30/56, unanswerable 81/90. Run itu juga menemukan tiga rencana kosong yang kemudian diperbaiki.

## Penilaian judge (Claude) per kasus yang berubah

**Benar:**

- **Periode di luar data.**
  - Desember 2026 ditolak dengan "periode belum dimulai (hari ini 8 November)".
  - Januari, Februari dan April 2026 ditolak dengan jendela data 1 Mei – 4 November 2026.
- **Kata samar.** Kandidat yang ditawarkan sesuai gold. Contohnya:
  - "sales" → `net_revenue, sales_before_returns, gross_revenue`;
  - "returns" → `returned_units` atau `returned_value`.

**Regresi pada pertanyaan answerable, menurut penyebab:**

- **Tiga karena router.** Pengganti model agent menjawab "tidak ada view" untuk pertanyaan stop yang terlewat dan jarak rencana. Ini variasi pemilihan view, bukan F2 atau F3.
- **Dua karena breakdown yang tidak diminta (F4).** Contohnya 616 baris untuk "unit terjual ke konsumen per bulan". Masalah ini lama dan muncul di run sebelumnya juga.
- **Dua klarifikasi baru di mode tanpa ontology.**
  - "jumlah unit dipesan" adalah kosakata bisnis yang tidak ada di view.
  - "route distance variance" bisa berarti dua metrik jarak.
  - Tanpa ontology, keduanya memang samar. Dengan ontology, aliasnya menyelesaikan kedua kata itu: klarifikasi berlebih di set bisnis turun ke 0. **Ini manfaat ontology yang terukur.**
- **Satu karena dimensi "status outlet".** Katalog memang punya dua dimensi bernama status.

**Kesimpulan judge.**

- F2 dan F3 menaikkan perilaku yang benar secara signifikan, tanpa menurunkan akurasi jawaban.
- F3 tuntas: 18/18 periode di luar data ditolak.
- F2 baru sekitar separuh: 30/56 diklarifikasi. Sisanya menjawab dengan satu metrik, terutama lewat alias generik seperti 'retur' → Returned units dan 'diskon' → Discount amount, yang memang menjadi keputusan bisnis selama alias itu ada di release. Alias ini sudah ditandai menyesatkan oleh audit klaim, dan perbaikannya ada di jalur steward (fase F), bukan di planner.

## Batasan dan langkah berikutnya

1. **F4: breakdown yang tidak diminta.** Ini sekarang sumber kesalahan answerable terbesar yang bukan routing.
2. **Alias generik.** Usulan steward untuk mengganti 'retur', 'diskon' dan 'omzet' dengan alias spesifik, atau menandai bahwa kata itu perlu ditanyakan. Ini masuk fase F (belajar dari definisi yang bertentangan).
3. **Router.** Pengganti model agent sesekali menyatakan tidak ada view. Studio memakai model agent sendiri, jadi angka ini batas bawah.
4. **Rubrik judge** belum direvisi (κ < 0,8, checkpoint A).


## Tindak lanjut: F4, nilai filter dan router (8 November 2026)

Dari 28 jawaban answerable yang salah di run final, ada empat pola. Masing-masing diperbaiki di pemilik yang ada:

| Pola | Contoh | Perbaikan |
|---|---|---|
| **F4.** Breakdown yang tidak diminta (8) | "untuk Salesman" ditambah dimensi nama karyawan; "hari outlet-produk" ditambah dimensi outlet dan produk | Aturan planner: dimensi hanya untuk *per / setiap / by*. Kata yang menerangkan ukuran atau nilai filter bukan pengelompokan |
| Nilai filter dipecah (5) | "Jawa Barat & DKI" menjadi `IN ('Jawa Barat','DKI')` | Nilai yang tidak dikenal dicari di data sebagai pemanggil (`literals.field_values`, row filter berlaku). Nilai yang disebut utuh di pertanyaan menggantikan tafsiran model |
| Nilai di field yang salah | `employee_name = 'Salesman'` | Bila field tidak memuat nilainya dan tepat satu dimensi lain memuatnya persis, filter dipindah ke dimensi itu (maksimal 8 pencarian) |
| Router pengganti "tidak ada view" (5) | "jarak rencana per peran" | Katalog router kini memuat breakdown setiap view |

**Hasil berpasangan** terhadap run final Phase E, empat run tanpa petunjuk view (`evn_1f71…`, `evn_1e9e…`, `evn_8064…`, `evn_e9a7…`):

| Ukuran | Sebelum | Sesudah | McNemar |
|---|---|---|---|
| Answerable benar (gabungan) | 486/514 = 94,6% | **505/514 = 98,2%** | 24 bertambah, 5 hilang, **p ≈ 0,0007** |
| Unanswerable ditolak | 84/90 | 86/90 | |
| Ambigu diklarifikasi | 30/56 | 28/56 | dalam variasi |
| Reliabilitas (TrustSQL) | 0,815–0,861 | **0,873–0,892** | naik di keempat run |

**Sisa kegagalan, 9 dari 514:**

- "units sold" (4), yang bisa berarti ke outlet atau ke konsumen. Rubrik judge v4 menilai kasus seperti ini memang ambigu.
- "item order count" dan "staf lapangan" (3), yaitu kosakata dan dimensi.
- "per baris pesanan" (1), mention yang tidak dijelaskan definisi.
- Dimensi bare `plan_id` yang tidak dikenali (1).

Perluasan aturan F1 untuk "baris pesanan" ditolak, karena hanya menambal satu kasus.
