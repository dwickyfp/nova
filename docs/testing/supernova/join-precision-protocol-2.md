# Protokol 2: presisi join otomatis setelah perbaikan lintas database

Ditulis 8 November 2026, sebelum database Spider diekstrak atau dilihat isinya. Hasilnya dicatat di [join-precision-2.md](join-precision-2.md).

Ini lanjutan dari [protokol 1](join-precision-protocol.md). Di sana presisi terima 114/115, dan satu-satunya kesalahan adalah join lintas database.

## Yang berubah sejak protokol 1

- **Kandidat join hanya dibentuk di dalam satu database.** Perubahan ada di `evidence/joins.candidates`.
- **Nama yang tidak bisa dikutip Nova dilewati dan dilaporkan.** Run tidak lagi gagal karenanya.

Ambang (`AUTO_PRECISION = 0,97`, `AUTO_MISS = 0,01`), kelas bukti, aturan pemilihan dan profiling tidak berubah. Semua dibekukan di commit yang memuat protokol ini.

## Klaim yang diuji

Sama seperti protokol 1: join yang diterima Nova sendiri benar setidaknya 97% dari waktu, pada database yang labelnya tidak pernah dilihat model.

- **Terbukti:** batas bawah Wilson 95% ≥ 0,97.
- **Dibantah:** batas atasnya < 0,97.
- **Di antaranya:** belum cukup data.

## Data baru: Spider

**Sumber.** `spider_data.zip` dari Google Drive resmi Spider (Yale LILY), SHA-256 `00636695dabed6b5f4b8328a16b13e069a2f16591d5efcce57660669c85b121b`, folder `test_database` (206 database).

**Yang dikeluarkan.** Aturan ini ditetapkan sebelum isi data dilihat:

1. **Database yang file SQLite-nya di atas 20 MB:** `wta_1`, `soccer_1`, `baseball_1`. Disk Docker hanya punya ±1,2 GB di atas batas 5 GB FE. `soccer_1` juga database yang sama dengan european_football BIRD.
2. **Database yang sudah pernah diukur.** Yaitu database yang sedikitnya separuh nama tabelnya, dinormalisasi ke huruf kecil tanpa `_`, sama dengan salah satu dari 17 database protokol 1.

Database tanpa foreign key yang dideklarasikan **tidak** dikeluarkan. Database seperti itu tetap bisa menghasilkan keputusan terima yang salah.

**Kunci jawaban.** Setiap foreign key satu kolom yang dideklarasikan (`PRAGMA foreign_key_list`), dimuat dengan `tests/benchmark/fk_schemas/load.py` yang sama. Database dimuat sebagai `NOVA_FK_SP_<nama>`.

## Pengukuran

1. **Run dalam batch berisi 20 database**, batas yang sama yang berlaku untuk pengguna. Urutannya alfabetis. Probabilitas tiap keputusan dihitung leave-one-database-out di dalam batch-nya sendiri.
2. **Setiap usulan yang bukan foreign key yang dideklarasikan direview,** bukan hanya yang diterima. Inklusi dan keunikan sudah terukur; yang dinilai maknanya. Verdict dicatat sebagai review eksternal.
3. **Presisi terima digabung atas semua batch.** Penolakan yang ternyata join nyata, cakupan otomatis, ECE dan AUC dilaporkan per batch dan gabungan.

## Yang tidak boleh dilakukan

- Mengubah ambang, kelas bukti, aturan pemilihan, atau aturan pengeluaran setelah melihat hasil.
- Membuang database, batch atau keputusan setelah melihat hasil.
