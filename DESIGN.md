# DESIGN.md, Design System Nova

> Sumber kebenaran tunggal untuk warna, permukaan, dan ikon di frontend Nova.
> Setiap aturan di sini punya gate yang menjalankannya. Melanggar aturan yang
> punya gate berarti `pnpm lint` gagal, bukan sekadar catatan review.

---

## 1. Prinsip

1. **Satu arti, satu token.** Kalau dua tempat butuh makna yang sama, keduanya
   memakai token yang sama. Tidak ada nilai kembar yang dipilih per-komponen.
2. **Warna semantik, bukan warna tampilan.** Komponen menulis *perannya*
   (`text-success`), bukan *warnanya* (`text-emerald-600`). Palet mentah adalah
   keputusan tema, bukan keputusan komponen.
3. **Bangun di atas `theme.css`.** Token sudah ada dan sudah berkarakter
   (`--primary: #d04738`, merah bata). Jangan mengganti, jangan menambah sistem
   warna paralel.
4. **Setiap aturan punya alasan satu baris.** Kalau alasannya tidak muat dalam
   satu baris, keputusannya belum matang.

---

## 2. Token

Semua token didefinisikan di `frontend/src/styles/theme.css`, dipetakan ke
utility Tailwind lewat blok `@theme inline`. Tidak ada file lain yang boleh
mendefinisikan warna.

### 2.1 Warna inti

| Token | Arti | Dipakai untuk |
|---|---|---|
| `--background` | Kanvas aplikasi | `<body>`, area scroll utama |
| `--foreground` | Teks utama di atas `--background` | Body copy, heading |
| `--card`, `--card-foreground` | Permukaan isi tingkat 1 | Kartu, panel, dialog |
| `--popover`, `--popover-foreground` | Permukaan mengambang | Dropdown, tooltip, command palette |
| `--muted`, `--muted-foreground` | Permukaan teredam dan teks sekunder | Label, deskripsi, header tabel |
| `--border`, `--input`, `--ring` | Garis struktur dan fokus | Border kartu, border input, focus ring |
| `--primary`, `--primary-foreground` | Aksi utama, merah bata Nova | Tombol primer, tautan aktif, aksen |

### 2.2 Warna status

| Token | Arti | Dipakai untuk |
|---|---|---|
| `--success` | Operasi berhasil, keadaan sehat | Badge sukses, "success rate", status aktif |
| `--success-strong` | Teks sukses di atas permukaan terang | Teks status sukses, bukan latar |
| `--warning`, `--warning-strong` | Perhatian, ambang terlampaui | Badge peringatan, "slow queries" |
| `--info`, `--info-strong` | Informasi netral, bukan ajakan bertindak | Badge informasi, hint |
| `--destructive` | Kerusakan, penghapusan, kegagalan | Tombol hapus, error |
| `--danger` | Alias `--destructive` | Dipertahankan agar kosakata status konsisten |

**Aturan varian.** Token dasar (`--warning`, `--info`, `--success`) adalah warna
yang aman dipakai sebagai **latar** dengan `-foreground` gelap di atasnya.
Varian `-strong` adalah warna yang lolos AA sebagai **teks**, dan diukur di
ketiga permukaan tempatnya benar-benar muncul: `--background`, tint `/10` dari
warna dasarnya sendiri, dan `--accent`. Ini yang membedakan `text-warning` dari
`text-warning-strong`.

Mengukur hanya di atas `--background` tidak cukup: tint `/10` menaikkan
luminansi latar, dan itu yang menjatuhkan `#a06b11` ke 4.21:1 saat dipakai di
dalam `StatusBadge`. Nilai sekarang (`#986411`, `#4369c4`, `#107c70`) dipilih
supaya lolos di ketiganya sekaligus.

### 2.3 Permukaan

Satu permukaan = satu token. Sembilan variasi `bg-card/40` sampai `bg-card/90`
dan `border-border/50` sampai `border-border/80` dihapus sebagai kosakata.
Opacity desimal pada token permukaan dilarang karena angkanya tidak punya arti
dan setiap penulis kode berikutnya menambah nilai kesepuluh.

