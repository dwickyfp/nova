# Benchmark Nova Studio agent memory — 2026-09-23

> Hasil pengujian offline dan realtime pada konfigurasi Sales Agent lokal.

---

## Metode

Eval offline memakai model skrip tanpa kunci provider. Skenario memeriksa fakta omzet pada thread baru, koreksi, pemisahan user/agent/role di query SQL, penolakan ekstraksi tanpa kutipan sumber, penolakan pesan berbentuk kredensial, dan retrieval di antara 100 fakta pengalih.

Benchmark retrieval memakai 200 fakta di memori proses, 500 iterasi, dan tidak menguji waktu query StarRocks. Benchmark realtime memakai **prompt dan model Sales Agent yang dikonfigurasi** (`deepseek-v4-1-flash`). Setiap percobaan memakai user uji unik untuk menyatakan aturan omzet sintetis, menanyakan definisi pada konteks baru, mengoreksinya, lalu menanyakan lagi. Sesudah pilot dengan penanda acak di dalam prompt, percobaan diulang lima kali dengan kalimat bisnis natural. Memory uji dihapus pada `finally`; penghapusan juga dicatat ke audit.

## Hasil

| Pengukuran | Hasil |
|---|---:|
| Eval memory + benchmark offline | 8 lulus |
| Regresi backend terarah | 48 lulus |
| Regresi chat dan dialog memory | 24 lulus |
| Harness agent yang sudah ada | 32/32 skenario; 98/98 pemeriksaan |
| Seleksi 200 fakta, p50 | 0,216 ms |
| Seleksi 200 fakta, p95 | 0,327 ms |
| Panjang prompt dari fakta terpilih | 341 karakter |
| Pagination StarRocks `LIMIT/OFFSET` | Diverifikasi pada StarRocks 4.1; 0 baris uji tersisa |
| Isolasi live di StarRocks | User, agent, dan role lain mengembalikan 0 baris |
| Model tanpa memory | Menggunakan `recognized_revenue` dari konfigurasi agent |
| Ekstraksi fakta awal | 2,207 detik; 1 fakta; 266 token |
| Model dengan memory | Menyebut invoice lunas − retur, tanpa PPN; menandai sumbernya sebagai pernyataan user |
| Koreksi fakta | 2,329 detik; tetap 1 baris memory; 354 token |
| Model setelah koreksi | Menyebut diskon dalam rumus baru |
| Nova Studio UI, Sales Agent | Aturan tersimpan, terlihat di dialog, dan dijawab pada chat baru |
| Koreksi via UI | Rumus dengan biaya layanan mengganti fakta lama; tetap satu memory |
| Recall sesudah koreksi via UI | Jawaban chat baru menyebut invoice lunas − retur − diskon − biaya layanan, tanpa PPN |
| Penghapusan via UI | Berhasil; dialog kembali ke “No memories yet” dan chat sintetis dibersihkan |
| Respons baseline | 3,999 detik; 1.671 token total |
| Respons dengan memory | 3,317 detik; 1.704 token total |

### Lima percobaan realtime dengan kalimat natural

| Pengukuran | Hasil |
|---|---:|
| Fakta awal tersimpan dan diambil | 5/5 |
| Jawaban recall menyebut retur dan tanpa PPN | 5/5 |
| Koreksi menambah diskon, tetap satu memory | 5/5 |
| Jawaban setelah koreksi menyebut diskon | 5/5 |
| Ekstraksi awal, median (rentang) | 2,158 detik (1,811–2,525) |
| Ekstraksi koreksi, median (rentang) | 1,940 detik (1,782–2,097) |
| Respons recall, median (rentang) | 2,600 detik (2,126–3,115) |
| Token ekstraksi awal, median | 262 |
| Token respons tanpa / dengan memory, median | 1.542 / 1.742 |
| Selisih token respons berpasangan, median | +176 |
| Memory sintetis tersisa di StarRocks | 0 |

Kelima cuplikan jawaban recall dan koreksi diperiksa: model menyebut aturan sebagai pernyataan user dan tidak menganggap perlakuan invoice, retur, diskon, atau PPN pada `recognized_revenue` sudah diketahui. Pilot lima percobaan dengan penanda acak juga lulus pemeriksaan kata kunci, tetapi satu jawaban menyinggung penanda itu secara tidak relevan; angka utama di atas memakai prompt natural. Satu percobaan tambahan setelah perbaikan skrip cleanup berhasil dan menghasilkan audit `DELETE`.

Perintah:

```bash
cd backend
uv run pytest tests/eval/test_agent_memory.py tests/benchmark/test_agent_memory.py -q -s
STARROCKS_HOST=127.0.0.1 STARROCKS_FE_MYSQL_PORT=29030 uv run python -m scripts.benchmark_agent_memory_live
```

Baris realtime awal pada tabel pertama berasal dari satu run; tabel lima percobaan adalah pengukuran berulang, bukan p95 layanan. Probe memakai prompt agent yang asli dan model yang dikonfigurasi, tetapi memberitahu model bahwa tool tidak tersedia. Jadi angka latensi respons mengukur panggilan model langsung, bukan seluruh alur HTTP/streaming Nova Studio. Sales Agent mempunyai definisi `recognized_revenue`; memory user menghasilkan jawaban berbeda yang diberi label belum terverifikasi dan dibandingkan dengan metrik itu. Ekstraksi menambah satu panggilan model per turn yang memenuhi syarat. Saat ini token panggilan ekstraksi belum masuk angka pemakaian loop utama di UI. Respons awal sempat menyimpulkan perlakuan invoice belum lunas atau PPN yang tidak dinyatakan secara eksplisit; prompt diperketat dan lima percobaan natural tidak mengulang klaim tersebut pada cuplikan yang diperiksa. Ini tetap batas perilaku model, bukan jaminan. Pada percobaan lebih awal, model mengekstrak tiga fakta dari satu kalimat; instruksi ekstraksi dipersempit agar satu rumus menjadi satu fakta. Variasi ini perlu terus dipantau dengan korpus bisnis yang lebih besar.

Lima keberhasilan dari lima percobaan tidak membuktikan reliabilitas 100%. Belum ada pengukuran beban bersamaan, banyak user dan agent pada UI, atau latensi ujung ke ujung pada volume besar.

Pengujian UI dilakukan di Chrome pada Nova Studio lokal sebagai `nova_admin` dengan Sales Agent yang sudah dikonfigurasi. Uji awal menunjukkan pengajaran definisi dan pertanyaan recall salah diarahkan ke `semantic_query`; guard capability kemudian menghentikan jawaban. Router kini mengarahkan pengajaran, recall aturan yang pernah diajarkan, dan koreksi aturan ke jawaban langsung tanpa tool data. Ekstraksi juga diizinkan setelah kegagalan tool yang sudah selesai, karena fakta eksplisit dari pesan user tetap dapat disimpan. Pengujian ulang membuktikan satu memory tersimpan, dikoreksi, diambil pada chat baru, lalu dihapus. Respons model pada uji awal menyimpulkan perlakuan PPN dan status pembayaran metrik semantik tanpa dasar eksplisit; prompt memory diperketat. Respons recall terakhir menyatakan ketidakpastian itu dengan benar. Server dev beberapa kali memuat ulang saat pengujian karena perubahan file lain yang berlangsung bersamaan, sehingga beberapa turn awal tidak selesai stabil. Suite `studio-app.test.tsx` tidak dapat dimuat karena ekspor `chartSpecWithRows` yang hilang dari `studio-artifacts.tsx` pada area kerja yang sudah berubah; suite chat dan dialog memory lulus terpisah.
