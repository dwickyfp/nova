# Studio Intelligent UI acceptance

Branch: `feat/studio-intelligent-ui`. SDK: OpenUI Lang/React **1.0.0**.
Katalog: `nova-openui-1`, 56 komponen dan 59 fungsi Nova, ditambah fungsi SDK
dan binding immutable `Evidence`. Referensi perilaku: Open Intelligent UI
commit `d789417ddf87699681b49f2675f7bceba42fdd65`; source contoh tidak disalin.

Status acceptance: seluruh gate lokal yang berlaku lulus pada 14 Oktober 2026.
Commit/push mengikuti sinkronisasi akhir dengan remote main. Laporan ini tidak
menyatakan GitHub Actions sudah lulus.

## Perubahan dan alasan desain

- Welcome dan transcript menempati satu scroller; composer dan header berada
  di luarnya supaya pengguna dapat mengubah input saat membaca hasil panjang.
- Teks memakai lebar baca existing; jawaban UI dapat melebar untuk filter,
  tabel, chart, diagram, dan itinerary.
- Warna, font, radius, komponen, dan ikon mengikuti `DESIGN.md`. Aksen merah
  bata menandai aksi utama; teks link dark memakai token accent foreground
  agar memenuhi kontras teks. Status tidak bergantung pada warna.
- Kartu membatasi satu hasil interaktif dan state-nya; grid hanya mengelompokkan
  input yang saling berkaitan. Teks, diagram, dan data memakai susunan berbeda.
- Proses Smart tetap dapat dibuka; consent, feedback, attachment, Missions,
  agent picker, Stop, dan artefak lama memakai jalur Nova existing.
- Tidak ada animasi masuk composer atau chart. Timer berubah karena pengguna
  menjalankannya; canvas berjalan sesudah blok selesai dan hanya dalam iframe.

Design Read: workspace intelligence untuk pengguna bisnis, memakai bahasa visual
Nova; **ENERGY 1 / RHYTHM 2 / MOTION 1** sesuai arah yang disetujui.

## Bukti pemeriksaan

| Gate | Hasil lokal dan bukti |
| --- | --- |
| Dependency/Chromium CI | Frozen install pnpm 11.8.0 dan Chromium `--with-deps` lulus; Node 22.22.3. |
| Frontend lint | Lulus, 0 error; 25 warning baseline/generated coverage. |
| Frontend build | Lulus; warning ukuran chunk tetap dilaporkan. MapLibre dimuat dinamis. |
| Frontend coverage | 1.220 tes pada 151 file lulus; statements 51,62%, branches 51,56%, lines 52,28%. |
| Katalog/bundle drift | `pnpm ui:check` memeriksa schema, prompt, digest bundle, dan NOTICE. |
| Browser production | 18 case lulus: light/dark, 1440/768/320 px, renderer, welcome, dan transcript Studio. JSON dan screenshot: `/tmp/nova-intelligent-ui-browser-qa/`. |
| Unit backend/coverage | 7.495 tes lulus dengan 7 warning; coverage 60%, Linux Docker dengan dependency locked. |
| Ruff/batas system data | Changed-file Ruff lulus; `check_user_data_system_access.py` melaporkan tidak ada shortcut system/root pada adapter data pengguna. |
| Evals/scorecard/benchmark | 331 eval lulus; scorecard 48/48 scenario dan 165/165 checks; benchmark 12 lulus, 1 skip. Parser median 51,1 ms, p95 84,0 ms pada 20 proses Node terisolasi. |
| Seeded engine | Suite penuh: 211 lulus, 16 skip kondisional, 30 deselected sesuai marker, 2 warning, 24 menit 31 detik. Persistence/replay UI dan seluruh skenario bisnis Studio lulus pada StarRocks/Redis. |
| Patched FE | Lulus: proxy nyata, active role, row filter, mask, root guard, dan tool Studio; acceptance terpisah dari stack stock. |

