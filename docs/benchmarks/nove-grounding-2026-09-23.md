# Nove: perbaikan routing, skills, knowledge, dan benchmark

> Verifikasi implementasi lokal pada 23 September 2026. Pengujian offline memakai
> scripted provider; angka ini bukan latency model atau StarRocks.

## Perubahan

Nove tetap memakai AssistantLoop yang sama dengan Studio. Perbaikan ini
menghubungkan seluruh 12 playbook bawaan ke discovery Nove, menampilkan metadata
skill yang tersedia, dan memuat paling banyak dua discoverable skill per turn.
Pencocokan skill kini mempertimbangkan frasa trigger, bukan kata umum pada
summary. Trigger Indonesia untuk debugging dan task terjadwal ditambahkan.

Routing membedakan pertanyaan konsep, authoring SQL, troubleshooting, dan
permintaan data. Contoh yang dilindungi tes:

| Permintaan | Perilaku |
|---|---|
| Apa itu revenue? | Tidak diwajibkan menjalankan query |
| What is revenue this month? | Memerlukan evidence data |
| Buat SQL untuk revenue bulanan | Authoring, bukan eksekusi wajib |
| Cari penyebab query lambat | Diagnosis; dapat mengambil playbook dan referensi |
| Tampilkan jumlah transaksi bulan ini | Query wajib sebelum jawaban data |
| Cari pelanggan Sari | Dapat memakai query berotorisasi jika semantic search tidak tersedia |

Fallback tidak menggantikan semantic capability yang diwajibkan untuk agent
dengan semantic model terikat. Consent, active role, dan pemeriksaan akses tetap
mengikuti runtime bersama.

Nove sekarang mendaftarkan data_to_chart dan search_knowledge. Pencarian knowledge
membaca library playbook serta tiga referensi produk yang dikemas bersama backend:
workflow Nove/Studio, troubleshooting query, dan stages/tasks/access. Hasil dibatasi
tiga excerpt, masing-masing 3.500 karakter, dengan source identifier dan hash
konten. Input tidak dapat memilih path file atau mengakses network. Ini corpus
terkurasi, bukan indeks seluruh dokumentasi Nova atau informasi live deployment.

Database, schema, active role, dan worksheet ID diteruskan sebagai konteks model.
Prompt meminta bahasa sesuai pengguna, membedakan dokumentasi dari fakta runtime,
dan memeriksa apakah hasil menjawab tujuan pengguna. Primer authoring diselaraskan
dengan eksekusi read-only dan consent tools lain. Perbaikan playbook create-user
dari pekerjaan keamanan yang berjalan bersamaan dipertahankan.

Loop tidak langsung menonaktifkan tools setelah satu query berhasil. Inspeksi
schema dapat diikuti query data, tetap dalam batas iterasi dan per-tool cap.
Pemeriksaan context budget sekarang berjalan sebelum setiap panggilan provider,
mencadangkan ruang untuk schema tools. Hasil tool yang membuat konteks terlalu
besar dihentikan sebelum panggilan model berikutnya.

Penungguan progress sekarang bangun saat tool selesai atau mengirim progress.
Task progress dibersihkan; tool yang masih berjalan dibatalkan saat loop ditutup.

## Pengujian

| Pemeriksaan | Hasil |
|---|---|
| Unit/integrasi terarah Assistant, Studio, security revision, grounding, dan eval | 446 passed |
| Scorecard harness | 32/32 skenario; 96/96 checks |
| Benchmark offline | 13 passed; 1 engine test deselected |
| Ruff pada file Python yang disentuh untuk pekerjaan ini | Passed |
| git diff --check | Passed |

Tes baru mencakup pemilihan skill dengan frasa Indonesia, referensi dengan revision,
pembatasan sumber file, penyaringan reference yang mengandung credential shape,
input pencarian berlebih, query setelah inspeksi, consent pada fallback query,
dokumentasi yang tidak dapat menggantikan evidence data, overflow setelah hasil
tool, serta cancellation tool yang tidak mengirim progress.

Ada satu warning dependency Starlette/httpx; tidak ada kegagalan pada run verifikasi.
Working tree juga sedang dikerjakan task lain. Hasil ini berlaku pada snapshot
lokal saat pengujian, bukan klaim bahwa seluruh perubahan repo dibuat oleh task ini.

## Benchmark

Median dan p95 dalam milidetik. Setiap sampel tool-round-trip sekarang mereset
script provider dan memverifikasi tool benar-benar berjalan. Benchmark lama
memakai script yang habis setelah sampel awal; hasil jalur tool dari benchmark
lama tersebut tidak digunakan sebagai baseline.

| Kasus | Sampel | Median ms | p95 ms |
|---|---:|---:|---:|
| Text-only turn | 500 | 0.148 | 0.202 |
| Satu putaran tool | 500 | 0.366 | 0.696 |
| Read-only grant | 500 | 0.349 | 0.447 |
| Consent resolver palsu per call | 500 | 0.347 | 0.473 |
| Konteks Nove dengan skill terpilih | 200 | 0.167 | 0.196 |
| Pemilihan skill | 200 | 0.051 | 0.054 |
| Pencarian reference terkurasi | 200 | 12.115 | 14.868 |
| Trajectory dua query, tiga provider calls | 200 | 0.644 | 2.345 |

Setelah script benchmark diperbaiki tetapi sebelum perubahan penungguan progress,
median satu putaran tool adalah 101.904 ms pada 500 sampel. Sesudah perbaikan dan
penambahan pemeriksaan context per-call, median menjadi 0.366 ms. Penurunan ini
menghilangkan penantian polling 100 ms; jangan menafsirkannya sebagai percepatan
LLM, database, atau latency end-to-end dengan faktor yang sama.

Prompt dasar Nove: 8.918 karakter, sekitar 2.229 token estimasi, dalam budget
primer 3.000 token. Metadata skill, body terpilih, workspace context, history,
dan schema tools menambah total request; context budget tetap diberlakukan.

## Reproduksi

Jalankan dari backend dengan environment Python project:

```bash
.venv/bin/python -m pytest tests/unit/test_assistant*.py tests/unit/test_agents*.py tests/unit/test_agent_intelligence.py tests/unit/test_security_revision.py tests/unit/test_nove_grounding.py tests/eval -q
.venv/bin/python -m tests.eval.report --json
.venv/bin/python -m pytest tests/benchmark -m 'not engine' -q -s
```

Hasil mentah dan manifest tersimpan di `artifacts/nove-2026-09-23/`.

## Batas hasil

- Scripted provider menguji wiring dan guardrails. Belum ada pengukuran kualitas
  jawaban model nyata, akurasi SQL terhadap engine, atau latency end-to-end.
- Routing masih deterministik. Kasus baru perlu masuk corpus; belum ada classifier
  LLM dengan confidence score untuk seluruh bahasa ambigu.
- Knowledge retrieval bersifat lexical atas corpus terkurasi. Belum mencakup
  seluruh modul produk atau sinkronisasi otomatis dengan versi deployment.
- Konteks workspace belum mencakup halaman aktif dan error terbaru secara otomatis.
- Tidak ada perubahan ke memory lintas percakapan atau migrasi konfigurasi Nove
  menjadi record agent Studio. Keduanya tetap memakai engine bersama.
- Guardrails berbasis prompt dan evidence tidak membuktikan setiap kalimat model
  benar. Evaluasi provider nyata dengan task pengguna tetap diperlukan.
