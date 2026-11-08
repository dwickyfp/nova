# Hasil: kesepakatan judge gold set (8 November 2026)

Protokol: [judge-agreement-protocol.md](judge-agreement-protocol.md), commit `2219e104`. Protokol itu ditulis sebelum set tantangan dibuat.

Skrip: `tests/benchmark/supernova_ontology/judge_challenge.py`. Rubrik `rubric-4`, model judge yang dikonfigurasi, 15 item per panggilan.

## Ringkasan

| Gold set | n | κ (interval bootstrap 95%) | Mutan tertangkap | Item sah diterima |
|---|---|---|---|---|
| serbadata | 144 | 1,000 (1,000–1,000) | 64/64 | 80/80 |
| serbadata_business | 144 | 0,972 (0,930–1,000) | 65/65 | 77/79 |
| **Gabungan** | **288** | **0,986 (0,965–1,000)** | **129/129** | **157/159** |

**Klaim terbukti** untuk cakupan protokol: batas bawah κ, 0,965, di atas 0,8.

Hasil per jenis mutasi, kedua set digabung:

| Mutasi | Tertangkap |
|---|---|
| period | 26/26 |
| top_n | 6/6 |
| breakdown | 22/22 |
| measure | 75/75 |

## Dua item sah yang ditolak

Keduanya sebelumnya disepakati "yes" oleh judge dan oleh reviewer eksternal.

- **"Berapa jumlah unit dipesan untuk setiap toko pelanggan pada September 2026?"** dengan makna *units sold*. Judge menolak karena "unit dipesan" tidak persis *units sold*. Ini kelompok "units sold" yang sama di analisis kegagalan benchmark. Kedua bacaan bisa dibela.
- **"Can you compare sales by region?"** Judge menjawab *unsure*, karena net revenue, penjualan sebelum retur dan gross revenue sama-sama mungkin. Alasan itu benar, jadi kasus ini lebih tepat diberi label *ambiguous*.

Keduanya juga menunjukkan variansi judge: verdict untuk item yang sama bisa berubah antar-run.

## Batas klaim

- **Yang terbukti:** judge dengan andal membedakan makna yang cocok dari makna yang tidak cocok dengan pertanyaan. Ini syarat utama agar gold set bersih.
- **Yang tidak terbukti oleh set ini:** kasus sulit secara alami, seperti ukuran yang sama di dua view, jalur peran, atau ambiguitas wajar. Di data alami, judge dan reviewer sepakat 95% (gold) dan 97% (klaim). κ alaminya masih belum pasti (gold 0,48–0,86, klaim 0,25–0,91), karena item "no" terlalu sedikit.
- **Pembanding untuk kasus sulit:** pembanding di sini adalah model. Pembuktian yang lebih kuat butuh label manusia pada kasus sulit itu.
- **Judge klaim** belum diuji dengan set seimbang.
