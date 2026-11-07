# Checkpoint Phase F: belajar terus

Tanggal: 8 November 2026. Kriteria dari [framework](../../proposals/supernova-ontology-framework.md) §9: penolakan menjadi usulan metrik dalam satu siklus, dan ekspor OSI bisa dibaca ulang tanpa kehilangan informasi. Ditambah definisi bertentangan (alias menyesatkan), kosakata dari pertanyaan gagal, dan drift.

Semua dijalankan live di stack dev oleh `nova_admin`. Steward diperankan `retail_steward_test` (izin uji dev). Judge: Claude Opus 5.5.

## Siklus belajar (`POST /ontology/learn`)

Satu panggilan menghasilkan usulan `ENGINE`, dan tidak ada yang berubah sebelum steward menyetujui dan release terbit. Ada tiga detektor:

| Detektor | Sumber bukti | Aturan |
|---|---|---|
| **Nama menyesatkan** | Audit klaim | Alias dihapus bila **semua** judge yang meninjaunya (minimal dua) mengatakan "no", dengan alasan dikutip |
| **Kata yang dipakai orang** | Pertanyaan gold yang gagal | Istilah ukuran yang tercatat di gold menjadi alias metrik yang dimaksud, bila lolos validator enrichment (tidak dipakai konsep lain, tidak generik) |
| **Metrik yang diminta penolakan** | Pertanyaan yang ditolak karena tidak ada ukuran | Model mengusulkan rumus dari ukuran dan kolom view, atau menyatakan tidak bisa. Rumus ditambahkan ke salinan view, divalidasi, dikompilasi, lalu **dieksekusi sekali sebagai pemanggil** dan harus menghasilkan nilai. Baru setelah itu menjadi usulan konsep metrik, dengan rumus untuk pemilik view |

### Putaran pertama, dinilai steward: gerbang manusia menangkap kesalahan

| Usulan | Penilaian steward | Penyebab dan perbaikan |
|---|---|---|
| Kosakata: 'per route' → Avg distance gap km; 'status outlet' → Sales before returns | **Ditolak.** Keduanya pengelompokan, bukan nama ukuran | Detektor mengambil teks *unresolved* planner. Sekarang hanya istilah ukuran yang tercatat di gold |
| Hapus 'jumlah unit terkirim'; paket berisi 'baris pesanan' | **Ditolak.** Hanya judge model yang menolak, judge kedua membela | Penghapusan nama resmi kini butuh kesepakatan semua judge |
| Hapus 'diskon', 'retur', 'net sales before returns' | Benar, tetapi terbungkus bersama yang salah | Diajukan ulang sendiri |

Detektor metrik di putaran pertama tidak menghasilkan apa pun, karena bug `ask_items`: jawaban yang tidak memuat `verdict` terbuang. Setelah diperbaiki, model masih menolak "harga jual rata-rata per kota" karena relasi view tidak disertakan di katalognya. Katalog kini memuat relasi.

### Putaran akhir

| Usulan | Isi | Penilaian judge dan steward |
|---|---|---|
| `prp_fb98…` | Hapus 'retur' (Returned units), 'diskon' (Discount amount), 'net sales before returns' (Sales before returns), disepakati kedua judge | **Disetujui** |
| `prp_dabe…` | Metrik baru dari penolakan "Berapa harga jual rata-rata per unit untuk setiap kota?": `SUM(net_amount) / NULLIF(SUM(quantity), 0)`, tervalidasi dan tereksekusi (4.845,61) | **Disetujui** |
| 33 penolakan lain | Biaya, emisi, komisi, timestamp proses, pemasok, umur pelanggan, alasan gagal: tidak bisa dihitung dari data | Benar ditolak. "Hari persediaan" bisa diperdebatkan (butuh periode acuan), jadi menolaknya konservatif |

**Kriteria "penolakan menjadi usulan metrik dalam satu siklus" tercapai.**

- Dari 34 pertanyaan yang ditolak, 1 diusulkan dan usulan itu benar (presisi 1/1).
- Recall terhadap penilaian judge 1/2 ("hari persediaan" tidak diusulkan).

Release **`ont_rel_0006`** terbit dengan perubahan ini. Efeknya pada jawaban ada di bagian berikut.

## Efek release baru pada jawaban

