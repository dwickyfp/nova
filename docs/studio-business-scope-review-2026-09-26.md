# Audit cakupan business agent Nova Studio

Tanggal: 26 September 2026. Status: research dan rekomendasi, belum diimplementasikan.

Permintaan: ketika pengguna bertanya “data apa saja yang kamu punya”, Studio menjelaskan Semantic View, tools, dan resource yang terikat pada agent. Pertanyaan ini tidak meminta penelusuran database. Nove tetap menjadi assistant untuk pekerjaan database umum. JEV tetap terbatas pada pemilihan model ringan/berat, agent Smart, tools, dan skills.

## Kesimpulan

Studio sudah membatasi pemilihan tool dan akses Semantic View secara struktural. Namun, katalog bisnis milik agent belum menjadi input yang konsisten untuk planner, jawaban tentang kemampuan sendiri, dan semua tool yang menerima resource. Akses database pengguna masih dapat menjadi cakupan efektif ketika `query_execute` dipakai.

Perbaikan `SHOW VIEWS` sebelumnya menangani sintaks. Ia tidak memperbaiki keputusan untuk melakukan inspeksi database pada pertanyaan tentang cakupan agent.

Audit ini berdasarkan pembacaan kode workspace saat ini dan dokumentasi primer. Tidak ada replay percakapan bermasalah, pemeriksaan konfigurasi agent live, pengujian model, atau benchmark baru. Karena itu, jalur salah yang ditemukan merupakan penjelasan yang didukung kode, bukan konfirmasi trace planner pada screenshot.

## Temuan kode

1. **Binding sudah ada dan dapat digunakan kembali.** `agents/semantic/access.py:44` memuat versi aktif yang diizinkan untuk binding agent. Agent tanpa binding mendapat daftar kosong, bukan seluruh Semantic View. `agents/tools/intelligence_views.py:67` menolak view di luar binding. Fondasi ini perlu dipertahankan.

2. **Planner belum menerima katalog bisnis agent.** `assistant/planning.py:157` dan payload di baris 285 menyediakan boolean `has_semantic_model`, tool catalog, skills, konteks aplikasi, dan percakapan. Tidak ada katalog view/metric/dimension milik agent. `assistant/service.py:529` memang memuat model yang diizinkan sebelum planning, tetapi pemanggilan planner di baris 644 hanya meneruskan boolean binding. `agents/prompt.py:115` memberi deskripsi kemampuan tool, bukan isi katalog semantic.

3. **Jalur introspeksi yang ada khusus Nove.** `assistant/tools/inspect_agent_configuration.py:67` secara eksplisit menolak pemanggilan dari Studio agent. Tool tersebut membaca konfigurasi owner untuk kebutuhan Nove. Jangan sekadar menghapus guard ini: agent shared dan pemilik konfigurasi mempunyai konteks akses berbeda. Gunakan resolver metadata bersama bila sesuai, dengan proyeksi khusus agent aktif.

4. **Fallback semantic ke SQL terlalu luas untuk business agent.** `assistant/planning.py:90` dapat mengganti required `semantic_query` dengan `query_execute`; instruksi di baris 237 juga menyarankannya. Perilaku ini tidak membedakan kebijakan Studio dengan Nove. Ketiadaan tool semantic bukan bukti bahwa raw SQL merupakan pengganti bisnis yang sah.

5. **Raw SQL belum terikat pada resource agent.** `assistant/tools/query_execute.py:150` memeriksa identitas pengguna, klasifikasi SQL, dan mengeksekusi dengan role pengguna. Tidak ada pemeriksaan binding resource agent dalam jalur tersebut. Ini bukan bukti bypass RBAC; ini perbedaan antara izin pengguna dan cakupan tugas agent. Pengguna admin dapat mempunyai akses jauh lebih luas daripada misi agent Sales.

6. **Smart masih mempunyai jalur SQL langsung.** `agents/harness_worker.py:829` memberi root `query_execute`. Guard di `assistant/service.py:1219` mewajibkan discovery lebih dahulu dan mencegah SQL langsung ketika metric owner ditemukan. Pertanyaan katalog umum belum tentu cocok dengan alias metric. `agents/agent_control.py:159` mengembalikan deskripsi kandidat dan semantic matches, bukan inventaris lengkap sumber bisnis. Hasil dibatasi 12 kandidat, sehingga tidak boleh diklaim sebagai seluruh katalog tanpa penanda kelengkapan.

7. **Resource binding belum seragam.** Semantic View memiliki binding. `agents/tools/ai_search.py:75` menerima nama index dan meneruskannya bersama identitas pengguna, tanpa binding index agent pada jalur ini. `agents/tools/intelligence_views.py:159` melakukan hal serupa untuk Feature Group. Schema agent di `agents/schemas.py:33` mempunyai semantic bindings tetapi belum field binding umum untuk kedua jenis resource tersebut. Custom/MCP tools sudah dipilih secara eksplisit melalui registry; jangan menganggap setiap connector membuka semua tool.

