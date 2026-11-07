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

## Berikutnya

1. Skema umum (TPC-H berbentuk lab) untuk membuktikan generalitas, sebagai database ketiga untuk validasi silang.
2. Menyambungkan join yang diterima ke Semantic View sebagai usulan relasi bagi pemilik view (Phase D, langkah aksi).
3. Penambangan riwayat query sebagai detektor kedua (Phase B, sisa).
