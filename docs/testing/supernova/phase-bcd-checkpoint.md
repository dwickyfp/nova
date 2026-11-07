# Checkpoint Phase B, C, D: bukti data, kalibrasi, keputusan otomatis

Tanggal: 8 November 2026. Desain: [framework Supernova](../../proposals/supernova-ontology-framework.md) §5.1–5.4 dan §9. Riset: [02-ml-methods](../../research/supernova-ontology/02-ml-methods.md) dan [05-join-evidence](../../research/supernova-ontology/05-join-evidence.md).

Semua angka diukur di stack dev (proyek `docker`) oleh `nova_admin` (ACCOUNTADMIN):

- **Sumber data:** dua database lab, `NOVA_RETAIL_FIELD` (SerbaData) dan `NOVA_INTELLIGENCE_BENCH` (business 360).
- **Cakupan:** 33 tabel dan 227 kolom.
- **Run final:** `evn_d99f601992cfc978555c55d405394a05`.
- **Interval:** Wilson 95%.
- **Judge:** Claude Opus 5.5 menilai usulan join yang tidak dideklarasikan view. Bukti yang dipakai: inklusi yang diverifikasi tepat, nama kolom, dan isi view.

## Ringkasan

| Ukuran | Hasil | Target | Status |
|---|---|---|---|
| Waktu profil seluruh kolom view lab | 6 s profil + 38 s discovery = **44 s** untuk 227 kolom | < 5 menit | Tercapai |
| Join: presisi terhadap relasi yang dideklarasikan view | 34/41 = 82,9% (68,7–91,5%) | | Ketujuh "salah" ternyata FK nyata |
| Join: recall terhadap relasi fisik yang dideklarasikan | **34/34 = 100%** (89,8–100%) | | |
| Join: setelah 7 usulan yang tidak dideklarasikan dinilai judge | presisi **41/41**, recall **41/41**, F1 1,00 (CI bawah 91,4%) | F1 ≥ 0,9 | Tercapai, dengan catatan generalitas di bawah |
| Kalibrasi probabilitas join (leave-one-database-out) | **ECE 0,033**, Brier 0,017, AUC 0,998, n=583 | ECE ≤ 0,05 | Tercapai |
| Kandidat yang diputuskan mesin tanpa manusia | **89,0%** (86,2–91,3%), 519/583 | ≥ 70% | Tercapai |
| Presisi yang diterima otomatis | 13/13 = 100% (77,2–100%) | ≥ 97% | Benar semua, tapi n terlalu kecil untuk membuktikan 97% |
| Yang ditolak otomatis ternyata join nyata | 0/506 (0–0,75%) | ≤ 1% | Tercapai |
| Antrian review | 39 pasangan | | Diurutkan: review, diterima, ditolak |
| Kalibrasi klaim lapisan derived (confidence konstan) | ECE 0,027, Brier 0,022, n=141 | ECE ≤ 0,05 | Sudah terkalibrasi, tidak diubah |

## Phase B: bukti dari data

### Yang dibangun

- **Profil kolom sebagai pemanggil.** Satu scan per tabel menghitung `approx_count_distinct` (HLL), jumlah non-null, dan panjang nilai. Query dijalankan lewat `query_service` dengan sesi dan role aktif orang yang memulai, sehingga row filter dan mask Ranger berlaku persis seperti di query lain.
- **Sketch KMV** (bottom-256 dari `murmur_hash3_32`) dipakai untuk menyaring pasangan kolom. Sketch hanya ada di memori dan tidak pernah disimpan.
- **Verifikasi tepat untuk kandidat** (filter-then-verify):
  - inklusi dihitung dengan `COUNT(DISTINCT)` dan `LEFT JOIN`;
  - keunikan kunci dihitung dengan `COUNT(DISTINCT)`.