Click-through memeriksa perubahan Units 5 menjadi 7, hasil 100 menjadi 140,
checklist, slider lewat ArrowRight, form required, form lanjut melalui callback
Nova, filter measure/row chart, flashcard, countdown/reset, carousel, popover,
date picker, radio, segmented control, IconButton, Pressable, dan link publik.
State terakhir dan revision disimpan lewat kontrak renderer. Enam jenis chart
memiliki pemeriksaan SVG dan tabel data pada browser test, termasuk 12 case
light/dark untuk outline. Delapan warna data dibatasi outline dengan kontras
minimal 3,77:1 pada light dan 5,88:1 pada dark.

Canvas benar-benar menggambar dan mengubah skor melalui klik; akses
`window.parent.document` ditolak. HTML hostile, network/parent/storage/eval,
SVG navigation, state berlebih, evidence palsu, data bisnis literal, nested
interactive control dalam Pressable, dan salinan evidence ke state diuji sebagai
penolakan. State/action/history mengikat principal, active role, security version,
pesan, blok, dan hash program. Cross-device tidak bergantung pada session id.
Fallback Markdown ordered/legacy tetap menampilkan label media tanpa request
eksternal. Input aksi gabungan melebihi 32.000 karakter ditolak tanpa truncation.

Reflow Studio 200% memakai lebar CSS separuh viewport dan DPR 2. Renderer juga
diuji dengan CSS zoom 200%. Keyboard ponsel diemulasikan dengan viewport yang
lebih pendek saat field difokuskan; field tetap terlihat di atas composer.
Pemeriksaan overflow, posisi header/composer, focus, dan kontras memakai DOM
browser nyata. Formula kontras memakai luminance sRGB WCAG: teks normal 4.5:1,
teks besar/kontrol 3:1. Report tidak memuat kegagalan kontras.
Chart dan peta juga diambil sebagai screenshot terpisah ketika berada di
viewport; bentuk bar harus memiliki ukuran terlihat dan canvas peta harus
memenuhi lebar kontainernya. Screenshot blok tersebut diperiksa secara visual.

Baseline coverage tidak ditimpa: backend XML final ditulis ke
`/tmp/nova-ui-validation/coverage.xml`. Perubahan research unrelated dan
`backend/coverage.xml` dipertahankan.

## Batas bukti

Provider pada trajectory dan fixture bersifat deterministik; pemeriksaan ini
tidak membuktikan kualitas generasi dari provider produksi tertentu. Browser
fixture membatasi API state/media pada data uji; seeded engine menguji persistence
dan replay yang nyata. Tile OpenFreeMap dan rute OSRM publik berhasil dimuat.
Probe Wikimedia mendapat HTTP 502; fallback gambar diuji, tetapi keberhasilan
gambar Wikimedia live belum terverifikasi dari jaringan ini. YouTube membuka
tautan watch eksplisit. Runtime canvas dan kamera peta tidak dipersistensikan.

Suite real-engine membutuhkan stack isolated, seed CI, dan MySQL client nyata.
Stack stock tidak menggantikan acceptance patched-FE/Ranger. Conditional live
provider, cross-cluster, shared-data, delayed-publication, dan fitur engine yang
tidak tersedia tetap dilaporkan sebagai skip dengan alasan masing-masing.
Percobaan suite sebelumnya gagal pada deadline/observasi outcome dalam skenario
bisnis Studio saat beberapa job berat berjalan bersamaan. Run akhir memakai
seed bersih, heap FE 2 GiB sesuai CI, dan satu thread BLAS pada proses tes;
seluruh suite lulus tanpa mengubah budget atau assertion tes.

## Delivery Gate antislop

Scope: perubahan Studio dan katalog UI pada task ini. Panduan desain/layout Nova
mengatur komponen existing; gate tidak mengklaim audit ulang seluruh aplikasi.
Bukti runtime: script `frontend/scripts/verify-intelligent-ui.mjs`, browser tests
Studio/renderer, report production, serta screenshot yang diperiksa.