Ini dua run berpasangan dengan ontologi, tanpa petunjuk view. Kodenya sama, yang berbeda hanya release-nya: `ont_rel_0005` (`evn_8064…`, `evn_1f71…`) lawan `ont_rel_0006` (`evn_c833…`, `evn_d44f…`).

| Ukuran (gabungan dua gold set) | 0005 → 0006 | McNemar |
|---|---|---|
| Answerable benar | 252 → 251 / 257 | 1 bertambah, 2 hilang, p = 1,0 |
| Ambigu diklarifikasi | 14 → 15 / 28 | p = 1,0 |
| Unanswerable ditolak | 42 → 43 / 45 | |

**Efek yang ditargetkan terjadi.** Kedua pertanyaan yang memakai 'retur', yang sebelumnya dijawab dengan Returned units lewat alias, kini ditanya balik:

- "Berapa jumlah retur per bulan?"
- "Tampilkan total retur per merek."

Perubahan lain, seperti "penjualan bulan lalu" dan satu pertanyaan top-5 karyawan, adalah variasi planner pada kasus yang tidak tersentuh perubahan ontologi. Akurasi jawaban tidak berubah secara signifikan. Release ini memperbaiki ketepatan kapan Studio bertanya, tanpa biaya akurasi.

## Drift data

Setiap run join membandingkan profilnya dengan run join sebelumnya dalam konteks yang sama (`evidence/discovery.py`, `profile_drift`). Ada empat aturan:

- kolom kunci tidak lagi unik;
- null rate naik ≥ 5 poin;
- nilai unik bergeser ≥ 30% tanpa pertumbuhan baris;
- join yang dulu diterima kehilangan integritas atau hilang.

**Uji live.** Ke TPC-H disisipkan 1.500 order yang menunjuk customer yang tidak ada, dan 150 customer dengan kunci ganda dan telepon kosong:

| Temuan (`evn_2e03…` dibanding `evn_97b2…`) | Sebelum → sesudah |
|---|---|
| `customer.c_custkey` tidak lagi unik | 1,0 → 0,909 |
| Null rate `customer.c_phone` naik | 0 → 9,1% |
| `orders.o_custkey → customer` kehilangan integritas | 1,0 → tidak ditemukan |

- **Versi pertama aturan** juga melaporkan atribut yang kebetulan unik (`c_name`, `o_totalprice`). Aturan kunci kini hanya untuk kolom kunci, dan versi akhir tepat tiga temuan.
- **Data dipulihkan** setelah uji. Overview menampilkan jumlah drift di Health.

## Pertukaran OSI (`GET /ontology/osi/export`, `POST /ontology/osi/import`)

**Bentuk ekspor.** Release diekspor sebagai dokumen Ossie 0.1.1, satu per Semantic View yang terikat:

- metrik yang terikat membawa definisi resmi sebagai `description`, alias konsep di `synonyms`, dan konsepnya di `ai_context`;
- `ai_context.nova_ontology` memuat seluruh release yang boleh dilihat pemanggil.

**Uji live `ont_rel_0005`:**

- 3 dokumen OSI, masing-masing lolos parser Ossie.
- Saat dibaca ulang: 3 domain, 42 konsep, 58 relasi dan 34 binding.
- **Manifest hash hasil hitung ulang sama persis** dengan hash release (`ba8b047a…`): tidak ada informasi yang hilang.

**Uji unit membuktikan dua hal tambahan:**

- satu definisi yang diubah terdeteksi (`lossless: false`);
- pengguna tanpa akses domain tidak mengekspor isinya, dan ekspornya ditandai `complete: false`.

**Kriteria tercapai.**

## Batasan

- **Studio tidak menyimpan teks pertanyaan yang gagal.** Detektor belajar memakai run evaluasi pemanggil sebagai sumber yang terukur. Belajar langsung dari percakapan Studio butuh persetujuan per percakapan, dan belum dibangun.
- **Metrik baru baru sampai di konsep ontologi.** Pemilik view masih harus menambahkan metrik ke Semantic View dan mengikatnya lewat usulan Autopilot. Sampai itu terjadi, Studio menjawab pertanyaan itu dengan `NO_AUTHORIZED_REPRESENTATION`, bukan dengan metrik yang mirip.