- **Aturan keputusan** (`evidence/joins.py`). Setiap aturan berasal dari kesalahan yang terukur di run pertama:

  | Kesalahan di run pertama | Aturan |
  |---|---|
  | `city` unik di tabel 4 baris dijadikan target | Target harus identifier tabelnya: kolom bernama kunci (`*_id`, `*_code`), atau satu-satunya kolom unik |
  | `fulfillment_events.event_id` dijadikan sumber | Kolom yang mengidentifikasi tabelnya sendiri (kata kepala nama tabel) bukan referensi |
  | — | `customer_id` di `customer_health_events` tetap referensi, karena "customer" hanya pengubah |
  | `refunds.order_id` memilih `payment_attempts` karena noise KMV | Kolom yang menamai tabel target menang |
  | `campaigns` dan `incidents` (1 baris) terlewat | Dimensi satu baris boleh, asal namanya menyebut tabel |
  | — | Peran tanpa bukti nama (`driver_id` → `employees`) hanya untuk kunci teks dengan inklusi penuh |

- **Satu target per kolom sumber**, seperti arborescence Auto-BI.
- **Run evaluasi jenis `joins`** di bawah `/ontology/evaluation`, yang berjalan bertahap.
  - Hasilnya, termasuk nama tabel dan kolom, hanya tampil untuk principal dan role yang sama dengan yang menjalankan.
  - Admin lain tidak melihat run itu di daftar, dan mendapat 404 bila mencoba membuka hasilnya.

### Perjalanan angka

| Run | Presisi (deklarasi) | Recall | F1 | Perubahan |
|---|---|---|---|---|
| `evn_a577…` | 26/47 | 26/35 | 0,63 | Aturan awal, estimasi sketch dan HLL saja |
| `evn_22f8…` | 33/40 | 33/34 | 0,89 | Verifikasi tepat, identifier, dimensi satu baris |
| `evn_d333…` / `evn_d99f…` | 34/41 | 34/34 | 0,91 | Kata kepala nama tabel; gold dibatasi ke kolom fisik |

`route_stops.stop_driver_id` dikeluarkan dari gold karena hanya ada di view (field turunan), bukan kolom fisik. Ini dicatat di `not_physical` pada run.

### Penilaian judge atas 7 usulan yang tidak dideklarasikan

Semua tujuh dinilai **benar**, dengan bukti data:

- `campaign_exposures.session_id → web_sessions`: inklusi 100%, 1.000 dari 6.000 sesi.
- `employees`, `outlets` dan `route_plans.depot_id → depots`: inklusi 100%, dan namanya menyebut tabel.
- `route_stop_items.outlet_id → outlets`: inklusi 100%.
- `route_stop_items.plan_id → route_plans`: inklusi 100%, 1.931 dari 1.932 rencana.
- `route_stops.driver_id → employees`: inklusi 100%, 12 driver adalah karyawan. Ini peran yang tidak punya bukti nama, dan view rute memakainya lewat field turunan.

Artinya, view yang terbit tidak mendeklarasikan sebagian join nyata. Discovery menemukan joinnya, dan judge mengonfirmasi.

### Batasan yang jujur

- Hanya dua skema, keduanya dibuat oleh tim yang sama, dengan konvensi `*_id` yang bersih. Proposal meminta minimal satu skema umum (TPC-H/TPC-DS). Stack ini tidak memilikinya, jadi **generalitas belum terbukti**. Itu pekerjaan berikutnya.
- Recall hanya bisa dihitung atas join yang dideklarasikan atau dikonfirmasi. Join nyata yang tidak diusulkan dan tidak dideklarasikan tidak terlihat.
- Penambangan riwayat query (USAGE_QUERY_STATS) belum dibangun. Dengan F1 1,00 dari data saja, sinyal ini belum diperlukan untuk dua skema ini.

## Phase C: kalibrasi

### Klaim lapisan derived

Klaim lapisan derived memakai confidence konstan per aturan: 0,9 untuk dataset, 0,95 untuk relasi, dan seterusnya. Hasil pengukuran pada audit klaim (`evn_37cf…`, 141 klaim berlabel konsensus) **ECE 0,027, Brier 0,022**: sudah di bawah target. Alternatifnya, presisi per stratum leave-one-out, justru lebih buruk (ECE 0,044). Konstanta itu tidak diubah. Mengganti angka yang sudah jujur dengan model hanya akan menambah varians.

### Probabilitas join

Model kalibrasi ini baru. Tiga model dibandingkan dengan leave-one-database-out pada 583 kandidat berlabel:

