# Protokol: ontology belajar dari percakapan Studio

Ditulis 8 November 2026, sebelum percakapan pertama dijalankan. Hasilnya dicatat di [conversation-learning.md](conversation-learning.md).

## Klaim yang diuji

Kata yang dipakai orang di Studio untuk sebuah metrik, pada jawaban yang mereka sukai, menjadi usulan alias untuk steward. Setelah steward menyetujui dan release terbit, pertanyaan **baru** yang memakai kata itu dijawab benar lebih sering daripada sebelumnya.

## Kata baru

Ada 29 frasa sehari-hari, satu per metrik. Semuanya ditulis sebelum eksperimen, dan tidak ada yang sama dengan, atau memuat, nama atau alias konsep mana pun di release `ont_rel_0007`. Contohnya: "stok tersisa" untuk *closing stock*, "qty terjual" untuk *units sold*, dan "kunjungan bolong" untuk *missed stops*. Daftar lengkapnya disimpan bersama hasil.

## Pertanyaan

Setiap metrik memakai kasus *answerable* ACTIVE dari gold set `serbadata_business` yang kata ukurannya tercatat dan muncul utuh di pertanyaan. Kata ukuran itu diganti dengan frasa baru. Rencana dan makna gold tidak berubah, jadi jawaban yang benar tetap diketahui.

- Metrik yang dipakai hanya yang punya sedikitnya 3 kasus seperti itu.
- Kasus diurutkan menurut SHA-256 id-nya:
  - **dua kasus pertama untuk percakapan:** satu ditanyakan `nova_admin`, satu ditanyakan `retail_steward_test`;
  - **sisanya menjadi set uji `conversation_terms`.**

## Langkah

1. **Sebelum.** Run jawaban `with_ontology` tanpa petunjuk view pada set uji, dengan release saat ini.
2. **Percakapan.**
   - Setiap pertanyaan percakapan ditanyakan di thread Studio baru yang mengizinkan belajar, ke agent SerbaData.
   - Jawaban di-*like* bila rencana `semantic_query`-nya memakai tepat metrik gold. Ini mewakili orang yang menyukai jawaban yang benar.
   - Jawaban yang lain tidak diberi umpan balik.
3. **Belajar.**
   - `POST /ontology/learn` dengan detektor `conversations`.
   - Steward (`retail_steward_test`) menilai setiap usulan dengan aturan tetap: setujui alias yang memang frasa yang ditulis untuk metrik itu, tolak yang lain.
   - Lalu usulan diterapkan dan release baru diterbitkan.
4. **Sesudah.** Run jawaban yang sama pada set uji, dengan release baru.
5. **Regresi.** Run jawaban `with_ontology` pada `serbadata_business`, dibandingkan dengan run `evn_7e868e63…` (133/134).

## Ukuran

- **Utama:** jawaban benar pada set uji, sebelum lawan sesudah, berpasangan (McNemar), dengan interval Wilson.
- **Pendukung:**
  - jumlah frasa yang diusulkan dan berapa yang benar (presisi usulan);
  - frasa yang tidak lolos aturan dua orang atau validator, beserta alasannya;
  - bertanya balik pada set uji;
  - regresi pada `serbadata_business`.

## Batas

- **Bukan pengguna nyata.** Kedua "orang" adalah akun uji yang dijalankan dari skrip. Yang diuji adalah jalur produknya, bukan perilaku pengguna.
- **Konflik kepentingan.** Steward yang menyetujui juga salah satu orang yang memakai kata itu. Aturan penilaiannya ditetapkan di sini, sebelum hasil dilihat.
- **Efek bisa kecil.** Planner bahasa sering sudah memahami sinonim tanpa alias. Efek kecil pada set uji adalah hasil yang sah dan dilaporkan apa adanya.
