# Checkpoint Phase A: Fondasi ukur Supernova

Tanggal: 7 November 2026. Desain dan kriteria: [Phase A](../../proposals/supernova-phase-a-measurement.md). Riset: [docs/research/supernova-ontology](../../research/supernova-ontology/README.md).

Semua angka di sini diukur di stack dev (proyek `docker`), release ontology `ont_rel_0005`, oleh `nova_admin` (ACCOUNTADMIN). Interval adalah Wilson 95%, kecuali disebut lain. Ada dua judge:

- **Judge otomatis:** satu model terdaftar, `cx/gpt-6-luna`.
- **Judge kedua:** Claude Opus 5.5, dari keluarga model lain. Claude menilai buta: kasus dan klaim dibaca tanpa melihat vonis model.

## Ringkasan

| Ukuran | Hasil | Catatan |
|---|---|---|
| Akurasi jawaban **dengan** ontology | **80,5%** (72,6–86,5%), n=123 | Run `evn_b681…` |
| Akurasi jawaban **tanpa** ontology | **95,1%** (89,8–97,7%), n=123 | Run `evn_bb54…` |
| Efek ontology (berpasangan) | **−14,6 poin**: 2 pertanyaan diperbaiki, 20 dirusak; McNemar p = 0,00012 | **Ontology saat ini merugikan** |
| Reliabilitas (TrustSQL, −1..1) | 0,59 dengan ontology; 0,71 tanpa | n=157 |
| Jawaban salah dengan yakin | 14,0% (9,4–20,3%) di kedua mode | n=157 |
| Penolakan yang benar (unanswerable dan ambiguous) | 44% (29–61%) dengan; 47% (31–63%) tanpa | n=34 |
| Klarifikasi pada pertanyaan ambigu | 21% (8–48%) dengan; 14% (4–40%) tanpa | n=14 |
| Akurasi klaim ontology (konsensus dua judge) | **94,0%** (89,3–98,8%), stratified, n=166 | Hanya model: 99,2% |
| Kesepakatan judge (κ) pada gold set | 0,35 (setuju 90,1%), n=181 | Target 0,8 **tidak tercapai** |
| Kesepakatan judge (κ) pada klaim | 0,08 (setuju 89,2%), n=166 | Judge model lunak |
| Cakupan metrik derived yang resmi | 58% (45–69%), 34/59 | Live |
| Pertanyaan gold yang ter-resolve ke konsep resmi | 65% | Dari run dengan ontology |
| Usulan Nova yang diterima steward | 75% dari 12 | Live |

**Kesimpulan judge.**

- Alat ukurnya bekerja, dan hasil pertamanya justru membantah klaim yang selama ini diasumsikan: ontology versi sekarang menurunkan akurasi jawaban, signifikan secara statistik.
- Penyebab utamanya bisa ditunjuk per kasus (temuan F1).
- Inilah gunanya Phase A. Tanpa alat ukur, fase berikutnya akan dibangun di atas asumsi yang salah.

## Kriteria selesai

| # | Kriteria | Status | Bukti |
|---|---|---|---|
| 1 | Gold set SerbaData ≥ 150 kasus, sekitar 15% unanswerable, sekitar 10% ambigu, dikonfirmasi dua judge | **Tercapai** | v2: 157 aktif = 123 answerable, 20 unanswerable (12,7%), 14 ambigu (8,9%); 24 ditolak |
| 2 | Gold set domain kedua dengan generator yang sama | **Tercapai** | `business360` v1 dari `nova_business_360` (data Januari–Februari 2026, terdeteksi otomatis): 75 intent, 50 bisa dieksekusi, 61 aktif = 45 answerable, 10 unanswerable, 6 ambigu. Sampel 12 kasus diperiksa Claude: semuanya valid. Run ini menemukan bug generalitas: plan yang ditolak compiler (metrik non-aditif) menggagalkan seluruh run. Sekarang plan itu dibuang dan dihitung. |
| 3 | Run dengan dan tanpa ontology pada release dan gold set yang sama, dengan interval | **Tercapai** | Dua pasang run; pasangan kedua dipakai |
| 4 | Audit klaim ≥ 150, dinilai dua judge, ECE dilaporkan | **Tercapai, dengan catatan** | 166 klaim; ECE tidak informatif (lihat F6) |
| 5 | κ dilaporkan; bila < 0,8, rubrik direvisi sebelum dipakai mengambil keputusan | **Dilaporkan, belum tercapai** | κ 0,35 dan 0,08; revisi rubrik jadi pekerjaan pertama berikutnya |
| 6 | Overview hanya menampilkan ukuran tersimpan; tab Evaluation | **Tercapai** | Tangkapan layar terang, gelap dan 390 px; angka "belum diukur" eksplisit |
| 7 | Semua cek | **Tercapai** | Backend: 8191 unit test lulus (coverage modul baru 77–95%), eval 50/50 skenario, mypy dan ruff bersih. Engine: 6 lulus, termasuk paritas DDL tabel evaluasi; 1 dilewati karena dirancang tidak menyentuh data. Frontend: lint 0 error (25 warning lama di file lain), build, 1447 test lulus. |
| 8 | Checkpoint tercatat | Dokumen ini | |