| Token | Arti |
|---|---|
| `--surface-1` | Permukaan bersarang di dalam `--card`, mis. baris tabel yang di-hover |
| `--surface-2` | Permukaan kartu standar di atas `--background` |
| `--surface-3` | Permukaan yang harus lebih menonjol, mis. kartu metrik utama |
| `--surface-border` | Garis batas semua permukaan di atas |

**Larangan.** `bg-card/60`, `bg-card/85`, `border-border/70`, dan sejenisnya
tidak boleh dipakai lagi. Nilai yang sudah ada sebelumnya tersebar di 33
kemunculan `bg-card/*` dan 22 kemunculan `border-border/*`; penggantinya adalah
`--surface-*`. Migrasi dilakukan per grup refactor, bukan serentak.

### Nova Glass Shell

**Satu backdrop, dua ketebalan tint.** Shell aplikasi punya satu lukisan latar
(`--shell-backdrop`) yang dipasang pada koordinat viewport. Navigasi dan
workspace sama-sama berdiri di atasnya dan sama-sama memakai warna tint
`--background`; yang berbeda hanya opacity-nya. Karena itu sidebar dan konten
selalu berada di satu keluarga hue, dan gradasi backdrop menyambung melewati
batas kolom.

| Lapisan | Dipakai oleh | Tint | Filter |
|---|---|---|---|
| Chrome (tipis) | Sidebar global, sidebar Studio, rail ikon, drawer mobile, frame inset Console | `--sidebar-navigation-tint` | Satu `backdrop-filter` pada shell |
| Pane (tebal) | `SidebarInset` di bawah sidebar bermaterial navigasi, `<main>` Studio (`shell-pane`) | `--shell-pane-tint` | Tidak ada; komposit statis di atas `--background` opak |
| Isi | Kartu, tabel, editor, transcript, inspector, dialog, popover, menu portal | `--card`, `--surface-*`, `--popover` | Tidak ada; tetap solid |

Glass berhenti di pane. Permukaan isi tidak pernah transparan dan tidak pernah
memakai `backdrop-filter`; material bukan tingkat `--surface-4`.

| Token | Arti |
|---|---|
| `--shell-backdrop` | Lukisan latar shell: teal-slate di kiri atas, bata dan amber di bawah |
| `--shell-pane-tint` | Tint workspace di atas backdrop; 100% di tema terang, 82% di tema gelap |
| `--sidebar-navigation-tint` | Tint chrome di atas backdrop atau konten di belakang drawer |
| `--sidebar-navigation-tint-opacity` | Alpha tint chrome; 45% terang, 46% gelap |
| `--sidebar-navigation-fallback` | Latar opak saat blur tidak tersedia atau transparansi dikurangi |
| `--sidebar-navigation-edge` | Hairline transparan; juga `--inset-border` di tema gelap |
| `--sidebar-navigation-shadow` | Hairline pada sisi yang berbatasan dengan workspace; tidak dipakai pada Console inset |
| `--sidebar-navigation-hover` | Plate interaksi transparan |
| `--sidebar-navigation-selected` | Plate transparan lebih kuat untuk pilihan aktif |
| `--sidebar-navigation-selected-shadow` | Hairline dan bayangan halus plate aktif di tema terang; `none` di tema gelap |
| `--sidebar-navigation-foreground` | Teks navigasi; slate di tema terang, `--foreground` di tema gelap |
| `--sidebar-navigation-muted-foreground` | Teks sekunder yang lolos AA di atas plate navigasi |
| `--sidebar-navigation-ring` | Fokus merah bata dengan kontras terhadap plate navigasi |

**Backdrop harus punya variasi hue.** Kaca hanya terbaca kalau ada sesuatu di
belakangnya. Gradien abu-abu netral di bawah tint terlihat sama dengan warna
solid, jadi backdrop memakai teal-slate dan bata/amber dari palet Nova sendiri.
Blur tidak menggantikan variasi itu.

