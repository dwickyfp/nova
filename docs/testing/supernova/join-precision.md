# Hasil: presisi keputusan join otomatis (8 November 2026)

Protokol: [join-precision-protocol.md](join-precision-protocol.md), commit `ad9ccd22`. Protokol itu ditulis sebelum data baru diunduh.

## Ringkasan

| Ukuran | Hasil | Target | Status |
|---|---|---|---|
| Presisi terima otomatis (leave-one-database-out) | **114/115 = 99,1%** (95,2–99,8%) | batas bawah ≥ 97% | **Belum cukup data**: tidak terbukti, tidak terbantah |
| Join nyata yang ditolak otomatis | 39/29.070 = 0,13% (0,10–0,18%) | ≤ 1% | Terpenuhi |
| Diputuskan tanpa orang | 29.185/29.392 = 99,3% | — | — |
| Kalibrasi | ECE 0,0005, AUC 0,84 | — | AUC turun dari 0,999, lihat di bawah |

Run `evn_0f3de9ecbea22bba374d1168b471fbf3` mencakup 17 database (3 lab lama dan 14 baru), 156 tabel, 1.322 kolom dan 29.392 kandidat. Kunci jawabannya 159 foreign key berbeda: 150 yang dideklarasikan di sumber, ditambah 9 TPC-H.

## Database baru

Chinook, Sakila, Northwind, dan 11 database BIRD dev:
california_schools, card_games, codebase_community, debit_card_specializing, european_football_2, financial, formula_1, student_club, superhero, thrombosis_prediction dan toxicology.

Semua database dimuat dengan `tests/benchmark/fk_schemas/load.py`. Skema debit_card_specializing mendeklarasikan `yearmonth.CustomerID → customers.CustomerID` dua kali, jadi foreign key yang berbeda berjumlah 150, bukan 151.

## Pelabelan

Ada 34 usulan Nova yang bukan foreign key yang dideklarasikan. Protokol mewajibkan setiap keputusan *terima* berlabel, jadi ke-34 usulan itu direview semuanya, bukan hanya sembilan yang diterima. Inklusi dan keunikannya sudah terukur, jadi yang dinilai adalah maknanya.

- **11 benar**, termasuk `cards.setCode → sets.code` dan `transactions_1k.CustomerID → customers.CustomerID`. Keduanya foreign key nyata yang tidak dideklarasikan pembuat skema.
- **23 salah:**
  - 16 lintas database;
  - 5 id-baris ke id-baris;
  - `Country.id → League.id`, karena id liga kebetulan sama dengan id negara;
  - kode pos tagihan ke kode pos pelanggan di Chinook, yang kebetulan sama.

## Satu-satunya kesalahan terima

**`NOVA_FK_SUPERHERO.superhero.race_id → NOVA_FK_FORMULA_1.races.raceId`.** Nama kolomnya menyebut tabel target ("race"). Semua 61 nilai `race_id` muncul di `raceId`, dan `raceId` unik. Tetapi id 1 di superhero adalah ras "-", sedangkan id 1 di Formula 1 adalah Australian Grand Prix 2009.

## Temuan produk

1. **Kandidat lintas database.**
   - Generator kandidat memasangkan kolom dari database yang berbeda. Akibatnya kandidat bertambah dari 1.065 (3 database) menjadi 29.392 (17 database).
   - Inilah sumber satu-satunya kesalahan terima, dan 16 dari 23 usulan salah.
   - Verifikasi juga menjadi lambat, 43 menit, karena setiap kandidat dicek dengan SQL di jalur yang diatur.
2. **AUC turun dari 0,999 ke 0,84.** 38 dari 39 join nyata yang ditolak ada di kelas "tidak dipilih, kolom tidak menyebut tabel, nama berbeda". Contohnya `Match.home_player_1..11 → Player.player_api_id` di european_football_2. Penolakan ini tetap di bawah 1%, tetapi bukti nama tidak bisa membedakannya.
3. **Batas skala yang sudah diperbaiki:**
   - Menulis 27.800 hasil melewati deadline 60 detik `save_many`. Sekarang deadline-nya 120 detik dan penulisan memakai 500 baris per statement.
   - Run lebih besar dari batas baca 20.000 hasil. Batas itu sekarang 100.000.
4. **Identifier dengan spasi membuat profiling gagal (`ValueError`), dan run tidak menangkapnya.** Loader menulis ulang nama seperti itu supaya kode yang dibekukan tidak berubah.

## Langkah berikutnya

Sesuai protokol, perbaikan tidak boleh dibuktikan pada data ini. Ada dua langkah:

1. **Batasi kandidat ke database yang sama.** Join lintas database adalah kasus federasi yang harus dideklarasikan, bukan ditebak dari nilai.
2. **Ukur ulang pada database yang belum pernah dilihat.** Dengan run ini, butuh sedikitnya 125 keputusan terima tanpa salah, atau sekitar 200 dengan satu kesalahan, pada data baru.