## Gold set

**v1 (`serbadata` v1, 146 aktif).** Dari 136 intent, judge model menolak 27 kasus answerable. Penyebabnya mayoritas kesalahan alat ukur, bukan kesalahan pertanyaan:

- vonis "no" dengan alasan "cocok";
- katalog untuk judge tanpa grain waktu dan jendela data;
- dua label untuk satu field;
- pertanyaan degeneratif seperti outlet aktif untuk satu outlet.

Semuanya diperbaiki:

- alasan ditulis sebelum vonis;
- katalog lengkap;
- label memakai nama terkualifikasi;
- metrik `COUNT(DISTINCT x)` tidak dipecah per x;
- kasus role mismatch dipindah ke unanswerable.

**v2 (`serbadata` v2).**

- 136 intent, 100% bisa dieksekusi.
- Judge model menerima 167 dari 181. Claude menerima 165 dan menolak 16. Kasus aktif hanya bila semua judge menerima: **157**.
- Penolakan Claude yang tidak ditangkap model:
  - **"received units" tanpa sumber (8 kasus).** Ukuran ini ada di `outlet_stock` dan `delivery_routes` dengan deskripsi identik, jadi pertanyaan yang tidak menyebut sumber sebenarnya ambigu.
  - **"status" tanpa jelas status apa (2 kasus):** status order atau status outlet.
  - **"unit yang terjual" (1 kasus):** cocok untuk unit terjual ke outlet maupun sell-out.
  - **"Ambigu" yang tidak ambigu (4 kasus):** "transaksi" adalah sinonim jumlah order di view; "rata-rata" memilih rata-rata selisih jarak.
  - **"Pelanggan unik" (1 kasus):** di data B2B ini pelanggannya adalah outlet, jadi pertanyaannya bisa dijawab.

**Kesepakatan:**

- Setuju 163/181 (90,1%), κ 0,35, PABAK 0,80.
- κ rendah walau persetujuan tinggi karena hampir semua vonis "yes" (paradoks kappa). Tetap dilaporkan apa adanya, sesuai riset 03.
- Dua kasus mendapat "no verdict returned": judge melewatkan item dalam batch.
- Satu sengketa definisi belum diputuskan rubrik: "per salesman" untuk metrik pengiriman. Model berpendapat bisa difilter `role=Salesman` dengan hasil kosong. Claude berpendapat tidak ada jalur governed.

## Hasil jawaban (gold v2, 157 kasus)

Dua pasang run dijalankan. Pasangan pertama memakai pembanding lama yang salah pada nilai seri (1 kasus top-5 dengan tie). Setelah perbaikan, pasangan kedua menjadi angka resmi.

| Run | Dengan ontology | Tanpa ontology |
|---|---|---|
| Pasangan 1 | 82,1% (101/123) | 94,3% (116/123) |
| Pasangan 2 (resmi) | 80,5% (99/123) | 95,1% (117/123) |

Selisih antar pasangan (±2 kasus) adalah variasi planner LLM. Arah dan besar efeknya stabil.

**Hasil per kasus, dengan ontology:**

| Jenis | correct | wrong | failed | clarified | answered |
|---|---|---|---|---|---|
| Answerable (123) | 99 | 5 | 19 | 0 | – |
| Unanswerable (20) | – | – | 2 | 12 | 6 |
| Ambigu (14) | – | – | 0 | 3 | 11 |

Latensi p50 4,8 detik per pertanyaan (satu panggilan planner dan dua query). Satu run 157 pertanyaan memakan sekitar 10 menit dalam keadaan normal.

## Klaim ontology