| Model | AUC | ECE | Diterima otomatis (≥ 0,97) | Ditolak keliru (≤ 0,01) |
|---|---|---|---|---|
| Regresi logistik, 13 fitur | 0,942 | 0,105 | — | — |
| Regresi logistik, 7 fitur | 0,946 | 0,035 | 9/15 benar | 3/373 |
| **Kelas bukti, posterior Jeffreys** | **0,994** | **0,020** | **13/13 benar** | **0/506** |

Pada regresi, fitur kontinu seperti jumlah nilai unik dan ukuran tabel tidak berpindah antar database. Model yang dilatih di RETAIL memberi kandidat negatif di INTELLIGENCE probabilitas ~1. Kelas bukti tidak punya masalah itu. Kelasnya terdiri dari tiga hal:

- dipilih aturan atau tidak;
- kolom menamai tabel target atau tidak;
- nama sama atau tidak.

Probabilitasnya adalah presisi terukur kelas itu (histogram binning, Zadrozny dan Elkan 2001). Setiap kandidat menyimpan probabilitas, keputusan, dan penjelasan seperti "38 dari 38 yang seperti ini benar".

Masalah label juga ditemukan dan diperbaiki. Kandidat **arah terbalik** dari relasi yang dideklarasikan sebelumnya ikut berlabel benar. Sekarang hanya arah yang dideklarasikan yang benar.

## Phase D: keputusan otomatis

- **Ambang.** Karena probabilitasnya terkalibrasi, ambangnya adalah targetnya sendiri: terima sendiri pada ≥ 0,97, tolak sendiri pada ≤ 0,01, sisanya masuk antrian review.
- **Ambang adaptif ditolak.** Ambang yang dipilih dari database lain (cross-fitted) gagal total. RETAIL mengurutkan kandidatnya dengan sempurna, sehingga τ menjadi 4×10⁻⁵ dan menerima 375 pasangan salah di INTELLIGENCE.
- **Cara presisi dinilai.** Keputusan dinilai memakai probabilitas held-out, yaitu yang akan diputuskan mesin pada database yang labelnya tidak pernah ia lihat.
- **Hasil:**
  - 89% kandidat diputuskan mesin.
  - Yang diterima sendiri 13/13 benar, yang ditolak sendiri 0/506 keliru.
  - Antrian review berisi 39 pasangan.
- **Kelas "dipilih aturan, menamai tabel" (38/38 benar) belum diterima otomatis di semua database.** Saat dinilai dengan label satu database saja, posteriornya baru sekitar 0,955. Ini disengaja: mesin baru boleh memutuskan sendiri setelah bukti labelnya cukup.

## Keamanan, diuji live

- **Steward ritel (`retail_steward_test`, role `retail_steward`) ditolak di semua titik** dengan 403 "ACCOUNTADMIN must be the active role". Titik yang diuji:
  - memulai discovery pada database lain;
  - memulai discovery pada database default;
  - membaca daftar run;
  - membaca hasil run admin.
- **Uji unit membuktikan identitas yang dipakai.** Setiap query profil, sketch dan verifikasi berjalan dengan `username` dan role pemanggil. Admin lain tidak melihat run itu (404 untuk hasilnya, tidak muncul di daftar dan Overview).
- **Belum diuji:** penerimaan dengan *row filter* Ranger pada ACCOUNTADMIN yang dibatasi. Stack dev tidak punya principal seperti itu.
- **Uji engine:** `test_business_ontology_metadata.py` menghasilkan 6 lolos dan 1 dilewati by design. Query baru (profil, sketch KMV, verifikasi inklusi, plan jendela data) dijalankan live di FE terpatch lewat run di atas.

## UI

Overview mendapat baris baru "Joins found in the data", "Decided by Nova", dan "Is the join probability honest?". Isinya:

- presisi dan recall dengan interval;
- bagian kandidat yang diputuskan mesin;
- diagram reliabilitas.

Tab Evaluation mendapat tombol **Find joins** dan tabel kandidat. Defaultnya menampilkan join yang diusulkan, dengan filter "Needs review", "Accepted by Nova", "Rejected by Nova", dan "Every pair".

## Tindak lanjut: sisa B dan D dituntaskan (8 November 2026)

### Generalitas pada skema umum (TPC-H)

