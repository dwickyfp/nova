# Hasil: ontology belajar dari percakapan Studio (8 November 2026)

Protokol: [conversation-learning-protocol.md](conversation-learning-protocol.md), commit `6cb876bc`. Frasa uji ada di [conversation-phrases.json](conversation-phrases.json). Skripnya `tests/benchmark/supernova_ontology/conversation_learning.py`.

## Ringkasan

| Kelompok | Sebelum (`ont_rel_0007`) | Sesudah (`ont_rel_0008`) | Uji berpasangan |
|---|---|---|---|
| **Semua pertanyaan uji** (69) | 55/69 = 79,7% (68,8–87,5%) | **66/69 = 95,7%** (88,0–98,5%) | 13 diperbaiki, 2 rusak, McNemar **p = 0,007** |
| Metrik yang dipelajari (58) | 46/58 = 79,3% | **58/58 = 100%** (93,8–100%) | 12 diperbaiki, 0 rusak, **p = 0,0005** |
| Metrik yang tidak dipelajari, sebagai kontrol (11) | 9/11 | 8/11 | p = 1 |
| Regresi `serbadata_business` (134) | 133/134 | 132/134 | 0 diperbaiki, 1 rusak, p = 1 |

**Klaim terbukti untuk cakupan protokol.** Kata yang dipakai orang di Studio menjadi alias lewat steward. Setelah release, pertanyaan **baru** dengan kata itu dijawab benar 100%, dari sebelumnya 79%. Metrik yang tidak dipelajari tidak berubah, sehingga efeknya datang dari pembelajaran, bukan dari variasi run.

Run yang dipakai:

| Run | ID |
|---|---|
| Sebelum | `evn_0688315b…` |
| Sesudah | `evn_cd8d914d…` |
| Regresi | `evn_39d4e173…` |
| Pembanding regresi | `evn_7e868e63…` |

## Jalannya eksperimen

1. **Percakapan.**
   - 29 metrik × 2 pertanyaan, ditanyakan oleh `nova_admin` dan `retail_steward_test` di thread Studio yang mengizinkan belajar.
   - **54 dari 58 jawaban** memakai metrik yang benar dan di-*like*.
   - Dua jawaban tidak memakai semantic query sama sekali (`avg_checkin_distance_m`). Satu memakai *units sold* untuk "penerimaan unit". Satu lagi tidak menjawab "absen di luar lokasi".
2. **Belajar.**
   - `POST /ontology/learn` dengan detektor `conversations` menghasilkan **3 proposal (satu per domain) berisi 24 alias**.
   - **Ke-24 alias tepat sama dengan frasa yang ditulis untuk metrik konsep itu: presisi usulan 24/24.**
   - Enam frasa ditolak oleh aturan dua orang. Contohnya "trip" lawan "jumlah trip": model mengekstrak batas frasa yang berbeda untuk dua orang. "Penerimaan unit" baru di-*like* satu orang.
   - Rasional proposal hanya memuat frasa dan hitungan, misalnya *'qty terjual' (2 people, 2 liked answers)*. Tidak ada pertanyaan atau nama orang.
3. **Steward.** `retail_steward_test` menyetujui ketiga proposal dengan aturan tetap dari protokol. Lalu `nova_admin` menyiapkan dan menerbitkan release `ont_rel_0008`.

## Kegagalan yang tersisa

Ketiga kegagalan sesudah belajar ada di metrik yang tidak dipelajari:

- "absen di luar lokasi": menolak, sama seperti sebelumnya;
- "jumlah trip": menolak, padahal sebelumnya benar;
- "rerata jarak absen": bertanya balik, padahal sebelumnya benar.

Dua yang terakhir adalah variasi planner di metrik yang tidak tersentuh perubahan ontology.

**Regresi.** Satu kasus `serbadata_business` ("nilai unit retur per staf lapangan") berubah dari benar menjadi bertanya balik. Ini kegagalan yang aman, dan p = 1.

## Akses sementara

Steward perlu bisa memakai agent SerbaData. Untuk eksperimen ini, dengan persetujuan pengguna:

- SerbaData dibagikan ke role `retail_steward`;
- role itu diberi SELECT pada `NOVA_RETAIL_FIELD` lewat Ranger (policy 204);
- `nova_admin` dimasukkan sebentar ke role itu, hanya untuk verifikasi akses agent.

Setelah percakapan selesai, semuanya dicabut lewat jalur yang diaudit:

- policy Ranger dihapus;
- role dikeluarkan dari agent;
- visibilitas agent kembali `private`;
- keanggotaan role dicabut.

Steward kini kembali ditolak ("Access denied"). Akses `ACCOUNTADMIN` pemilik sudah diverifikasi ulang.

## Batas

- **Dua akun uji, bukan pengguna nyata.** Pertanyaan dan *like* dijalankan oleh skrip. Aturan *like* adalah "rencana memakai metrik gold", yang mewakili pengguna yang menyukai jawaban benar.
- **Pengukuran memakai jalur `generate_plan`** dari run evaluasi, bukan percakapan Studio penuh.
- **Konflik kepentingan.** Steward yang menyetujui juga salah satu orang yang memakai kata itu. Aturan persetujuannya ditetapkan sebelum hasil terlihat.
- **Frasa ditulis peneliti.** Pengguna nyata bisa memakai kata yang lebih beragam. Aturan dua orang lalu menahan kata yang jarang dipakai.