**Plate dan hairline transparan, bukan hex opak.** Hover, selected, dan edge
adalah putih ber-alpha di kedua tema, sehingga gradasi backdrop tetap terlihat
di bawah baris aktif. Di tema terang plate mengangkat baris (lebih terang dari
sekitarnya, dengan hairline dan bayangan halus); plate gelap di atas backdrop
terang terbaca sebagai cat abu-abu, bukan kaca. Edge tetap slate di tema terang. Kontras diukur pada hasil kompositnya di
setiap stop backdrop, bukan pada warna plate itu sendiri.

**Pane terang tetap 100%.** `--primary` (4.53:1) dan token `-strong` diukur
sebagai teks di atas `--background` tanpa ruang untuk tint, jadi pane tema
terang tidak tembus. Tema gelap punya margin (teks sekunder 7.1:1 pada stop
paling terang) dan memakai 82%. Mengubah opacity pane berarti menghitung ulang
B4 untuk semua token teks.

`--sidebar` dan `--background` tetap opak untuk layout dan konsumen lama.
Sidebar global memilih `material="navigation"`; Studio memakai
`sidebar-navigation-material` dan `shell-pane` tanpa menggabungkan implementasi
navigasi kedua produk. Halaman tidak boleh mengecat `bg-background` selebar
pane, karena itu menutup backdrop; latar pane milik shell.

Material chrome mengisi seluruh kolom sidebar desktop sampai tepi viewport,
termasuk padding layout di sekitar menu. Shell dalam transparan tanpa border,
radius, atau shadow pembentuk card, termasuk pada variant `inset` dan
`floating`. Header, menu, dan footer berada pada satu bidang kaca yang kontinu;
tidak ada permukaan kaca kedua di dalam sidebar. Frame inset Console
menggabungkan tint chrome dan backdrop sebagai latar statis tanpa filter.
Provider dengan sidebar solid memakai latar sebelumnya. Merah bata tetap untuk
identitas dan penanda aktif, bukan latar seluruh sidebar.

Shell memiliki paling banyak satu lapisan backdrop blur statis; header, baris,
history, dan footer tidak memiliki blur sendiri. Sidebar Console inset tidak
mengecat lapisan sendiri: kolomnya bening di atas frame wrapper, sehingga
navigasi dan frame adalah satu permukaan tanpa tepi yang harus disejajarkan.
Brand di header sidebar tidak pernah mendapat plate hover atau selected. `sidebar-navigation-item` berbagi hover dan
selection di dalam shell saja. Drawer mobile memfilter shell agar animasi Sheet
tidak membatasi backdrop; lapisan tint internal dinonaktifkan dan underlay
memakai campuran tint yang sama pada opacity 90%. Blur dan tint tidak
dianimasikan. Lebar, posisi kontrol, radius baris, kepadatan, dan perilaku
navigasi tidak berubah.

Fallback opak berlaku sebelum pemeriksaan `@supports`. Dukungan standar dan
WebKit mengaktifkan blur; `prefers-reduced-transparency` mematikan blur dan
gambar backdrop pada chrome, frame, dan pane. Forced colors memakai warna
sistem dan pilihan aktif yang tetap terbaca. Keterbacaan tidak boleh bergantung
pada blur.

### 2.4 Tipografi

Satu keluarga huruf untuk seluruh UI: **Inter**, di-host sendiri dari
`frontend/public/fonts/` dan didefinisikan di `fonts.css`. `--font-sans` menunjuk
ke `--font-inter`; IBM Plex Sans dan font sistem tetap tersedia lewat
`src/config/fonts.ts` sebagai pilihan, bukan default. Kode memakai JetBrains Mono.