- **Database lab.** `NOVA_TPCH_LAB` dibuat dengan bentuk TPC-H: 8 tabel, sekitar SF 0,01, 59.864 lineitem, dan 9 FK satu kolom sebagai gold. Generatornya deterministik, di `tests/benchmark/tpch_lab/seed.py`.
- **Baseline jujur.** Aturan Phase B sebelumnya mendapat **F1 0,00**. Aturan itu overfit pada konvensi `*_id`.
- **Generalisasi aturan nama.** Empat konvensi TPC-H kini dikenali:
  - kata kunci yang menempel (`custkey` = `cust` + `key`);
  - prefiks tabel pendek (`o_`, `ps_`);
  - singkatan (`cust` → `customer`, `supp` → `supplier`);
  - nama yang sama setelah prefiks dibuang (`o_custkey` = `c_custkey`).
- **Hasil di TPC-H:** **9/9 FK ditemukan, tanpa positif palsu.**
- **Hasil tiga database** (`evn_cbb2…`, lalu `evn_e145…` setelah data dipulihkan; 285 kolom, sekitar 2,7 menit):

  | Ukuran | Hasil |
  |---|---|
  | Terhadap deklarasi | presisi 43/50, recall 43/43 |
  | Setelah review judge | presisi dan recall **50/50** (CI bawah 0,929) |
  | Kalibrasi kelas bukti, leave-one-database-out pada 3 database | **ECE 0,015**, Brier 0,0075, AUC 0,999, n=1.065 |
  | Keputusan otomatis | **94,9%** diputuskan mesin (93,4–96,1%) |
  | Diterima otomatis | 47/47 benar (CI bawah 0,924) |
  | Ditolak otomatis | 0/964 keliru (CI atas 0,4%) |

  Presisi ≥ 97% butuh sekitar 120 penerimaan tanpa salah untuk terbukti, jadi itu belum.

### Uji penerimaan row filter Ranger

Uji ini ada di `tests/integration/test_ontology_evidence_ranger.py` (opt-in `NOVA_ONTOLOGY_EVIDENCE_RANGER_ACCEPTANCE=1`), dijalankan di FE terpatch dev, dan **lolos**.

- **Penyiapan:** dua principal dengan satu role. Row filter Ranger memperlihatkan Jakarta kepada satu principal dan Bandung kepada yang lain.
- **Hasil:** profil, sketch dan verifikasi inklusi masing-masing hanya mencerminkan baris kotanya (60 lawan 30 baris, 10 lawan 5 outlet). Sketch kedua principal **tidak berbagi satu nilai pun**.
- **Pembersihan:** user, role, database dan policy dihapus. Sisa objek terverifikasi 0.

### Penambangan riwayat query

- **Analisis join** (`sql_frontend/analysis/joins.py`) membaca kesamaan kolom dari parse tree SQL Nova sendiri (JOIN ON dan WHERE implisit), tanpa parser baru.
- **Sumber:** keluarga query Query Autopilot. Hanya keluarga milik principal, role dan versi konteks yang sama yang dihitung, sesuai aturan cohort Autopilot.
- **Hasil:** 1.789 keluarga milik pemanggil memuat 6 pasangan join, dengan **presisi 6/6** dan recall 6/43.
  - Riwayat di stack ini tipis. Detektor ini melengkapi bukti data (`history_families` per kandidat) dan dilaporkan terpisah.

### Phase D: aksi ke pemilik Semantic View

Discovery Semantic View (`POST /semantic-views/autopilot/discover`) sebelumnya hanya berhipotesis dari kesamaan nama kunci primer. Kini discovery memakai run join pemanggil sendiri:

- relasi yang diterima menjadi kandidat `DATA_EVIDENCE`, lengkap dengan probabilitas dan buktinya;
- relasi yang perlu review menjadi `DATA_REVIEW`.

Pemilik view tetap mengusulkan, mempreview dan menyetujui lewat alur Autopilot yang ada.

Diukur live pada 8 tabel TPC-H:

| Cara | Relasi benar yang ditemukan |
|---|---|
| Hipotesis nama (lama) | **0 dari 9** |
| Bukti data | **9 dari 9**, semuanya diterima otomatis |

### Drift (dipakai Phase F)

Setiap run join membandingkan profilnya dengan run sebelumnya dalam konteks yang sama. Lihat [checkpoint F](phase-f-checkpoint.md).
