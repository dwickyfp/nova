# Studio Intelligent UI — warning cleanup and regression acceptance

Tanggal: 14 Oktober 2026. Branch: `feat/studio-intelligent-ui`.

## Perubahan

Frontend memisahkan helper, variant, dan context dari modul komponen React;
komponen route berada dalam deklarasi route yang dikelola TanStack. Provider
format SQL memakai tipe Monaco. Directive lint yang tidak terpakai dihapus;
output coverage dikecualikan sebagai file generated. `pnpm lint` menolak setiap
warning baru melalui `--max-warnings 0`.

Build menggunakan source modular MapLibre terkunci, transform deklarasi field
TypeScript yang mempertahankan typed views, dan urutan eksekusi modul yang
terjaga. Batas warning chunk Vite tetap 500 kB; batas pemecahan chunk 400 kB.
Tidak ada warning yang disembunyikan atau ambang warning yang dinaikkan.

Backend memakai `set(..., ex=...)` untuk cursor Redis, autocommit pada koneksi
Arrow Flight yang tidak mendukung transaksi, dan menolak data clustering identik
sebelum fitting. Kandidat clustering dibatasi jumlah baris fitur yang berbeda;
Birch menjalankan clustering global hanya bila subcluster yang terbentuk cukup.
Penghitungan berhenti pada batas kandidat dan menyimpan view baris tanpa
mengalokasikan salinan matriks fitur. Dependency terkunci StatsForecast 2.1.1 menghilangkan warning
escape docstring; dependency test `httpx2` memenuhi TestClient Starlette.
SciPy 1.17.1 menyelesaikan error loader macOS pada extension PROPACK lama;
import `scipy.linalg` dan `scipy.sparse.linalg` pada mesin native berhasil.
[SciPy mencatat perubahan PROPACK ke C pada 1.17.1](https://github.com/scipy/scipy/issues/25635).
Pytest memperlakukan warning sebagai error tanpa filter ignore.
Docstring DDL dengan contoh backtick memakai raw string sehingga compile dan
coverage tidak menghasilkan warning escape. Fixture fake-engine proxy menutup
writer serta menunggu handler selesai sebelum event loop tes ditutup.

Bug tambahan yang diperbaiki:

- Edit form yang dikembalikan ke nilai awal sebelum autosave menyimpan nilai
  terkini, bukan edit sebelumnya.
- Aksi yang menunggu penyimpanan tidak dikirim setelah percakapan ditinggalkan.
- Identitas runtime mencakup agent, thread, pesan, blok, program, dan security
  epoch; runtime lokal tidak dipakai kembali untuk sumber lain.
- Media yang berganti sumber membersihkan error sebelumnya dan mengabaikan
  respons lama yang selesai setelah abort. Membuka kembali sumber yang pernah
  gagal dapat pulih setelah respons berhasil.
- Respons Wikimedia/OSRM rusak menghasilkan error media yang terkontrol atau
  fallback daftar tempat; koordinat boolean/nonfinite ditolak.
- SearchImage menerima thumbnail resmi `thumb.wikimedia.org`, dengan validasi
  hostname penuh. [Perubahan domain didokumentasikan Wikimedia](https://www.mediawiki.org/wiki/Snippets/Direct_imagelinks_to_Commons).
- Context form kosong kini ditolak dengan guard yang efektif.
- Socket upstream proxy dimiliki sejak challenge StarRocks dibuka. Login
  ditolak, handshake rusak, dan client yang pergi sebelum login kini melewati
  cleanup yang sama, tanpa mengubah keputusan autentikasi atau role.

Tes browser menunggu drawer selesai bergerak sebelum mengklik tab. Tes Quality
menegaskan nilai field setelah pengisian dan pengosongan; assertion payload
evaluasi tetap memeriksa bahwa batas yang dikosongkan tidak terkirim.
Provider scripted untuk outcome membedakan prompt judul chat dari jawaban
bisnis. Permintaan judul tidak memiliki evidence; assertion evidence tetap wajib
pada jawaban bisnis dan `context_checked` tetap harus terverifikasi.

## Validasi

| Pemeriksaan | Hasil |
| --- | --- |
| `pnpm lint` (`--max-warnings 0`) | Lulus, tanpa warning/error. |
| `pnpm build` | Lulus, tanpa warning; tsc juga lulus. |
| Entry aplikasi production | Halaman masuk terbuka; tidak ada error/warning pada console browser. |
| `pnpm test:coverage --silent=false` | 152 file, 1.225 tes lulus; statements 51,76%, branches 51,71%, lines 52,41%; tanpa warning/error. |
| `pnpm ui:check` | Katalog, prompt, schema, dan bundle validator sinkron. |
| `pnpm ui:verify` | 18 skenario production browser lulus; light/dark, 320/768/1440 px, zoom 200%, keyboard, form/state, chart, SVG, map, timer, dan canvas terisolasi. |
| Regresi clustering | 5 tes lulus, termasuk duplikat pada mode Interactive/Best dan subcluster Birch terbatas; warning menjadi error. |
| Proxy/DDL terarah | 183 tes lulus tanpa warning; socket fake-engine dibersihkan sebelum event loop ditutup. |
| Regresi lifecycle proxy | 40 tes lulus, termasuk tiga jalur cleanup baru: disconnect, handshake rusak, dan login ditolak. |
| MySQL CLI real-engine terarah | 30 tes lulus tanpa warning; konfigurasi MinIO memakai kredensial disposable stack yang sama dengan fixture API. |
| ML native macOS | 44 tes lulus dengan SciPy 1.17.1; tanpa warning/error. |
| Pengulangan Quality | Kasus batas yang dikosongkan lulus 5/5 pengulangan; suite Quality lengkap juga lulus 18 tes. |
| Changed-file Ruff | Lulus untuk semua Python yang berubah dan tes baru. |
| User-data adapter boundary | Lulus; tidak ada shortcut system/root. |
| Media publik nyata | Adapter Wikimedia menghasilkan gambar resmi; OSRM menghasilkan route LineString. |
| Patched-FE | Lulus dengan proxy/agent nyata: role aktif, row filter, mask, transaksi, isolasi koneksi, dan root guard. |
| Unit backend/coverage | 7.516 tes lulus, tanpa warning/error; line coverage 61,91%, branch coverage 54,50%. |
| Eval agent | 331 tes lulus, tanpa warning/error. |
| Scorecard | 48/48 skenario, 165/165 pemeriksaan lulus. |
| Benchmark agent/UI/prefetch | 27 tes lulus; parser median 92,2 ms, p95 526,3 ms pada 20 proses Node terisolasi, di bawah timeout helper 5 detik. |
| Real-engine integration | 211 tes lulus, 16 skip bersyarat, 30 deselected; tanpa warning Pytest. Migrasi, persistence/revision, replay, API Studio, proxy, storage, dan worker diperiksa pada StarRocks/Redis/MinIO. |
| Benchmark engine | 1 tes lulus, tanpa skip/warning; 20 query baca melalui `query_execute`, median 203,5 ms dan p95 221,7 ms. |

Frontend memakai Node 22 dan Chromium Playwright sesuai setup CI. Gate backend
memakai Linux Docker dengan dependency dari lockfile dan Node 22; import serta
regresi ML juga diperiksa secara native pada macOS setelah update SciPy.

Chunk JavaScript aplikasi terbesar 293,45 kB. Worker MapLibre terpisah berukuran
507,96 kB dan baru digunakan saat peta dibuka. Notice BSD MapLibre pada output
production sama dengan lisensi dependency terkunci.

Benchmark engine dijalankan terpisah menggunakan runner dengan Docker CLI dan
socket stack, memenuhi prasyarat yang tidak tersedia pada runner benchmark umum.
Integration final selesai dalam 25 menit 27 detik. Enam belas skip mencakup
provider/custom SQL opt-in (2), instalasi Ranger khusus (7), kemampuan stock FE
yang tidak tersedia (2), migrasi lintas cluster (1), shared-data stack (2), dan
konfigurasi fault-injection FE khusus (2). Acceptance patched-FE di tabel di atas
dijalankan terpisah setelah perubahan proxy terakhir. Tidak ada assertion yang
dihapus atau skip baru yang ditambahkan untuk membuat hasil lulus.

## Batas bukti

Tes provider menggunakan trajectory deterministik; hasilnya tidak membuktikan
kualitas semua jawaban dari provider LLM produksi. Layanan publik dapat gagal di
kemudian hari; success probe dan fallback keduanya diuji. Hasil lokal dibedakan
dari GitHub Actions. Tidak ada klaim bahwa seluruh kemungkinan bug pada produksi
sudah dapat ditiadakan.

Nol warning mengacu pada diagnostic lint/build/browser dan warning runner tes
Python. Log aplikasi untuk penolakan akses dan kemampuan engine yang dinonaktifkan
tetap dipertahankan; stack stock melaporkan placeholder UDF saat `enable_udf=false`.
Tes fault-tolerance juga sengaja memicu log saat FE tidak tersedia dan pembacaan
archive task ditolak. Warning judul dari fixture provider dan resource warning
socket sudah hilang pada pengulangan final.

Perubahan unrelated `backend/coverage.xml` dan dokumen research Snowflake tetap
dipertahankan serta tidak masuk commit tujuan ini.