**Populasi dan sampel.** Populasinya 545 klaim dalam 8 strata. Sampel 166 klaim, maksimal 25 per stratum, dengan seed tetap.

**Akurasi.**

- Judge model: 99,2% (98,3–100%), dengan 13 "unsure".
- Konsensus (satu "no" dari judge mana pun berarti salah): **94,0% (89,3–98,8%)**.
- Per stratum (konsensus):
  - nama resmi (alias) 88% (70–96%);
  - dimensi 96%, relasi entitas 96%;
  - entitas, metrik, metrik → entitas dan "dibangun dari" 100%.

**Kesalahan yang ditemukan Claude dan dilewatkan model (4 dari 5):**

- Alias **'diskon'** untuk DiscountAmount dan **'retur'** untuk ReturnedUnits terlalu umum. Kata yang sama cocok untuk rata-rata diskon dan nilai retur.
- Alias **'net sales before returns'** menyesatkan, karena "net sales" lazimnya berarti setelah retur.
- Definisi dimensi **Employee name**, "Nama driver. Pakai ini saat menyebut driver", diambil dari satu view, padahal dimensi ini juga dipakai view penjualan untuk salesman.
- Label **"Plan date"** untuk tanggal kirim di route stop items.

**κ model–Claude 0,08.** Model hanya menangkap 1 dari 5 kesalahan, sesuai temuan riset tentang judge yang lunak (Thakur et al. 2024). Karena itu Overview memakai konsensus. Angka model-only tetap disimpan sebagai pembanding.

## Temuan, dengan bukti

**F1. Grounding terlalu ketat.** Ini penyebab utama turunnya akurasi.

- `ground()` mewajibkan setiap konsep metrik yang ter-resolve di pertanyaan dipakai dalam plan. Kata yang hanya menerangkan ikut dihitung:
  - "rata-rata persentase **diskon** per **baris pesanan**" mewajibkan DiscountAmount dan OrderLines;
  - "penjualan kotor sebelum **diskon**" mewajibkan DiscountAmount;
  - "selisih jarak **aktual** dikurangi **rencana**" mewajibkan DistanceTravelled dan PlannedDistanceKm;
  - "permintaan tidak terlayani karena **stok habis**" mewajibkan StockoutDays.
- Akibatnya 19 kasus answerable gagal di run resmi.
- Alias umum dari fase 3 ('diskon', 'retur', 'baris pesanan') memperluas kecocokan. Audit klaim menandai dua di antaranya sebagai salah.
- Arah perbaikan:
  - wajibkan hanya konsep yang diminta sebagai ukuran, bukan yang muncul di dalam frasa definisi metrik lain;
  - turunkan alias umum.

**F2. Ambiguitas tidak terdeteksi.** Hanya 3 dari 14 pertanyaan ambigu yang diklarifikasi. Grounding hanya mengenali nama yang sama di dua domain, sedangkan kata umum ("penjualan", "retur", "jarak rute", "unit dikirim") di-resolve ke satu konsep atau tidak sama sekali, lalu planner memilih sendiri.

**F3. Tidak sadar jendela data.** Pertanyaan untuk April atau Desember 2026 dijawab dengan query periode kosong. Seharusnya dijawab bahwa data tidak tersedia.

**F4. Planner menambah breakdown yang tidak diminta.** Contoh:

- "unit tercatat terkirim **oleh driver**" menjadi per nama karyawan;
- "hari kehabisan stok per bulan" menjadi per outlet dan produk;
- "jumlah rute per kota" mendapat grain harian.

Ini 5 jawaban salah.

**F5. Model data yang ambigu.** "Received units" ada di dua view dengan deskripsi sama. Definisi dimensi bersama diambil dari satu view. Label kolom bertentangan dengan definisinya.

**F6. Kalibrasi belum bisa dinilai.**

- Semua confidence tersimpan berada di pita 0,9–1,0, berupa konstanta per aturan. ECE 0,027 dari satu bin tidak bisa membedakan kalibrasi baik dan buruk, dan Overview menyatakannya.
- Fase C mengganti confidence konstan dengan probabilitas terkalibrasi.

**F7. Judge otomatis tidak cukup.**

- Hanya satu model terdaftar, padahal panel lintas keluarga butuh minimal tiga. Model yang sama juga menjadi planner, jadi ada risiko self-preference.
- Batch kadang melewatkan item.
- κ terhadap Claude rendah.

**F8. Lingkungan.**