| Peran | Ukuran | Bobot | Contoh |
|---|---|---|---|
| Judul halaman | `text-lg` | 600 gelap, 550 terang | `PageHeader` |
| Label navigasi, judul baris, kontrol | `text-sm` | 500 gelap, 450 terang | Menu sidebar, judul riwayat Studio, tombol |
| Body dan isi tabel | `text-sm` (14px) | 400 | Paragraf, sel |
| Teks sekunder | `text-xs` (12px) | 400 | Timestamp, subjudul brand, label grup |

- **Bobot dikoreksi per tema.** Teks gelap di atas latar terang terlihat lebih
  tebal daripada bobot yang sama dalam keadaan terbalik. `--weight-medium` dan
  `--weight-semibold` bernilai 450/550 di tema terang dan 500/600 di tema gelap;
  `font-medium`, `font-semibold`, `font-bold`, dan `font-heading` membacanya.
  Komponen tidak menulis angka bobot sendiri.
- **Bobot maksimum 600.** `--font-weight-bold` dipetakan ke 600, jadi `font-bold`
  tidak pernah lebih berat dari `font-semibold`. Hierarki dibangun dari ukuran
  dan warna, bukan dari bobot ekstra.
- **Teks terkecil 12px.** `text-[11px]` dan `text-[10px]` tidak dipakai untuk
  teks baru; yang tersisa adalah label huruf besar di menu akun.
- **Letter spacing body `-0.006em`**, rekomendasi Inter untuk 14px. Judul boleh
  memakai `tracking-tight`; teks kecil tidak dirapatkan lagi.
- `font-synthesis-weight: none` mencegah browser menebalkan huruf secara
  sintetis saat berkas font belum termuat.

### 2.5 Grafik

`--chart-1` sampai `--chart-9` dan `--chart-tone-1` sampai `--chart-tone-6`
hanya untuk seri data di dalam grafik. Warna grafik tidak boleh dipakai sebagai
warna UI, dan `--success` diambil dari `--chart-2` supaya bahasa status dan
bahasa grafik tidak bercabang.

- `--chart-1..5` adalah palet kategori lama, dipakai juga oleh `query-cost`.
- `--chart-6..9` melengkapi palet kategori ke sembilan hue. Chart dengan lebih
  dari lima seri sebelumnya memakai enam hex ad-hoc di dalam
  `workspaces/index.tsx`; sekarang semuanya token.
- `--chart-tone-1..6` adalah ramp satu warna untuk mode "single tone".

Grafik yang butuh warna konkret (recharts) membacanya lewat `readToken` di
`src/lib/read-token.ts`, bukan menyalin hex. Token dibaca ulang saat tema
berubah, jadi chart tidak memakai palet lama setelah toggle.

---

## 3. Aturan yang ditegakkan (gate)

Gate berjalan di `frontend/eslint.config.js` dan **gagal sebagai `error`**, bukan
warning. CI tidak hijau sampai pelanggaran dihapus.

### A1. Palet mentah dilarang di `frontend/src/features/**`

Kelas Tailwind dari keluarga palet baku (`red`, `orange`, `amber`, `green`,
`emerald`, `teal`, `sky`, `blue`, `violet`, `slate`, dan seterusnya) dengan
tingkat `50` sampai `950` dilarang di `features/**`. Contoh yang gagal:
`bg-emerald-600`, `text-amber-500`, `border-sky-300`.

Yang benar: `bg-success`, `text-warning-strong`, `border-destructive`.

**Kenapa hanya `features/**`.** `components/ui/**` adalah lapisan primitif yang
memang merangkai token; membatasinya di sana akan memaksa setiap primitif punya
token sendiri sebelum tokennya ada. Batasnya ditegakkan di lapisan fitur lebih
dulu, tempat 44 + 37 + 31 kemunculan pelanggaran berada. Perluasan ke `ui/**`
adalah keputusan terpisah setelah token terbukti cukup.

