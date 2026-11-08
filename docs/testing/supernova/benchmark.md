# Benchmark ontology Supernova (8 November 2026)

Angka mentah ada di [benchmark-raw.md](benchmark-raw.md). File itu dihasilkan ulang dari run evaluasi yang tersimpan, tanpa memanggil model:

```bash
cd backend
NOVA_BENCH_PASSWORD=... UV_NO_SYNC=1 uv run python -m tests.benchmark.supernova_ontology.report \
  > ../docs/testing/supernova/benchmark-raw.md
```

Dokumen ini menjelaskan metode, membaca hasilnya, dan menyebut apa yang belum terbukti.

## Metode

- **Gold set.** `serbadata` memakai 157 kasus berkosakata view. `serbadata_business` memakai 172 kasus berkosakata bisnis yang tidak dipakai view. Keduanya terdiri dari kasus *answerable*, *ambiguous* dan *unanswerable*, berstatus ACTIVE, dan dinilai rubrik judge v4.
- **Mode.** Setiap gold set dijalankan dengan dan tanpa ontology (release `ont_rel_0007`). Agent tidak diberi petunjuk view. Hasil dipasangkan per kasus dan diuji dengan McNemar.
- **Ukuran.** Jawaban benar bila barisnya sama dengan hasil gold plan. Ketepatan memakai interval Wilson 95%. Reliabilitas memakai skor TrustSQL: jawaban salah diberi penalti, menolak atau bertanya balik bernilai nol.
- **Judge.** Verdict model dibandingkan dengan verdict reviewer eksternal yang buta (`claude-opus-5.5`). Pembagian dev/test ditentukan dari SHA-256 id item. κ dilaporkan pada separuh yang tidak dipakai untuk menyetel rubrik.
- **Join.** Profil, sketch KMV dan uji inklusi dijalankan dengan sesi dan role pemanggil. Uji acceptance Ranger membuktikan profil hanya melihat baris yang boleh dilihat pemanggil.

## Hasil

| Area | Hasil | Bacaan |
|---|---|---|
| Jawaban, berkosakata view | 120/123 (93,1–99,2%) dengan ontology, 122/123 tanpa | Tidak berbeda bermakna (McNemar p = 0,5) |
| Jawaban, berkosakata bisnis | 133/134 (95,9–99,9%) dengan ontology, 132/134 tanpa | Tidak berbeda bermakna (p = 1) |
| Reliabilitas | 0,885 dan 0,901 dengan ontology; 0,879 dan 0,901 tanpa | Sama atau lebih baik |
| Bertanya balik pada kasus ambigu | 50–57% dengan ontology, 43–50% tanpa | Naik, tetapi intervalnya lebar (n = 14) |
| Klaim (ketepatan konsensus) | 90,6% (85,0–96,2%), n = 166 | — |
| Penemuan join | Presisi 50/50, recall 50/50, ECE 0,015, AUC 0,999 | 94,9% diputuskan tanpa orang; 47/47 diterima benar; 0/964 ditolak padahal nyata |

## Analisis kegagalan jawaban

**Gold set berkosakata view** pada run konfirmasi `evn_49693c20…` gagal di tiga kasus:

- **Dua klarifikasi.** Jawaban tanpa ontology untuk kedua kasus ini benar. Kasusnya adalah "penjualan setelah diskon sebelum retur per status outlet" dan "stok penutup Oktober untuk Jawa Barat & DKI". Agent bertanya balik alih-alih menjawab. Ini gagal yang aman, karena tidak ada angka salah. Pada run sebelumnya, empat kasus serupa juga bertanya balik dan kemudian benar di run konfirmasi. Pola ini variansi planner, bukan cacat ontology yang sistematis.
- **Satu jawaban salah, di kedua mode.** Kasusnya "units of Kopi Hitam Sachet 25g sold". "Units sold" bisa berarti unit ke outlet atau ke konsumen. Ini termasuk kelompok "units sold" yang dicatat di checkpoint fase E.

**Kasus sistematis yang diperbaiki.** Di run pertama, "per baris pesanan" ditolak karena mention itu tidak dijelaskan definisi metrik. Grounding sekarang mengenali mention sesudah "per" sebagai satuan metrik yang direncanakan, asalkan definisi metrik menyebut "per" dengan kata yang sama. Run konfirmasi menjawabnya benar dengan penjelasan *'per baris pesanan' is the unit of planned metric avg_discount_pct*. Dua kasus negatif di unit test menjaga agar aturan ini tidak meloloskan dimensi biasa.

**Kesimpulan jujur.** Pada model dan gold set ini, ketepatan tanpa ontology sudah 98–99%. Ontology tidak menurunkan ketepatan secara bermakna, dan menaikkan pertanyaan balik pada kasus ambigu. Nilai utamanya ada di tempat lain: kosakata bisnis, pemilihan view, klaim yang tercatat, dan join yang ditemukan dari data (lihat checkpoint fase A–F). Benchmark ini tidak membuktikan ontology menaikkan ketepatan jawaban di atas baseline yang sudah tinggi.

## Kualitas judge

| Judge | Kesepakatan | κ, semua item (interval 95%) | κ separuh test | κ separuh dev |
|---|---|---|---|---|
| Klaim | 97,0% (n = 166) | 0,65 (0,25–0,91) | 0,79 (n = 72) | 0,58 |
| Gold set, rubrik v4 | 95,0% (n = 181) | 0,70 (0,48–0,86) | 0,59 (n = 96) | 0,80 |

Kedua judge sepakat dengan reviewer eksternal pada 95–97% item. Tetapi sekitar 94% item berlabel "yes", sehingga κ ditentukan oleh segelintir item "no". Interval bootstrap 95%-nya lebar: gold 0,48–0,86, klaim 0,25–0,91. Arah selisih dev/test juga berlawanan antara klaim dan gold, jadi selisih itu derau sampel, bukan bukti rubrik overfit. **κ ≥ 0,8 belum terbukti, dan juga tidak terbantah, dengan data alami ini.** Pengujian dengan set seimbang ada di [judge-agreement.md](judge-agreement.md).

Angka ketepatan jawaban di atas tidak bergantung pada judge. Hasil eksekusi agent dibandingkan baris demi baris dengan hasil gold plan yang dijalankan sebagai pemanggil yang sama. Judge hanya menentukan label kasus (answerable, ambiguous, unanswerable) dan menilai klaim.

## Yang belum terbukti

- **κ judge ≥ 0,8 pada kasus sulit alami.** Pada set tantangan seimbang, κ judge gold set adalah 0,986 (0,965–1,000), dan semua 129 mutan tertangkap ([judge-agreement.md](judge-agreement.md)). Klaim untuk deteksi makna yang salah sudah terbukti. Untuk kasus sulit alami, intervalnya masih lebar. Judge klaim belum diuji dengan set seimbang.
- **Presisi keputusan otomatis ≥ 97%.** Pada 14 database baru dan 3 database lab, presisinya 114/115 = 99,1% (95,2–99,8%). Hasil ini belum cukup data. Satu-satunya kesalahan adalah join lintas database. Lihat [join-precision.md](join-precision.md).
- **Belajar dari percakapan Studio.** Siklus belajar sekarang memakai hasil evaluasi dan riwayat query, belum percakapan Studio.
