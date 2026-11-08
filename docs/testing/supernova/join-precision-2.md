# Hasil 2: presisi join otomatis setelah perbaikan lintas database (8 November 2026)

Protokol: [join-precision-protocol-2.md](join-precision-protocol-2.md), commit `d36606f7`. Protokol itu ditulis sebelum database Spider dibuka.

## Ringkasan

| Ukuran | Hasil | Target | Status |
|---|---|---|---|
| Presisi terima, label ketat | **322/327 = 98,5%** (96,5–99,3%) | batas bawah ≥ 97% | **Belum cukup data** |
| Presisi terima, label longgar (lihat di bawah) | 360/365 = 98,6% (96,8–99,4%) | batas bawah ≥ 97% | Belum cukup data |
| Join nyata yang ditolak otomatis | **12/149 = 8,1%** (4,7–13,5%) | ≤ 1% | **Gagal** |
| Diputuskan tanpa orang | 476/3.999 = 11,9% | — | Turun dari 99% di BIRD |
| Kalibrasi per batch | ECE 0,014–0,064, AUC 0,77–0,91 | — | — |

**Kesimpulan:** perbaikan lintas database menghilangkan jenis kesalahan yang dulu satu-satunya. Presisi terima tetap ±98,5%, tetapi batas bawahnya masih di bawah 97%. Pada Spider, aturan tolak otomatis gagal untuk foreign key yang namanya berupa peran, dan sebagian besar kandidat masuk antrian review.

## Data

Sumbernya `test_database` Spider (206 database). 183 database dipakai dan 23 dikeluarkan sesuai aturan protokol:

- 3 di atas 20 MB;
- 20 yang separuh nama tabelnya sama dengan database yang sudah diukur.

Kunci jawabannya **791 foreign key** yang dideklarasikan. Run dibagi dalam 10 batch berisi 20 database, dengan total 3.999 kandidat berlabel. Batch terakhir hanya 3 database dan terlalu sedikit labelnya untuk kalibrasi, jadi tidak membuat keputusan.

**Batas aturan pengeluaran.** `store_1` adalah Chinook dengan nama tabel jamak (`albums`, bukan `Album`). Aturan nama tabel tidak menangkapnya, jadi database ini ikut. Semua usulannya di sana id-baris ke id-baris dan tidak diterima.

## Pelabelan

Ada 81 usulan yang bukan foreign key yang dideklarasikan. Semuanya direview, bukan hanya yang diterima. Inklusi dan keunikannya sudah terukur; yang dinilai maknanya, dengan DDL sumber dibaca untuk kasus yang ragu.

- **33 benar.** Contohnya `Invoices.Order_Item_ID → Order_Items` dan `departments.LOCATION_ID → locations`, yang nyata tetapi tidak dideklarasikan atau dideklarasikan lewat kunci komposit.
- **48 salah.** Kebanyakan id-baris ke id-baris. Ada juga tiga yang menyesatkan namanya: `Order_ID` di `Performers_in_Bookings`, `Invoice_Items` dan `Bookings_Services` ternyata merujuk `Bookings.Booking_ID`, bukan `Customer_Orders`.
- **Label ketat dan longgar.** Empat join menuju tabel 1:1 atau tabel penghubung yang memakai kunci yang sama: `store_district`, `school_details` (2) dan `membership_register_branch`. Keempatnya diberi label **salah** di bacaan ketat, karena kolomnya merujuk entitas utama (`store`, `school`, `member`). Bacaan longgar menghitung keempatnya benar. Mengubah label mengubah kalibrasi, jadi bacaan longgar dihitung ulang penuh, bukan sekadar menambah empat.

## Kesalahan terima, bacaan ketat (5)

- **Tiga kolom `Order_ID` di `cre_Drama_Workshop_Groups` yang diterima menuju `Customer_Orders`.** Pada DDL sumber, ketiganya merujuk `Bookings.Booking_ID`. Namanya menyesatkan, nilainya kebetulan cocok, dan kelas buktinya ("dipilih, menyebut tabel, nama sama") yang paling kuat.
- **`Staff_in_Processes.document_id → Documents_Processes.document_id`.** Targetnya tabel penghubung yang kebetulan unik pada data ini.
- **`Restaurant_Type.ResTypeID → Type_Of_Restaurant.ResTypeID`.** Arahnya terbalik.

Empat join ke tabel 1:1 tidak lagi diterima setelah label lengkap masuk kalibrasi.

## Penolakan yang ternyata nyata (12)

Semuanya foreign key yang **namanya berupa peran, tanpa akhiran id**:

- `planet_1`: `Shipment.Planet → Planet.PlanetID`, `Package.Sender` dan `Package.Recipient → Client.AccountNumber`, `Shipment.Manager → Employee.EmployeeID`, dan lainnya (7);
- `sports_competition`: `Club_ID_1` dan `Club_ID_2 → club.Club_ID`;
- `small_bank_1`: `CHECKING.custid` dan `SAVINGS.custid → ACCOUNTS.custid`;
- `store_1`: `customers.support_rep_id → employees.id`.

Kelas buktinya "tidak dipilih aturan, kolom tidak menyebut tabel, nama berbeda". Probabilitas kelas itu 0,005–0,0098, tepat di bawah ambang tolak 0,01. Kolom `Planet` sebenarnya menyebut tabel `Planet`, dan `Club_ID_1` menyebut `club`. Aturan `names_table` tidak mengenali nama tabel tanpa akhiran id atau dengan nomor peran.

## Langkah berikutnya

1. **Perbaiki `names_table`.** Kolom yang sama dengan nama tabel (`Planet`), atau nama tabel ditambah nomor peran (`Club_ID_1`), harus dihitung menyebut tabel itu.
2. **Pertimbangkan batas bawah jumlah label untuk keputusan tolak.** Dengan sedikit database per batch, satu skema dengan gaya penamaan berbeda sudah cukup untuk menggeser kelas.
3. **Ukur ulang pada data yang belum dilihat.** Sesuai protokol, perbaikan tidak dibuktikan pada Spider.

**Catatan eksploratif, bukan bukti.** Menggabungkan hasil ini dengan protokol 1 (114/115) memberi 436/442 = 98,6%, dengan batas bawah ±0,971. Gabungan ini tidak ditetapkan sebelumnya, dan kedua run memakai kode yang berbeda, jadi tidak dipakai sebagai klaim.