### Hard Gate

- R-02 PASS: scan copy yang ditambahkan tidak menemukan em dash; sel kosong tetap kosong.
- R-03 PASS: 18 case tidak memiliki overflow halaman; composer/transcript reflow pada 320 px dan 200%.
- R-17 PASS: bisnis memakai Evidence; angka fixture terlihat sebagai asumsi dan contoh.
- R-18 PASS: perubahan tidak menambahkan testimonial, avatar orang, atau identitas pelanggan.
- R-23 PASS: tidak membuat aset brand, foto, statistik, atau struktur navigasi baru; fixture diberi label acceptance.
- R-24 PASS: navigasi existing dipertahankan; dua link publik fixture membuka tujuan yang diperiksa.
- R-25 PASS: report browser memeriksa pairing teks/kontrol light/dark dengan threshold WCAG yang sesuai.
- R-26 PASS: click-through mengubah state, mengirim form valid, membuka link, dan menjalankan canvas; source action divalidasi backend.
- R-27 PASS: welcome, preparing, loading saved state/media, empty filtered rows, retry/conflict, invalid-program fallback terlihat sebagai teks.
- R-28 PASS: perubahan tidak menambahkan FAQ atau pertanyaan pemasaran generik.
- R-32 PASS: Tab, ArrowRight, Enter, dan Escape bekerja; field/summary/link memiliki focus indicator Nova.
- R-33 PASS: perilaku ditulis dalam source React/Python; bundler hanya menghasilkan helper resmi dari source tersebut.
- R-34 PASS: seluruh matrix production dijalankan dalam light dan dark, termasuk welcome dan composer.
- R-35 PASS: build dan click-through production dijalankan; seluruh jenis kontrol baru dipakai dalam fixture, dengan enam chart pada browser test.
- R-36 PASS: laporan menyebut hasil lokal, threshold, fixture, dan keterbatasan media/provider; tidak menyatakan sertifikasi atau SLA.
- R-37 PASS: arah Nova dan dials 1/2/1 berasal dari plan yang disetujui; tidak membuat brand alternatif.
- R-38 PASS: data contoh ditandai sebagai asumsi; data bisnis memerlukan evidence dan verifikasi angka sebelum publikasi.

### Purpose Gate

- R-01 PASS: tidak menambah gradient/glow; aksen Nova dipakai untuk aksi utama dan diagram.
- R-04 PASS: ikon existing menunjukkan file, agent, research, send, reset, atau aksi bernama; IconButton mengikuti ikon yang diminta.
- R-06 PASS: font/type scale berasal dari Nova; monospace hanya untuk code dan formula yang memerlukannya.
- R-07 PASS: tidak menambahkan grid dekoratif, blueprint, atau dot background.
- R-08 PASS: panah menandai Send/Continue, bukan dekorasi setiap kontrol.
- R-09 PASS: badge hanya komponen informasi deklaratif; welcome tidak mendapat pill label pemasaran.
- R-10 PASS: tidak menambahkan glassmorphism pada halaman atau blok hasil.
- R-12 PASS: memakai elevation komponen Nova; kartu hasil tidak mendapat shadow besar.
- R-13 PASS: tidak menambahkan glow pada kartu, tombol, border, atau status.
- R-14 PASS: greeting, transcript, form, diagram, chart, dan map memiliki komposisi sesuai isinya.
- R-19 PASS: MOTION 1; composer/hasil tidak dianimasikan masuk, chart animation off, countdown dimulai pengguna.
- R-22 PASS: tidak menambahkan ilustrasi stok atau gambar dekoratif tanpa hubungan dengan pertanyaan.

### Liveliness