8. **Mode harness `strict` bukan batas domain bisnis.** Pemeriksaan di `assistant/service.py:1236` menegakkan urutan required capabilities. Mengaktifkan mode tersebut saja tidak menambahkan katalog agent atau pembatasan sumber data.

9. **Skills juga perlu konsistensi antara konfigurasi dan kemampuan efektif.** `agents/service.py:94` menambahkan personal skills milik owner ke daftar discoverable. Untuk business agent yang dikurasi ketat, rekomendasinya adalah skills yang secara eksplisit dipasang atau diizinkan. Ini merupakan perubahan perilaku tersendiri yang harus terukur, bukan disisipkan dalam perbaikan pertanyaan katalog.

Semua path kode di atas relatif terhadap `backend/app/modules/`.

## Perbandingan dengan dokumentasi primer

- Snowflake mengonfigurasi agent melalui tools dan resource masing-masing: Semantic View untuk Cortex Analyst, serta search service dan filter untuk Cortex Search. Instruksi orchestration melengkapi konfigurasi tersebut. Ini mendukung desain resource yang eksplisit sebelum pemilihan tool. [Create and manage agents](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-manage).
- Google BigQuery menjelaskan bahwa data agent mengakses knowledge sources yang dipilih secara eksplisit sekaligus mengikuti izin pengguna. Panduannya menyarankan cakupan agent yang sempit karena cakupan terlalu luas dapat menimbulkan konflik instruksi dan jawaban ambigu. Ia juga menekankan verified queries, glossary, dan metadata. [Conversational analytics overview](https://docs.cloud.google.com/bigquery/docs/conversational-analytics).
- MCP membedakan resources yang dikelola aplikasi dan tools yang dapat dipanggil model. Artinya, memiliki suatu tool tidak otomatis menjelaskan resource apa yang dilampirkan ke agent. Ini pembagian peran protokol, bukan jaminan pembatasan akses otomatis. [MCP server features, versi 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/server).

Rekomendasi di bawah merupakan sintesis untuk Nova, bukan klaim bahwa implementasi vendor tersebut harus disalin.

## Perilaku yang dituju

| Permintaan | Sumber jawaban Studio | Eksekusi yang diperlukan |
| --- | --- | --- |
| Data apa saja yang kamu punya? | Metadata resource terikat yang boleh diketahui pengguna | Baca metadata; tanpa scan database/tabel bisnis |
| Bisa bantu apa? | Kemampuan efektif dari tools, skills, resource, dan misi agent | Tidak perlu menjalankan tools bisnis sekadar membuktikan keberadaannya |
| Revenue bulan lalu berapa? | Definisi metric dan query pada Semantic View terikat | Jalankan tool semantic dengan constraint pengguna |
| Bisa akses data payroll? | Periksa cakupan yang terikat | Nyatakan tersedia/tidak tercakup; jangan mencari database payroll |
| Di Smart, agent apa yang bisa bantu? | Katalog specialist yang diizinkan dan resource mereka | Discovery metadata; tidak perlu menjalankan semua specialist |
| Resource belum tersedia atau akses gagal | Status yang dapat diverifikasi | Jelaskan keterbatasan; jangan diam-diam beralih ke raw SQL |

Jawaban katalog sebaiknya memakai bahasa bisnis: topik, metric, dimensi pengelompokan, kemampuan pencarian dokumen, dan contoh pertanyaan yang didukung. Nama tabel fisik bukan sumber utama penjelasan.

Bedakan **terkonfigurasi**, **diizinkan**, dan **berhasil dibaca**. Metadata dapat membuktikan adanya metric revenue; metadata belum membuktikan nilai revenue terkini atau rentang tanggal yang benar-benar berisi data. Jangan mengarang freshness, jumlah baris, maupun periode ketersediaan.

Agent tanpa Semantic View tetap dapat berguna jika mempunyai tools/resource lain yang dikonfigurasi. Jika seluruh sumber kosong, agent menyatakan belum ada sumber bisnis terhubung dan menjelaskan kemampuan yang masih tersedia.

## Rencana perubahan terukur

### Prioritas pertama: pertanyaan katalog agent

1. Bentuk ringkasan metadata dari agent aktif, binding view yang sudah diotorisasi, dan registry tool efektif. Gunakan data existing; tidak perlu database atau engine baru. Baca metadata melalui service, bukan meminta LLM menemukan tabel konfigurasi lewat SQL.
2. Berikan ringkasan terbatas pada planner dan konteks jawaban. Katalog besar memakai pengambilan detail sesuai kebutuhan dengan penanda hasil terpotong. Gunakan versi konfigurasi dan konteks role untuk invalidasi; jangan menggunakan cache lintas pengguna secara sembarang.
3. Bedakan pertanyaan tentang kemampuan/cakupan diri dari `schema_inspection` dan permintaan hasil bisnis. Dapat memakai cabang intent existing apabila cukup, tanpa kewajiban menambah enum baru. Uji parafrasa Indonesia, Inggris, dan follow-up; jangan mengandalkan satu keyword.
4. Pada jalur katalog Studio, hanya izinkan pembacaan metadata cakupan. Jangan izinkan `query_execute` atau eksekusi tool bisnis untuk menjawab inventaris.
5. Smart menyusun ringkasan berdasarkan specialist yang boleh ditemukan pengguna. Pertahankan identitas pemilik metric. Metric bernama sama dengan definisi berbeda tidak boleh digabungkan begitu saja.

### Prioritas kedua: batas business agent yang konsisten

- Cakupan efektif adalah irisan konfigurasi agent dan izin pengguna saat ini. Keduanya diperiksa sebelum eksekusi. Hak admin tidak memperluas misi agent secara otomatis.
- Hilangkan fallback semantic-ke-SQL untuk Studio yang dikonfigurasi sebagai business agent. Query bisnis yang tidak tercakup harus mendapat penjelasan keterbatasan atau klarifikasi yang relevan.
- Raw SQL menjadi kemampuan eksplisit untuk agent yang memang memerlukannya. Pembatasan resource harus ditegakkan server-side, bukan hanya di prompt. Jangan menyisipkan parser allowlist regex sederhana untuk menjanjikan pembatasan SQL kompleks. Tahap awal yang lebih terbatas adalah meniadakan raw SQL dari agent bisnis; kebutuhan SQL terikat ditangani sebagai perubahan berikutnya.
- Jika search index atau resource connector menjadi sumber agent, deklarasikan resource tersebut secara eksplisit. Untuk tool eksternal yang belum mempunyai kontrak resource, jangan mengklaim seluruh isi sistem eksternal sebagai data agent.
- Pertahankan satu bounded engine. Perbedaan Studio/Nove berada pada konfigurasi, registry, konteks, dan policy yang diterapkan engine tersebut.

### JEV tetap empat keputusan

JEV memilih model ringan/berat, specialist Smart, tools, dan skills dari kandidat yang sudah memenuhi scope dan izin. JEV tidak menentukan otorisasi, memperluas resource, memilih Semantic View sebagai fitur baru, atau mengganti proses ML. Batas scope berlaku sama saat JEV aktif maupun nonaktif.

## Verifikasi yang cukup untuk perubahan ini

Trajectory tests harus memastikan pemilihan tool, sumber bukti, consent bila diperlukan, dan finish reason, sesuai aturan engine repo.

| Kasus | Kriteria lulus |
| --- | --- |
| Inventaris agent dengan Semantic View | Hanya sumber terikat; tidak ada SHOW DATABASES/SHOW TABLES atau query data bisnis |
| Parafrasa/follow-up tentang data yang tersedia | Tetap memakai katalog agent, bukan inspeksi schema |
| Permintaan angka bisnis | Tool semantic benar-benar berjalan; jawaban didukung hasil |
| Agent tools-only | Menjelaskan kemampuan nyata tanpa mengarang Semantic View |
| Resource kosong/tidak diizinkan/gagal dimuat | Tidak mengklaim data tersedia dan tidak fallback ke database lain |
| Pengguna admin bertanya di agent sempit | Katalog tetap sebatas agent |
| Smart dengan agent mirip atau katalog terpotong | Kepemilikan jelas; tidak mengklaim daftar lengkap jika terpotong |
| Pengguna meminta sumber di luar cakupan | Batas runtime tetap berlaku |
| Nove diminta inspeksi database | Perilaku inspeksi yang sah tetap berjalan |
| JEV aktif/nonaktif | Cakupan dan izin identik |

Untuk benchmark sebelum/sesudah JEV, gunakan pertanyaan, konfigurasi agent, resource, data, dan model ringan/berat yang sama. Ulangi pasangan agar variasi model terlihat. Pisahkan perubahan policy Studio dari eksperimen JEV; jangan membandingkan baseline lama dengan JEV plus berbagai perbaikan lain lalu mengatribusikan semuanya ke JEV.

Nilai ketepatan isi, kelengkapan terhadap pertanyaan, klaim tanpa bukti, dan kepatuhan scope. Catat tool calls, latency, dan token secara terpisah. LLM judge menerima jawaban secara acak tanpa label JEV serta metadata/hasil referensi. Pemeriksaan deterministik atas tool dan scope tetap menjadi penentu pelanggaran. Belum ada hasil benchmark baru dalam audit ini.

## Pemeriksaan antislop untuk deliverable research

- Hard Gate: PASS untuk kejujuran klaim. Temuan menunjuk kode dan sumber primer; keterbatasan audit dan belum adanya benchmark dinyatakan. Tidak ada statistik performa rekaan, testimoni, atau asset visual. Pemeriksaan interaksi, layout, keyboard, tema, dan build UI tidak berlaku karena tidak ada UI/aplikasi yang dikirim.
- Purpose-Gate: PASS. Tabel hanya membandingkan perilaku dan kriteria verifikasi. Tidak ada dekorasi visual atau asset baru.
- Liveliness: tidak berlaku pada memo research; tidak ada layar, animasi, atau keputusan desain visual.
- Craftsmanship: PASS untuk cakupan dan bukti. Rekomendasi dibedakan dari implementasi, JEV tetap empat keputusan, dan contoh pertanyaan tidak diklaim sebagai isi konfigurasi live. Pemeriksaan kualitas visual tidak berlaku.
