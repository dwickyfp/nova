# Validasi perbaikan katalog bisnis Studio

Tanggal: 26 September 2026.

Implementasi dan pengujian lokal selesai. Uji jawaban dengan provider nyata masih tertahan oleh persetujuan inferensi eksternal dan sesi login aktif.

## Perubahan

- Studio mendapat `describe_agent` secara otomatis. Tool membaca metadata Semantic View yang diotorisasi, tools efektif, serta skills. Ia tidak menerima agent ID dari model dan tidak melakukan penelusuran database.
- Planner dan konteks jawaban mendapat ringkasan katalog yang dibatasi ukurannya. Pertanyaan tentang sumber atau kemampuan sendiri mempunyai intent `agent_catalog`.
- Validator membatasi intent tersebut ke `describe_agent` saja dan mewajibkan hasil tool sebelum jawaban selesai. JEV tidak dapat menambahkan eksekusi bisnis atau menghapus kebutuhan bukti katalog.
- Smart membaca katalog specialist yang diizinkan tanpa spawning. Identitas pemilik metric dipertahankan. Daftar memakai pagination; rincian yang terpotong ditandai.
- Data katalog membedakan binding yang tidak tersedia dari konfigurasi kosong. Ekspresi SQL, tabel fisik, dan metric internal tidak disertakan dalam proyeksi Semantic View.
- Fallback semantic ke SQL tidak berlaku pada registry Studio. SQL bebas tidak terdaftar dalam runtime Studio, dan `query_execute` juga menolak langsung konteks Studio dengan audit. Nove tetap dapat menjalankan inspeksi SQL yang diizinkan.
- Konfigurasi UI tidak menawarkan SQL bebas. Saat konfigurasi lama disimpan, pilihan tersebut dibersihkan; pilihan tools bisnis dipertahankan. Konfigurasi tersimpan tidak dimigrasikan secara massal.
- Query semantic terkompilasi, custom/MCP tools yang dipasang, dan proses ML menggunakan jalur existing. Engine tetap satu; cakupan JEV tetap empat keputusan.

## Bukti pengujian

| Pemeriksaan | Hasil |
| --- | --- |
| Gabungan `tests/eval`, unit agent, RBAC shared, Smart, decision, query tool, dan inspeksi konfigurasi | 707 lulus |
| Tes khusus katalog setelah tambahan kasus binding tidak tersedia dan validasi parameter | 28 lulus; sebagian juga terdapat dalam kelompok di atas |
| Scorecard `python -m tests.eval.report` | 48/48 skenario, 165/165 pemeriksaan |
| Browser `configuration-tab.test.tsx` | 6 lulus |
| Frontend `npm run build` | Lulus |
| Ruff pada file Python yang disentuh | Lulus |
| `git diff --check` | Lulus |

Skenario baru mencakup blok query/delegasi saat intent katalog, kewajiban bukti sebelum menjawab, parameter yang tidak sah, metadata kosong/gagal, pagination, credential redaction, business source projection, penolakan SQL bebas termasuk role admin, dan JEV yang mencoba mengubah pilihan tools katalog.

Tes browser menjalankan interaksi Tools, pengecekan pilihan semantic, tidak tersedianya pilihan SQL bebas, serta Save changes dan payload penyimpanannya. Tes existing pada file yang sama memeriksa editor custom tool dan penggantian model. Tidak ada perubahan layout, asset, warna, atau animasi.

Tes trajectory menggunakan provider terprogram. Parafrasa pada tes tersebut memverifikasi jalur runtime setelah klasifikasi; tes ini tidak mengukur akurasi pemahaman bahasa model nyata. Tidak ada klaim kenaikan akurasi atau benchmark JEV baru.

Build melaporkan peringatan ukuran chunk existing. Pengujian melaporkan peringatan kompatibilitas FastAPI/httpx dan Vite config; semuanya selesai dengan exit code sukses.

## Verifikasi live yang tertahan

Pembacaan konfigurasi lokal berhasil menemukan Sales Agent dengan satu binding Semantic View. Model terdaftar saat pemeriksaan adalah `space-bunny-alpha:free`, `deepseek-v4-1-flash`, dan decision model `jev-1-13-free`; ketiganya menunjuk hostname `kenari.id`.

Pemeriksaan persetujuan otomatis menolak script uji inferensi karena payload prompt/katalog dan tujuan eksternal belum disetujui secara spesifik. Script tersebut tidak dijalankan. Pemeriksaan katalog melalui service lokal kemudian tidak menemukan sesi owner yang memenuhi prasyarat. Percobaan memperluas pencarian sesi ditolak otomatis sebagai probing sesi dan dihentikan.

Langkah lanjutan yang disiapkan: login normal ke Nova, lalu uji pertanyaan katalog dan kontrol pertanyaan angka bisnis dengan provider terdaftar. Persetujuan perlu mencakup pengiriman pertanyaan, prompt agent, dan metadata bisnis terikat ke `kenari.id`. Kredensial tidak menjadi payload prompt. Settings global JEV dan konfigurasi agent tidak perlu diubah untuk uji berpasangan.

## Batas implementasi

Katalog merupakan bukti konfigurasi, bukan bukti nilai metric, freshness, atau periode data aktual. Katalog yang terpotong tidak boleh disebut lengkap. Binding resource umum baru untuk search index dan Feature Group belum ditambahkan; kedua tool itu masih memakai kontrak dan otorisasi existing. Perubahan ini tidak boleh dianggap sebagai sandbox universal untuk setiap custom tool, connector, atau input ML.

## Antislop

- Hard Gate: PASS untuk perubahan yang dikirim. Tidak ada klaim hasil live atau peningkatan akurasi tanpa bukti. Build dan tes interaksi browser lulus. Tidak ada asset, statistik produk, atau testimoni rekaan.
- Purpose-Gate: PASS. Teks tambahan menjelaskan batas tool yang benar-benar diterapkan. Pilihan UI yang tidak dapat dijalankan dihapus. Tidak ada dekorasi baru.
- Liveliness: tidak ada keputusan desain visual baru; konfigurasi mempertahankan tampilan produk existing.
- Craftsmanship: PASS. Pemeriksaan kode, lint, build, dan tes sesuai perubahan selesai; batas verifikasi live dicatat secara eksplisit. Komentar baru hanya menjelaskan fungsi atau batas metadata.