- Dials PASS: ENERGY 1, RHYTHM 2, MOTION 1 dinyatakan di atas dan pada dokumentasi implementasi.
- Consistency PASS: hasil tenang dengan variasi teks/area data; tidak ada motion template atau accent page-wide.
- Focal point PASS: welcome berfokus pada pertanyaan; transcript berfokus pada jawaban yang sedang dipakai.
- Whitespace PASS: lebar baca, jarak antar jawaban, serta gap form memisahkan tugas dan hasil.
- Accent PASS: merah bata Nova menandai aksi utama; token semantic menandai state/data yang bermakna.
- Identity PASS: agent, governed result scope, consent, proses Smart, dan cara melanjutkan melalui Nova menjadi pola berulang.
- Design Read PASS: workspace intelligence, pengguna bisnis, visual Nova, dials 1/2/1 ditetapkan sebelum rewrite.

### Craftsmanship dan Quality Locks

- C-1 PASS: alasan warna, layout, lebar, kartu, spacing, dan motion ditulis pada bagian desain.
- C-2 PASS: kontrol baru memiliki state/action nyata; read-only preview mematikan aksi dan script.
- C-3 PASS: setiap blok mengerjakan input, hasil, proses, atau tindak lanjut; tidak ada section template pemasaran.
- C-4 PASS: matrix layar/tema, keyboard, loading/error/conflict, fallback, dan reflow dipakai dalam checks.
- C-5 PASS: Evidence dan label asumsi memisahkan fakta bisnis dari simulasi; tidak ada testimonial/metric rekaan.
- R-05 PASS: halaman chat terdiri dari greeting/transcript/composer dan hasil yang beragam, tanpa hero-card-pricing template.
- R-11 PASS: field/button memakai radius Nova; hasil memakai rounded-xl; hanya ikon/avatar existing berbentuk lingkaran.
- R-15 PASS: CTA bernama Review, Send, Retry save, Reload saved state, Start, Reset, dan View chart data.
- R-16 PASS: copy menjelaskan tindakan dan batas data; tidak menambahkan slogan AI atau janji marketing.
- R-20 PASS: governed scope, agent, Smart, consent, serta hasil yang dapat diubah mencerminkan tugas Nova.
- R-21 PASS: theme mengikuti pilihan pengguna dan diuji pada kedua mode.
- R-29 PASS: warna inti/aksen mengikuti DESIGN; chart memakai delapan token data existing dengan legend/tabel.
- R-30 PASS: referensi digunakan untuk perilaku OpenUI; komponen, warna, navigasi, dan data path berasal dari Nova.
- R-31 PASS: semua keputusan utama memiliki alasan satu kalimat pada bagian desain.

### UI checklist

- UI-01 PASS: palette berasal dari token DESIGN Nova, tanpa gradient baru.
- UI-02 PASS: accent menunjuk aksi/diagram yang bermakna; content utama tetap foreground.
- UI-03 PASS: tidak menambahkan emoji dekoratif pada heading, tombol, atau daftar.
- UI-04 PASS: RHYTHM 2 terlihat pada pergantian teks, form, diagram, chart, dan itinerary.
- UI-05 PASS: tidak menambahkan bento, terminal palsu, pricing, atau stripe dekoratif.
- UI-06 PASS: greeting tidak memiliki badge di atas judul yang mengulang judulnya.
- UI-07 PASS: semua jenis action control baru, date/radio/segment, serta link dipakai dalam click-through.
- UI-08 PASS: hasil dan composer tetap stabil; timer/canvas bergerak hanya melalui tindakan pengguna.
- UI-09 PASS: glass/glow tidak ditambahkan; shadow dan radius mengikuti komponen Nova.
- UI-10 PASS: saving/error/conflict berasal dari hasil request dan ditulis sebagai status teks.
- UI-11 PASS: layout berpusat pada pertanyaan, perubahan asumsi, dan penggunaan hasil, tanpa stat row dekoratif.
- UI-12 PASS: evidence bisnis dan sample label mengatur asal setiap hasil; tidak ada feed fiktif.
- UI-13 PASS: field awal kosong, sel null kosong, placeholder menerangkan input yang diminta.
- UI-14 PASS: state gagal memberi Retry/Reload; media gagal tetap menyisakan deskripsi dan daftar lokasi.
- UI-15 PASS: matrix production mencakup desktop/tablet/320, light/dark, keyboard, dan reflow.

