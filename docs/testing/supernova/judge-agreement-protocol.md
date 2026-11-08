# Protokol: kesepakatan judge gold set (κ ≥ 0,8)

Ditulis 8 November 2026, sebelum set tantangan dibuat atau dijalankan. Hasilnya dicatat di [judge-agreement.md](judge-agreement.md).

## Kenapa desain lama tidak bisa membuktikan apa pun

Judge dan reviewer eksternal sepakat pada 95–97% item. Tetapi sekitar 94% item berlabel "yes", sehingga κ ditentukan oleh beberapa item "no" saja. Akibatnya interval bootstrap 95% sangat lebar:

| Judge | n | κ | Interval 95% |
|---|---|---|---|
| Gold, semua | 181 | 0,70 | 0,48–0,86 |
| Gold, separuh test | 96 | 0,59 | 0,22–0,85 |
| Klaim, semua | 166 | 0,65 | 0,25–0,91 |
| Klaim, separuh test | 72 | 0,79 | 0–1,0 |

Data ini tidak membuktikan κ ≥ 0,8, dan juga tidak membuktikan κ < 0,8. Pembuktian butuh set yang seimbang.

## Klaim yang diuji

Dengan rubrik dan model yang sekarang, judge gold set membedakan item yang sah dari item yang maknanya tidak sesuai pertanyaan, dengan κ ≥ 0,8.

- **Terbukti:** batas bawah interval bootstrap 95% κ ≥ 0,8 (2.000 resample, seed 7).
- **Dibantah:** batas atasnya < 0,8.
- **Di antaranya:** belum cukup data.

## Set tantangan

**Item positif, label "yes".** Kasus ACTIVE dari gold set `serbadata` dan `serbadata_business` yang:
- berada di separuh **test** (SHA-256 id, sama seperti laporan); rubrik disetel di separuh dev;
- semua review sebelumnya, model maupun eksternal, bernilai "yes".

**Item negatif, label "no" menurut konstruksi.** Setiap kasus *answerable* positif mendapat tepat satu mutan. Pertanyaannya tetap, tetapi rencananya diubah, lalu maknanya ditulis ulang dengan format renderer gold set. Jenis mutasi dipilih bergiliran secara deterministik dari yang berlaku untuk kasus itu:

- **period:** periode diganti dengan periode lain dari jendela data;
- **top_n:** N diganti (5 → 3);
- **breakdown:** dimensi diganti dengan dimensi lain yang boleh untuk metrik itu;
- **measure:** metrik diganti dengan metrik lain di view yang sama, dengan kata yang berbeda.

Pertanyaan kemudian tidak lagi menanyakan makna itu, sehingga jawaban yang benar adalah "no". Label ini tidak bergantung pada verdict saya.

## Pengukuran

- **Judge dipanggil per 15 item (`JUDGE_BATCH`), sama seperti run judge.** Setiap panggilan memakai rubrik `rubric-4` (`GOLD_RUBRIC`), katalog view yang sama, dan jendela data dari run gold terakhir, sama seperti run judge. Urutan item diacak dengan seed tetap sebelum dibagi ke batch, sehingga mutan tidak selalu satu batch dengan aslinya.
- **Verdict "unsure" dihitung sebagai tidak sepakat.**
- **Laporan:**
  - κ dengan interval bootstrap;
  - sensitivitas untuk "no" (mutan yang tertangkap);
  - spesifisitas (item sah yang diterima);
  - hasil per jenis mutasi.

## Batas klaim

Set ini menguji deteksi **makna yang tidak cocok dengan pertanyaan**. Set ini tidak menguji kasus sulit secara alami, seperti ukuran yang sama di dua view, jalur peran, atau ambiguitas wajar. Untuk kasus seperti itu, κ alami di atas tetap berlaku, dan pembuktiannya butuh label manusia.

## Yang tidak boleh dilakukan

- Mengubah rubrik setelah melihat hasil, lalu mengukur ulang pada set ini.
- Membuang item atau jenis mutasi setelah melihat hasil.