- Load host 20–29 pada 8 CPU (renderer VS Code 216% CPU selama 1 hari 18 jam).
- FE dan BE StarRocks OOM-killed (VM 7,8 GiB, FE `-Xmx4g`).
- `superset-worker` 2,9–3,2 GiB dengan proses zombie yang tidak bisa di-restart.
- Sinkronisasi katalog Snowflake `sf_sales` gagal terus karena IP allowlist.
- Runner evaluasi bertahan: langkah bisa dilanjutkan, timeout provider dicoba ulang, dan view yang tidak terbaca membuat langkah menunggu, bukan menulis kasus kosong.

## Perbaikan alat ukur selama Phase A

Setiap perbaikan berikut ditemukan saat pengukuran nyata, punya uji, dan sudah masuk kode:

- satu kasus yang error tidak lagi menggagalkan run;
- timeout provider membuat run tetap berjalan, dengan `resume` untuk run yang gagal;
- panggilan negatives dipecah dua karena satu panggilan melewati batas waktu;
- view yang tidak terbaca menghentikan langkah, tidak menulis kasus kosong;
- plan yang ditolak compiler, misalnya metrik non-aditif yang dipecah per dimensi, dibuang dari gold set, tidak menggagalkan run (ditemukan oleh domain kedua);
- pembanding hasil menerima urutan bebas untuk nilai seri;
- ringkasan klaim memakai konsensus dan dihitung ulang setiap ada review baru;
- tata letak kartu pada lebar 390 px melebihi layar, sudah diperbaiki;
- kartu kalibrasi menyatakan bila hanya ada satu pita confidence.

## Keterbatasan

- **Gold set terlalu mudah bagi baseline.** Pertanyaan dirumuskan dari deskripsi view sendiri, jadi planner tanpa ontology mendapat petunjuk kuat. Efek negatif ontology mungkin dilebih-lebihkan, tetapi arahnya tetap.
- **κ terhadap manusia belum ada.** Belum ada steward yang melabeli sampel; endpoint review (`kind: person`) sudah siap.
- **Domain uji terbatas:** satu domain bisnis nyata (SerbaData) dan satu domain sintetis (`nova_business_360`).
- **Profil data dan jendela data belum dipakai sebagai bukti.** Itu fase B.

## Tindak lanjut F1: grounding yang menjelaskan sebutan (7 November 2026)

**Aturan baru** (`grounding_gaps` di `ontology/grounding.py`, dipakai Studio `semantic_query` dan evaluasi). Konsep yang ter-resolve tetap wajib, kecuali sebutannya dijelaskan oleh metrik yang memang direncanakan:

- metriknya adalah input dari metrik yang direncanakan, dibuktikan pada ekspresi yang di-parse; atau
- kata yang menyebut konsep itu muncul utuh dalam deskripsi, sinonim, nama, atau definisi resmi metrik yang direncanakan.

Substitusi tetap ditolak. "Omzet" dengan rencana penjualan kotor tidak dijelaskan, karena deskripsi penjualan kotor tidak menyebut omzet. Pertanyaan yang meminta dua metrik juga tetap mewajibkan keduanya. Resolver kini mencatat kata yang cocok per konsep.

**Hasil.** Gold v2, release `ont_rel_0005`, run `evn_29ba…`, dibandingkan per kasus:

| Pembanding | Sebelum | Sesudah | Diperbaiki / dirusak | McNemar p |
|---|---|---|---|---|
| Dengan ontology, aturan lama | 80,5% | **93,5%** (87,7–96,7%) | 18 / 2 | **0,0004** |
| Tanpa ontology | 95,1% | 93,5% | 0 / 2 | 0,5 (tidak berbeda) |

Reliabilitas naik dari 0,59 menjadi 0,69. Kasus `failed` turun dari 19 menjadi 2.

**Penilaian judge:**

- **Penjelasan.** Ke-18 penjelasan yang dipakai sah. Setiap sebutan memang menerangkan metrik yang direncanakan ("penjualan kotor sebelum **diskon**") atau menjadi inputnya (jarak aktual → selisih jarak, omzet bersih → margin kotor). Tidak ada substitusi.
- **Regresi.** Dua kasus yang memburuk berasal dari variasi planner, bukan grounding: breakdown karyawan yang tidak diminta, dan klarifikasi untuk nilai "Jawa Barat & DKI".
- **Sisa over-grounding: 1 kasus.** "Per **baris pesanan**" adalah kata breakdown yang menyebut OrderLines. Aturan ini sengaja tidak melepaskannya, karena kata itu tidak ada dalam deskripsi metrik yang direncanakan.