**Lingkup efektifnya, supaya tidak salah dibaca.** A1 (`semantic-tokens-features`)
hanya berjalan di `src/features/**` — kelas palet mentah di `components/ui/**`
atau `src/lib/**` **tidak** dilaporkan A1. Yang berlaku di seluruh `src/**` adalah
A2 dan A3: aturan `nova/semantic-tokens` memasang `HEX_PATTERN` +
`STORAGE_VENDOR_PATTERN` tanpa batas direktori, jadi hex dan nama vendor storage
tetap dilarang di `components/ui/**` sekalipun A1 tidak menyentuhnya.

### A2. Hex literal dilarang di `frontend/src/**` kecuali `theme.css`

Literal `#rrggbb` dan `#rgb` dilarang di seluruh `src/**`, kecuali
`src/styles/theme.css` (tempat token didefinisikan) dan nilai monaco yang
memang bukan CSS.

#### Pengecualian: fallback pembaca token

Hex **diizinkan** kalau ia adalah argumen ke-N (N > 0) dari pemanggilan fungsi
yang namanya cocok `/^read[A-Za-z]*Token$/` **dan** argumen pertamanya adalah
literal string yang diawali `--`:

```ts
readToken('--chart-1', '#f05a47')            // boleh
readColorToken('--x', 'y', '#d04738')        // boleh
readToken(someVar, '#d04738')                // dilarang, argumen pertama bukan literal
readToken('chart-1', '#f05a47')              // dilarang, bukan custom property
cn('bg-emerald-600', '#d04738')              // dilarang
`text-[#d04738]`                             // dilarang
'#d04738'                                    // dilarang
```

**Kenapa pengecualian ini ada.** `recharts` menerima warna sebagai nilai
konkret, bukan kelas Tailwind, dan `getComputedStyle` tidak ada di server. Jadi
fungsi pembaca token **wajib** punya fallback, dan fallback itu wajar berupa
hex. Tanpa pengecualian, aturan ini menghukum kode yang benar.

**Kenapa berbasis bentuk kode, bukan path berkas.** Pengecualian per-berkas
(`*/chart-colors.ts`) akan membuat seluruh berkas jadi zona bebas: hex sloppy di
`className` bisa bersembunyi di dalamnya. Membatasi ke posisi sintaksis yang
sempit membuat hex hanya sah di dalam pembacaan token, dan tidak bisa dipakai
untuk hal lain.

#### Jangan pakai `hsl(var(--token))`

Ini justru kelas bug yang aturan ini ada untuk mencegah. Token di `theme.css`
bernilai `oklch(...)` atau hex, **bukan** triplet HSL. Jadi:

```ts
// RUSAK. hsl(oklch(...)) bukan CSS yang valid, deklarasinya diabaikan
// diam-diam dan warna jatuh ke nilai awal SVG.
fill: 'hsl(var(--muted-foreground))'
```

Ada 11 situs pola ini di `workspaces/index.tsx` (10) dan `sidebar.tsx` (1),
semuanya bug laten. Yang benar adalah membaca token lewat `readToken` seperti di
atas, atau memakai `var(--token)` langsung di CSS, bukan dibungkus `hsl()`.

Catatan penting: gate A2 **tidak** menangkap `hsl(var(--x))` karena tidak ada
hex di dalamnya. Aturan ini tidak bisa menyelamatkanmu dari pola itu, jadi ini
larangan yang harus dipegang manusia, bukan mesin.

Saat ini ada 107 hex di `features/`, 43 di antaranya di dalam SVG ilustrasi
`sign-in-visual.tsx`, 28 di tema Monaco `workspaces/index.tsx`, dan 12 di
`users/index.tsx`. Semuanya masuk daftar hutang yang dibersihkan per grup.

### A3. `s3://` dan nama vendor storage dilarang di seluruh `src/**`

Menyusul `AGENTS.md` aturan #1, frontend adalah storage-provider agnostic.
Tidak boleh ada `s3://`, `minio`, `gs://`, `azure`, atau `gcs` di UI, state,
maupun komentar yang dirender.

### Baseline hutang lama

Gate ini mendarat di atas repo yang sudah punya 269 pelanggaran (katalog S1-S15
dari audit Fase A). Kalau gate langsung menolak semuanya, PR ini tidak bisa
hijau dan tidak ada yang bisa merge, jadi hutang itu dicatat sekali di
`frontend/eslint.design-system-baseline.json`.

Aturannya:

- Berkas di daftar baseline **dilewati** oleh gate. 22 berkas terdaftar.
- Semua berkas lain **gagal** saat melanggar, tanpa pengecualian.
- **Daftar ini tidak boleh ditambah.** Satu-satunya arah perubahan adalah
  menghapus entri saat grup refactor pemiliknya selesai. Menambah entri berarti
  melemahkan gate, dan itu butuh keputusan eksplisit pemilik desain.
- `src/components/layout/app-title.tsx` sudah dihapus dari daftar di PR ini
  karena pelanggarannya (S4) sudah diperbaiki.

### Cara menjalankan

```
cd frontend
pnpm lint
```

Pelanggaran baru muncul sebagai `error` dengan nama aturan `nova/semantic-tokens`
atau `nova/semantic-tokens-features`, dan pesannya menyebut pasal di dokumen ini.

#### Nilai yang dirakit dari literal

Gate tidak hanya memeriksa node `Literal` dan `TemplateElement`. Nilai yang
dipecah dan disatukan kembali tetap harus terlihat, jadi gate juga
merekonstruksi string dari sintaksis saat bisa diketahui sepenuhnya:

```ts
'bg-' + 'emerald-600'                 // dilarang
['bg', 'emerald', '600'].join('-')    // dilarang
'#' + 'd04738'                        // dilarang
`${'#'}d04738`                        // dilarang
```

Rekonstruksinya konservatif: begitu satu operand tidak diketahui dari sintaksis
(sebuah `Identifier`, pemanggilan fungsi, akses properti), tidak ada nilai yang
dihasilkan dan tidak ada yang dilaporkan. Karena itu `(x) => 'p-2 ' + x` dan
`` (n) => `${n} p-2` `` tidak pernah false-positive. Binding yang tidak diketahui
juga **tidak** diperlakukan sebagai string kosong — literal palet yang mengalir
ke dalam template tetap tertangkap di tempat ia ditulis.

#### Test

Dua lapis, dan keduanya harus hijau:

- `src/lib/design-system-gate.test.ts` — sembilan kasus predikat pengecualian A2
  terhadap bentuk AST.
- `src/lib/design-system-gate.rule.test.ts` — plugin asli dijalankan lewat
  `Linter.verify` in-process, jadi perilaku rule-nya benar-benar diuji, bukan
  direkonstruksi ulang oleh test. Berkas ini berjalan di project vitest `node`
  (ESLint butuh built-in Node yang tidak bisa di-resolve runner browser).

`pnpm lint` terhadap berkas nyata tetap pemeriksaan terakhir.

### Urutan merge PR design system

`theme.css` berubah di lebih dari satu PR, jadi urutannya mengikat:

1. `grup-1-design-system` (aturan, token, gate)
2. `grup-2-semantic-primitives` (lima primitif)
3. `grup-3-monitoring-adoption` (adopsi di enam halaman monitoring)

PR berikutnya bergantung pada token yang mendarat di PR sebelumnya. Rebase PR
lanjutan tanpa mendahulukan yang sebelumnya akan membawa `theme.css` yang
mengasumsikan keadaan yang belum ada.

---

## 4. Aturan yang belum punya gate

Aturan berikut mengikat dan ditinjau manual sampai tokennya cukup matang untuk
ditegakkan otomatis. Setiap grup refactor yang menyentuh area terkait wajib
mematuhinya.

### B1. Satu ikon, satu arti

`Sparkles`, `Wand2`, dan `Bot` saat ini dipakai di 14 lokasi sebagai ikon fitur
LLM dan ML sekaligus. Ikon fitur harus punya arti tunggal. Sampai bahasa visual
ML dan LLM diputuskan, ikon ini **tidak boleh ditambah ke lokasi baru**, dan
lokasi lama dipindahkan ke ikon yang benar saat grup halamannya dikerjakan.

### B2. Setiap data view punya tiga keadaan

Setiap tampilan data wajib punya keadaan kosong, memuat, dan error. Keadaan
kosong menyebut sebab dan langkah berikutnya, bukan hanya "No data". Keadaan
memuat memakai primitif `LoadingOverlay` yang akan dibangun di Grup 2, bukan
animasi ad-hoc per halaman.

Primitif untuk tiga keadaan ini ada di `src/components/ui/`:

| Primitif | Untuk | Jangan dipakai untuk |
|---|---|---|
| `StatusBadge` | Satu keadaan berlabel: sukses, peringatan, info, gagal, netral | Angka atau metrik, itu `MetricCard` |
| `EmptyState` | Daftar/tabel kosong, dengan sebab dan aksi | Error muat data, pakai `variant='error'` |
| `PageHeader` | Judul halaman plus deskripsi dan aksi level halaman | Judul section di dalam halaman |
| `MetricCard` | Satu angka yang menjawab satu pertanyaan, dengan bobot hierarki | Bulk angka tanpa keputusan, itu tabel |
| `LoadingOverlay` / `LoadingLines` / `RefreshBanner` | Muat pertama, muat daftar, refresh di atas data lama | Skeleton buatan sendiri |

`StatusBadge` hanya memakai token status (`--success-strong`,
`--warning-strong`, `--info-strong`, `--danger`), tidak pernah emerald/teal/sky
mentah. `MetricCard` mewarnai ikon, tidak pernah angkanya: angka tetap
`--foreground` supaya netral dan terbaca.

### B3. Aksi yang terlihat harus punya perilaku

Kontrol yang dirender harus benar-benar berfungsi. Pengecualian tunggal adalah
kontrol `// TODO` dengan label "Coming soon" yang terlihat pengguna.

### B4. Kontras

Semua teks lolos WCAG AA: 4.5:1 untuk teks normal, 3:1 untuk teks 18px ke atas.
Garis batas dan ikon non-teks lolos 3:1 terhadap warna di sebelahnya. Rasio
dihitung, bukan diperkirakan.

**Keputusan yang mengikat untuk `--primary`.** `#d04738` adalah warna brand dan
tidak digeser. Rasio terukurnya 4.53:1 di light dan 4.25:1 di dark, jadi:

- `--primary` dipakai sebagai **teks** hanya di mode terang. Di mode gelap,
  teks memakai `--primary-foreground` di atas blok `--primary`, atau token
  status yang relevan.
- Di mode gelap, `text-primary` sebagai teks di atas `--background` dilarang.
  Yang benar adalah `text-primary` di atas blok `bg-primary`, atau
  `text-primary-foreground`.

---

## 5. Kosakata yang dilarang

| Dilarang | Diganti |
|---|---|
| `bg-emerald-600`, `text-blue-500` | `bg-success`, `text-info-strong` |
| `bg-card/60`, `border-border/70` | `bg-surface-2`, `border-surface-border` |
| `#d04738` di luar `theme.css` | `text-primary`, `bg-primary` |
| `FILES('s3://...')` di UI | `SELECT * FROM @stage_name.data.csv` |
| Nama vendor storage di UI/state/log | Nama koneksi (`nova.yaml`) atau `@stage` |
| `text-primary` sebagai teks di dark | `text-primary-foreground` di atas `bg-primary` |
| Ikon AI generik untuk segala arti | Satu ikon, satu arti (B1) |

---

## 6. Status penerapan

Dokumen ini berlaku sejak Grup 1. Gate A1 sampai A3 aktif penuh; pelanggaran
lama yang belum dimigrasi akan muncul sebagai error sampai grup pemiliknya
dikerjakan. Karena itu A1 dan A2 hanya menargetkan berkas yang belum masuk
daftar hutang, dan daftar hutang menyusut setiap PR.

Aturan B1 sampai B4 belum bisa diekspresikan sebagai gate dan bergantung pada
Grup 2 untuk primitifnya.