### Human checklist

- HUMAN-01 PASS: script mengukur text/background sRGB pada dua tema, termasuk SVG text dan child Pressable.
- HUMAN-02 PASS: border field/radio, chart/SVG control, dan focus memakai threshold kontrol 3:1 serta token Nova.
- HUMAN-03 PASS: field, button, summary, link, dan composer memakai focus-visible ring/outline yang diuji pada kedua tema.
- HUMAN-04 PASS: native field/form, disclosure, local actions, dan menu dapat dipakai dengan keyboard; Escape menutup agent picker production.
- HUMAN-05 PASS: loading/empty/error/save-conflict ditulis sebagai teks, dengan role status/live region pada state terkait.
- HUMAN-06 PASS: 200% reflow tidak overflow; viewport keyboard yang dipendekkan tetap menampilkan field fokus di atas composer.

### Mobile checklist

- MOBILE-01 PASS: grid input runtuh menjadi satu kolom dan toolbar composer membungkus pada lebar kecil.
- MOBILE-02 PASS: tiga lebar 1440/768/320 menguji desktop, tablet, dan phone; reflow menguji ruang efektif yang lebih sempit.
- MOBILE-03 PASS: padding blok p-3 naik ke sm:p-5; toolbar/input dan max-height textarea mengikuti viewport.
- MOBILE-04 PASS: grid/row mempunyai min-width 0 dan reflow, tanpa benturan kolom pada screenshot.
- MOBILE-05 PASS: report menolak overflow halaman dan posisi composer di luar viewport.
- MOBILE-06 PASS: kontrol baru/composer memakai tinggi minimal 44 px; radio/checkbox memiliki label hit area dan gap.
- MOBILE-07 PASS: aksi memakai click/native controls; popover/details tidak membutuhkan hover.
- MOBILE-08 PASS: agent picker memakai menu Radix dengan lebar dibatasi viewport; toolbar dapat membungkus.
- MOBILE-09 PASS: composer/header berada di luar scroll dengan ruang layout sendiri; field keyboard tetap terlihat.
- MOBILE-10 PASS: production screenshot/checks tersedia pada 320 px, dua tema, keyboard viewport, dan reflow 200%.

### Copywriting checklist

- COPY-01 PASS: hitungan test, pin, batas runtime, dan label fixture bersumber dari code/report; tidak ada klaim pelanggan.
- COPY-02 PASS: copy menjelaskan input, hasil, save, retry, dan kelanjutan tanpa buzzword marketing.
- COPY-03 PASS: scan copy baru tidak memiliki em dash atau en dash untuk gaya kalimat.
- COPY-04 PASS: tanda kutip hanya dipakai pada kode, nama aksi, atau istilah yang sedang diperiksa.
- COPY-05 PASS: tidak ada all-caps clause untuk menekankan marketing; kapital pada identifier/dials mengikuti namanya.
- COPY-06 PASS: copy menamai Nova, planner, server, renderer, atau pengguna ketika menjelaskan perilaku.
- COPY-07 PASS: CTA menerangkan tindakan yang terjadi, termasuk Retry save dan Reload saved state.
- COPY-08 PASS: copy tidak memakai aphorism, false range, staccato drama, atau negative parallelism.
- COPY-09 PASS: tone operasional Nova menjelaskan keputusan dan keterbatasan dengan bahasa langsung.
- COPY-10 PASS: review baca ulang memastikan welcome/status/doc berbunyi sebagai instruksi yang dapat dipakai.