**Status target:**

- Target "dengan ontology ≥ tanpa ontology" belum terbukti, karena selisihnya tidak signifikan. Ontology tidak lagi merugikan, tetapi pada gold set ini juga belum terbukti membantu.
- Gold set ini condong ke baseline: pertanyaannya dirumuskan dari deskripsi view sendiri.
- Bukti nilai tambah ontology butuh pertanyaan dengan kosakata bisnis yang tidak ada di view, misalnya "omzet", "sopir" atau "toko". Itu menjadi bagian revisi gold set berikutnya.

**Kegagalan yang tersisa** (8 dari 123) berasal dari planner dan perumusan gold, bukan grounding:

- breakdown yang tidak diminta, karena "oleh driver" dibaca sebagai pemecahan per karyawan (3 kasus);
- grain harian karena frasa definisi "per hari" (1 kasus);
- nilai literal yang memuat "&" (2 kasus);
- dimensi tidak valid (1 kasus);
- sisa over-grounding (1 kasus).

## Gold set kosakata bisnis (7 November 2026)

**Pertanyaan.** Apakah ontology menambah akurasi ketika pertanyaan memakai kata bisnis yang tidak ada di view? Yang bisa memetakan kata itu ke view hanyalah ontology.

**Desain yang adil** (`GoldRequest.vocabulary = "business"`):

- Kata diambil dari label atau alias konsep di release yang berlaku (`ont_rel_0005`).
- Kata hanya dipakai bila tidak muncul sama sekali dalam nama, deskripsi, atau sinonim view (metrik, dataset, field), dan hanya menamai satu konsep. Contohnya, "omzet", "toko" dan "diskon" sudah ada di view sehingga dikeluarkan.
- Kata entitas ("sopir", "barang", "toko pelanggan") dipakai untuk breakdown per nama entitas.
- Pertanyaan yang tidak memuat kata wajibnya ditolak secara deterministik (`nova:vocabulary`).
- Dua judge menilai seperti biasa.

**Gold set `serbadata_business` v1:**

- 226 kasus; judge model menerima 182.
- Claude menolak 24:
  - alias menyesatkan ("net sales before returns", "retail sales" untuk jumlah unit);
  - "per sales" pada metrik pengiriman;
  - "status" tanpa kejelasan;
  - 2 kasus unanswerable yang ternyata bisa dijawab;
  - 7 kasus "ambigu" yang tidak ambigu.
- Hasilnya 173 aktif: 134 answerable (22 per entitas), 25 unanswerable, 14 ambigu.
- Catatan realisme: model kadang menyisipkan istilah Inggris apa adanya ke dalam kalimat Indonesia ("Berapa number of routes…").

**Hasil** (run `evn_63fb…` dengan ontology, `evn_0e35…` tanpa):

| Kasus | Tanpa ontology | Dengan ontology | McNemar |
|---|---|---|---|
| Semua answerable (134) | 97,8% (93,6–99,2%) | 97,8% (93,6–99,2%) | 1 diperbaiki, 1 dirusak, p = 1,0 |
| Breakdown kata entitas (22) | 90,9% | 95,5% | p = 1,0 |
| Kata metrik saja (112) | 99,1% | 98,2% | p = 1,0 |
| Reliabilitas (173) | 0,77 | 0,79 | |
| Penolakan yang benar (39) | 51% | 59% | |
| Klarifikasi ambigu (14) | 21% | 29% | |

**Penilaian judge:**

- **Alias tidak menambah akurasi yang terukur** bila view sudah diberikan dan modelnya kuat. Planner memahami "penjualan bersih", "untung kotor" atau "sopir" dari deskripsi view dan pengetahuan bahasanya sendiri. Ontology me-resolve 100% pertanyaan, tetapi bantuannya tidak dibutuhkan untuk tugas satu view.
- **Arah penolakan dan klarifikasi** sedikit lebih baik dengan ontology, tetapi tidak signifikan pada n ini.
- **Gold set ini belum menguji nilai ontology yang paling mungkin:** memilih view yang benar di antara beberapa view, karena setiap kasus memberi planner view yang tepat. Juga belum menguji pertanyaan lintas domain atau menjaga definisi resmi ketika deskripsi view berbeda dari makna yang disetujui steward.

