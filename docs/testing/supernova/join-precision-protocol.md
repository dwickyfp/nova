# Protokol: presisi keputusan join otomatis ≥ 97%

Ditulis 8 November 2026, sebelum data baru diunduh atau dilihat. Hasilnya dicatat di [join-precision.md](join-precision.md).

## Klaim yang diuji

Join yang diterima Nova sendiri (`decision = accept`, probabilitas ≥ 0,97) benar setidaknya 97% dari waktu, pada database yang labelnya tidak pernah dilihat model.

Klaim dianggap **terbukti** bila batas bawah interval Wilson 95% presisi terima ≥ 0,97. Klaim dianggap **dibantah** bila batas atasnya < 0,97. Di antara keduanya, hasilnya **belum cukup data** dan dilaporkan apa adanya.

## Yang dibekukan

- **Ambang** `AUTO_PRECISION = 0,97` dan `AUTO_MISS = 0,01`, model kelas bukti, aturan pemilihan kandidat, profiling dan sketch: kode di commit `14d65710`. Tidak ada yang diubah selama protokol ini berjalan.
- **Probabilitas leave-one-database-out.** Setiap keputusan dinilai dengan probabilitas yang dihitung hanya dari label database lain, seperti di `calibration.cross_validated`.

## Data baru

Semua database berikut berasal dari luar Nova, dibuat oleh perancang lain, dan foreign key-nya dideklarasikan di skema sumbernya:

- Chinook (lerocha/chinook-database v1.4.5, SQLite)
- Sakila (bradleygrant/sakila-sqlite3, SQLite)
- Northwind (jpwhite3/northwind-SQLite3, SQLite)
- 11 database BIRD dev (bird-bench `dev.zip`, SQLite)

Ketentuan data:

- **Kunci jawaban** adalah setiap foreign key satu kolom yang dideklarasikan (`PRAGMA foreign_key_list`), dari kolom anak ke kolom induk. Foreign key komposit tidak dihitung.
- **Pemuatan.** Setiap database dimuat ke StarRocks sebagai `NOVA_FK_<nama>`, dengan nama tabel dan kolom apa adanya. Tipe dipetakan ke BIGINT, DOUBLE, DATETIME atau VARCHAR.
- **Sampling.** Hanya untuk tabel di atas 200.000 baris yang tidak menjadi target foreign key mana pun. Tabel seperti itu dicuplik deterministik menjadi 200.000 baris. Cuplikan anak tetap terkandung dalam induknya, jadi inklusi tidak rusak. Tabel induk tidak pernah dicuplik.

## Pengukuran

1. Jalankan satu run join (`POST /ontology/evaluation/joins`) atas tiga database lab lama dan semua database baru, dengan kunci jawaban di atas sebagai `gold`.
2. **Setiap keputusan `accept` harus berlabel.** Join yang diterima tetapi tidak ada di kunci jawaban tidak dianggap salah otomatis. Join itu diperiksa dengan SQL: inklusi nilai anak di induk, keunikan induk, dan makna kolom. Verdictnya dicatat sebagai review. Foreign key yang tidak dideklarasikan pembuat skema bisa saja benar.
3. **Laporan:** presisi terima, miss tolak, cakupan otomatis, ECE dan AUC. Dilaporkan untuk semua database dan juga **hanya untuk database baru**. Yang menentukan klaim adalah angka database baru.

## Yang tidak boleh dilakukan

- Mengubah ambang, kelas bukti atau aturan pemilihan setelah melihat hasil, lalu mengukur ulang pada data yang sama.
- Membuang database atau kasus setelah melihat hasil.

Kalau sebuah perbaikan diperlukan, perbaikan itu dibuktikan pada database lain yang belum dilihat, bukan pada data ini.