**Implikasi.** Klaim "ontology meningkatkan akurasi" tidak didukung data untuk tugas satu view. Pengukuran berikutnya harus menguji:

1. pemilihan view tanpa petunjuk view;
2. konflik definisi, yaitu ketika view mendefinisikan istilah berbeda dari definisi resmi;
3. model yang lebih kecil atau lokal, tempat konteks biasanya paling berpengaruh (arXiv 2604.25149 menemukan efek konteks tidak bergantung model pada model frontier).

## Pemilihan view tanpa petunjuk (8 November 2026)

**Pertanyaan.** Di Studio, agent punya beberapa view dan harus memilih sendiri. Apakah ontology membantu memilih view, lalu menjawab benar?

**Desain** (`AnswerRunRequest.view_hint = false`):

- Kasus tidak memberi view. Semua view yang dipublikasikan menjadi kandidat, termasuk `nova_business_360` sebagai pengecoh.
- **Tanpa ontology:** view dipilih seperti di Studio. Pertama dicari satu view yang nama atau sinonim metriknya disebut di pertanyaan (`_view_naming_metric`). Bila tidak ada, satu panggilan model memilih dari katalog view, menggantikan model agent.
- **Dengan ontology:** view dari binding resmi didahulukan, lalu jalur yang sama.
- Yang diukur: ketepatan view terhadap view kasus gold, dan akurasi jawaban.

**Hasil** (release `ont_rel_0005`, dua judge, kasus berpasangan):

| Gold set | Ukuran | Tanpa ontology | Dengan ontology | McNemar |
|---|---|---|---|---|
| `serbadata` v2, kata view (123) | Jawaban benar | 95,9% | 95,1% | 1 diperbaiki, 2 dirusak, p = 1,0 |
| | View tepat | 99,2% | 99,2% | – |
| `serbadata_business`, kata bisnis (134) | Jawaban benar | 89,6% (83,2–93,7%) | **97,0%** (92,6–98,8%) | **11 diperbaiki, 1 dirusak, p = 0,006** |
| | View tepat | 94,8% | **100%** | **7 vs 0, p = 0,016** |
| | Reliabilitas (173) | 0,64 | 0,69 | |

Run: `evn_b10c…` dan `evn_c428…` untuk `serbadata`; `evn_1628…` dan `evn_f42d…` untuk `serbadata_business`.

**Penilaian judge.** Ke-11 perbaikan sah dan mekanismenya jelas:

- **Rute (6).** Tanpa ontology, model router tidak menemukan view untuk kata bisnis seperti "planned quantity", "untung kotor" atau "baris produk berulang" (5 kasus menjawab "none"). Satu kasus lain salah view: "barang tidak sampai" dikirim ke `outlet_stock`, padahal seharusnya `delivery_routes`. Ontology mengarahkan 100% kasus lewat binding.
- **Pemilihan metrik (5).** View sudah benar, tetapi tanpa ontology planner memilih metrik lain atau meminta klarifikasi ("kunjungan gagal", "nilai pengembalian", "ending inventory per outlet"). Dengan ontology, planner menerima metrik resmi sebagai petunjuk.
- **Satu kasus memburuk** ("5 karyawan dengan persentase retur tertinggi") karena variasi planner.
- Variasi antar run sekitar ±2–4 kasus, sedangkan selisih 11 lawan 1 jauh di atasnya.

**Kesimpulan yang didukung data:**

- **Ontology menambah akurasi ketika dua hal terjadi bersamaan:** pertanyaan memakai kosakata bisnis, dan view harus dipilih sendiri. Kenaikannya +7,4 poin jawaban dan +5,2 poin ketepatan view, signifikan.
- **Bila pertanyaan memakai kata view sendiri, atau view sudah diberikan,** efeknya nol.
- **Nilai ontology ada pada pemetaan bahasa bisnis ke data yang benar.** Ia tidak menambah kecerdasan pada pertanyaan yang sudah memakai kata data.

## Prioritas berikutnya

Prioritas ini diputuskan oleh data:

1. ~~Perbaiki F1~~: **selesai** (lihat tindak lanjut di atas). Tersisa: menurunkan alias umum ('diskon', 'retur') lewat usulan steward, dan gold set dengan kosakata bisnis di luar view untuk membuktikan nilai tambah ontology.
2. **Revisi rubrik judge:**
   - definisi "per role tanpa jalur";
   - sumber ganda;
   - item yang hilang diminta ulang;
   - item sengketa dinilai manusia.

   Target κ ≥ 0,8 pada sampel baru.
3. **F2 dan F3 sebagai bagian Phase E** (grounding): deteksi ambiguitas dari kata umum, dan kesadaran jendela data.
4. **Fase B** (profil data) **dan fase C** (kalibrasi), sesuai rencana framework.


## Tindak lanjut: revisi rubrik judge (8 November 2026)

Rubrik judge direvisi tiga kali (rubric-2 sampai rubric-4).

**Metode.**

- **Pemisahan dev/test.** Item dibagi dua secara deterministik (SHA-256 id, genap/ganjil). Revisi hanya memakai ketidaksepakatan di paruh *dev*. κ akhir dilaporkan dari paruh *test*.
- **Vonis pembanding.** Vonis buta Claude dari Phase A dipakai apa adanya, tidak diubah setelah melihat alasan model.
- **Penilaian ulang tanpa mengubah set.** Run baru `judge` menilai ulang kasus gold tanpa mengubah status kasus. Audit klaim diulang dengan seed yang sama, sehingga itemnya identik.

**Perubahan rubrik**, semuanya di `evaluation/service.py`:

- **Gold:**
  - Kasus answerable sudah dieksekusi, jadi judge hanya menilai apakah pertanyaannya meminta tepat makna itu.
  - Ukuran bernama sama di dua view harus disebut view-nya.
  - "Per peran tanpa jalur governed" berarti unanswerable.
  - Sinonim view menyelesaikan sebuah kata, kecuali ada ukuran lain yang sama wajarnya: 'retur' bisa unit atau nilai, sedangkan 'transaksi' adalah jumlah order.
- **Klaim:**
  - Judge kini diberi *konsep pesaing* yang berbagi kata dengan alias.
  - Label yang menamai hal lain dari definisinya dihitung salah.
  - 'net sales' berarti setelah retur.
  - Frasa sehari-hari dan nama peran boleh.
  - Hanya klaim yang dinyatakan yang dinilai.
- **Alat:**
  - Item yang terlewat ditanyakan ulang sekali (`ask_items`).
  - Potongan bukti metrik kini memuat definisinya. Sebelumnya `allowed_dimensions` mendorong definisi keluar dari batas 900 karakter, sehingga judge menjawab "unsure".
  - κ memakai vonis model terbaru.

| Rubrik | Gold κ (dev / test) | Klaim κ (dev / test) | Klaim κ semua |
|---|---|---|---|
| Phase A | 0,35 (semua) | 0,08 (semua) | 0,08 |
| rubric-2 | 0,59 / 0,38 | 0,17 / 0,34 | 0,26 |
| rubric-3 | 0,84 / 0,47 | 0,58 / 0,42 | 0,51 |
| **rubric-4** | 0,80 / **0,59** | 0,58 / **0,79** | **0,65** |

Paruh test rubric-4, dengan bootstrap 95%:

- **Gold:** κ 0,59 (0,22–0,85), kesepakatan 93,8%, PABAK 0,875, n=96.
- **Klaim:** κ 0,79 (0,0–1,0), kesepakatan 98,6%, PABAK 0,97, n=72.

**Penilaian.**

- **Target κ ≥ 0,8 belum tercapai di gold.** Di klaim, nilainya tepat di ambang, tetapi CI-nya sangat lebar.
- **Penyebab utamanya kelangkaan vonis "no".** Hanya beberapa item yang ditolak, sehingga satu ketidaksepakatan menggeser κ jauh. Inilah paradoks kappa (Feinstein dan Cicchetti 1990), dan PABAK yang tinggi menunjukkannya.
- **Jarak dev–test** (0,80 vs 0,59) menunjukkan revisi berikutnya harus diuji pada sampel baru, bukan pada paruh test ini.
- **Langkah berikutnya:** sampel baru yang diperkaya kasus sulit (stratified, lebih banyak "no"), dinilai steward manusia. Endpoint review `kind: person` sudah ada.

**Efek sampingan yang berguna.** Judge yang lebih ketat menurunkan akurasi klaim versi model dari 99,2% menjadi sekitar 88–91%, dan menemukan sendiri alias 'retur', 'diskon' dan 'net sales before returns', yang di Phase A hanya ditemukan Claude. Ketiganya dipakai siklus belajar Phase F ([checkpoint F](phase-f-checkpoint.md)).
