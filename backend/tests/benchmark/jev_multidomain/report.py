"""Render measured artifacts; missing experiments stay visibly incomplete."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import UTC, datetime

from tests.benchmark.jev_multidomain.acceptance import acceptance
from tests.benchmark.jev_multidomain.analysis import aggregate
from tests.benchmark.jev_multidomain.environment import ARTIFACTS
from tests.benchmark.jev_multidomain.metrics import LABELS


def percent(value: float | None) -> str:
    return "Belum terukur" if value is None else f"{100 * value:.2f}%"


def number(value: float | None) -> str:
    return "Belum terukur" if value is None else f"{value:,.3f}"


def table(headers: list[str], rows: list[list]) -> str:
    def cell(value) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
        + ["| " + " | ".join(map(cell, row)) + " |" for row in rows]
    )


def matrix(summary: dict) -> str:
    values = summary.get("confusion_matrix")
    if not values:
        return "Belum ada matriks yang dapat dihitung."
    return table(
        ["Expected / actual", *LABELS],
        [[label, *(values[label][other] for other in LABELS)] for label in LABELS[:5]],
    )


def render() -> dict:
    analysis = aggregate()
    smart, probes, comparisons = (analysis["smart"], analysis["probes"], analysis["comparisons"])
    primary = smart.get("final-on", {})
    baseline, controlled = smart.get("baseline-off", {}), smart.get("controlled-on", {})
    environment = json.loads((ARTIFACTS / "environment.json").read_text())
    ground = json.loads((ARTIFACTS / "ground_truth.json").read_text())
    experiments = json.loads((ARTIFACTS / "experiments.json").read_text())
    all_cases = {case["id"]: case for case in ground["cases"]}
    all_cases.update(
        {
            case["id"]: case
            for case in json.loads((ARTIFACTS / "cohort-ground-truth.json").read_text())["cases"]
        }
    )
    required = {"final-on": 200, "cohort-on": 6}
    required.update({e["name"]: len(e["ids"]) for e in experiments["experiments"]})
    coverage = [
        [
            name,
            expected,
            smart.get(name, {}).get("attempted", 0),
            smart.get(name, {}).get("judge_missing", expected),
        ]
        for name, expected in required.items()
    ]
    complete = all(
        planned == attempted and missing == 0 for _, planned, attempted, missing in coverage
    )
    status = "Pengukuran Smart lengkap" if complete else "DRAFT: pengukuran belum lengkap"
    sections = [
        "# JEV Multi-Domain Benchmark",
        f"{status}. Dihitung pada {datetime.now(UTC).isoformat()}.",
        "Laporan membedakan ranking relevansi JEV, keputusan delegasi Smart, eksekusi SQL, "
        "dan jawaban akhir. Probe ranking tidak membuktikan keberhasilan end-to-end.",
        "# A. Architecture Observed",
        "Istilah resmi yang digunakan TypeSafe adalah System One. Jev menerima state dan "
        "pertanyaan bertipe Choice, Score atau Noul; model ini ditujukan untuk keputusan "
        "terbatas yang dikomposisikan oleh kode. "
        "[Dokumentasi TypeSafe](https://docs.typesafe.ai/introduction).",
        "Smart memakai satu AssistantLoop yang juga dipakai specialist. DecisionSession "
        "memilih model ringan/berat, kemudian planner menentukan tools dan skills. Smart "
        "memanggil discover_agents untuk mengambil registry yang lolos akses, capability "
        "manifest, dan alias metrik. Ranking lexical/semantic berjalan sebelum rank_agents "
        "meminta penilaian relevansi JEV. Model jawaban kemudian memilih spawn_agent dan "
        "wait_agent. Specialist memakai semantic_query, SemanticPlan tervalidasi, compiler "
        "SQL, koneksi pengguna, dan bukti query sebelum menyelesaikan jawaban.",
        "JEV mengembalikan primary/support/irrelevant per kandidat. Hook saat ini mengubah "
        "urutan kandidat; ia tidak langsung membuat keputusan FINANCE/MARKETING/MULTI/"
        "CLARIFY/GENERAL. Kandidat dan pemilik metrik tetap dibatasi secara deterministik. "
        "Ambiguitas, delegasi gabungan, dan sintesis akhir masih bergantung pada model jawaban.",
        "Candidate.prompt_view mengirim deskripsi dan ringkasan capability, termasuk owns "
        "dan good_for. Seluruh deskripsi metrik, dimensi, relasi, dan definisi Semantic View "
        "tidak dikirim ke hook ranking ini. Karena itu ablation mengukur konteks yang benar-benar "
        "mencapai hook, bukan manfaat seluruh katalog semantik secara hipotetis.",
        "Runner membuat pesan dengan security context dan root Smart sungguhan, lalu menjalankan "
        "AgentHarnessWorker.process pada antrean dalam root tersebut. Lima tabel jurnal disalin "
        "ke nama CONFIG_JEVBENCH_* di StarRocks. Pemeriksaan akses, lease, mailbox, consent, "
        "tool, provider, SQL dan validasi bukti tetap nyata. HTTP/SSE, UI, admission endpoint, "
        "dan sweeper recover_stale/expire_waiting tidak termasuk jalur yang diuji.",
        "# B. Benchmark Environment",
        f"Database sintetis: `{environment['database']}`. Seed 20260925; "
        f"{sum(environment['row_counts'].values()):,} baris dalam "
        f"{len(environment['row_counts'])} tabel. Tabel Finance dan Marketing mencakup dua "
        "tahun, tren, seasonality, anomali, deferral, credit note, late posting, multi-touch "
        "attribution dan perbedaan nilai pelanggan. Nilai uang fixture memakai IDR.",
        table(
            ["Tabel", "Baris"], [[name, count] for name, count in environment["row_counts"].items()]
        ),
        "Finance memiliki 18 metrik pada 9 dataset; Marketing memiliki 18 metrik pada 8 dataset. "
        "Metadata memuat grain, ownership, synonyms, relasi dan batas interpretasi. Deskripsi "
        "kedua agent masing-masing 49 kata. Semantic Views versi 2 telah dipublikasikan; "
        "50 query hasil kompilasi cocok dengan oracle SQL, termasuk setelah pemulihan database.",
        table(
            ["Komponen", "Konfigurasi"],
            [
                ["Decision", "jev-1-13-free; respons provider jev-1.13-free"],
                ["Endpoint decision", "https://kenari.id/v1/systemone, dipakai utuh tanpa suffix"],
                ["Model ringan tersimpan", "space-bunny-alpha:free"],
                ["Model berat dan blind judge", environment["answer_model"]],
                ["Timeout JEV", environment["decision_settings"]["timeout_seconds"]],
                ["Probability / confidence minimum", "0.85 / 0.60; margin minimum 0.15"],
                ["Temperature", "Tidak dikirim eksplisit; default provider tidak diketahui"],
                [
                    "Deadline benchmark per kasus",
                    "240 detik; root produksi memiliki budget 600 detik",
                ],
                ["Concurrency akhir", "2 kasus utama, 1 paired case, 1 probe, 1 judge"],
            ],
        ),
        "Konfigurasi provider pengguna tidak diubah. Arm normal memakai pilihan ringan/berat "
        "tersimpan. Arm off dan controlled-on sama-sama memakai model berat, sehingga pasangan "
        "itu lebih tepat untuk mengukur kontribusi JEV. Override hanya berlaku dalam runner.",
        "Dua gangguan infrastruktur dipisahkan dari skor: FE dimulai ulang oleh pihak yang "
        "tidak teridentifikasi pada fase awal; kemudian Docker mencatat OOM pada 18:44:42 UTC. "
        "Container yang sama dipulihkan dan oracle diverifikasi lagi. Percobaan terputus tetap "
        "tersimpan di infrastructure-interrupted-1750/ dan artefak diagnostik. Percobaan dengan "
        "filter registry terlambat disimpan di postfiltered-registry-pilot/. Rincian ada di "
        "[protokol](../jev-multidomain-plan.md).",
        "Load trial dengan 4+2 kasus Smart memicu HTTP 429 pada provider jawaban dan "
        "latency lebih panjang. Seluruh trial, termasuk hasil yang berhasil, disimpan "
        "di rate-limited-six-worker-pilot/. Cohort final memakai 2+1 kasus bersamaan.",
        "# C. Benchmark Dataset",
        f"Ground truth utama: 200 pertanyaan, SHA-256 `{ground['sha256']}`. "
        "138 DEV dan 62 HOLDOUT; seluruh anggota paraphrase dan percakapan berada dalam split "
        "yang sama. Pertanyaan dan oracle dibekukan sebelum inferensi. Label, ID benchmark "
        "dan nilai oracle tidak disisipkan ke prompt sistem yang diuji.",
        "Batas interpretasi holdout: beberapa percobaan sebelumnya sudah menjalankan kasus "
        "holdout, termasuk satu kasus pada prefix sebelum perbaikan target relatif. Perbaikan "
        "dibuat dari trace DEV, tetapi pengukuran final ini bukan holdout yang sepenuhnya "
        "belum pernah dijalankan. Daftar dan provenance percobaan tetap dipertahankan.",
        table(
            ["Kategori", "Jumlah"], sorted(Counter(c["category"] for c in ground["cases"]).items())
        ),
        "Terdapat 10 intent dengan 3 paraphrase dan 10 percakapan dengan 3 giliran. Enam "
        "tantangan tambahan memerlukan join cohort campaign/acquisition ke data customer "
        "Finance. Oracle tambahan dibekukan secara terpisah sebelum pengujiannya.",
        "Dua belas kasus DEV dipilih dengan hash tetap per kategori sebelum hasil dilihat. "
        "Enam kasus ambiguity/cross-domain/adversarial diulang tiga kali. Ukuran subset ini "
        "kecil; hasil invariance yang baik belum membuktikan batas populasi di bawah 2%.",
        table(["Eksperimen", "Direncanakan", "Terekam", "Belum dinilai"], coverage),
        "# D. Baseline Result",
        "Baseline lexical memakai rank_candidates produksi. Baseline Smart off menonaktifkan "
        "DecisionSession hanya dalam proses benchmark. Pilot sebelum perbaikan tetap dilaporkan "
        "sebagai diagnosis; metadata fixture, concurrency, dan cakupannya berbeda sehingga "
        "tidak dipakai untuk mengklaim kenaikan akurasi seluruh 200 kasus.",
        "Pilot lama memakai rubric v1. Seluruh arm final memakai v2, yang menegaskan bahwa "
        "satuan dalam kolom tabel tetap sah dan hilangnya label periode saja bernilai 2 jika "
        "angka serta grain sudah benar. Koreksi evaluasi ini tidak diklaim sebagai peningkatan "
        "model. Penilaian lama disimpan dalam judge-rubric-v1/ dan archive percobaan.",
        table(
            ["Arm", "N", "Routing", "SQL oracle", "Skor jawaban / 3"],
            [
                [
                    name,
                    value["attempted"],
                    percent(value["routing_accuracy"]),
                    percent(value["execution_oracle_accuracy"]),
                    percent(value["answer_normalized_accuracy"]),
                ]
                for name, value in smart.items()
                if name
                in {
                    "baseline-off",
                    "controlled-on",
                    "baseline-pilot-recovered",
                    "metadata-v2-pilot",
                    "corrected-controlled-pilot",
                    "pre-evidence-fix",
                }
            ],
        ),
        "# E. Failure Analysis",
        "Diagnosis DEV menemukan nama dimensi yang berulang antar-dataset, konteks percakapan "
        "Smart yang tidak dimuat kembali, penolakan bukti berdimensi qualified yang sebenarnya "
        "cocok, dan koneksi pool yang masih memiliki paket belum terbaca setelah cancellation. "
        "Masalah tersebut terpisah dari kualitas klasifikasi JEV.",
        "Pada diagnostic planner dengan katalog dan pertanyaan yang sama, model ringan "
        "menghasilkan native tool calls yang tidak diminta dan tidak menghasilkan SemanticPlan; "
        "model berat menghasilkan JSON valid. Ini observasi komponen, bukan bukti bahwa "
        "setiap panggilan model ringan akan gagal. Unsupported tools tidak dijalankan.",
        "Timeout pada worker tidak otomatis berarti JEV salah. Deadline benchmark mencakup "
        "autorisasi, query jurnal, provider, tool dan koordinasi. Kegagalan database dicatat "
        "terpisah; error dan kasus tanpa jawaban tidak dibuang untuk memperbesar skor.",
        table(
            ["Kategori kegagalan utama", "Jumlah kasus"],
            sorted(primary.get("error_types", {}).items()),
        ),
        "# F. Optimization Performed",
        table(
            ["Masalah", "Perbaikan", "Bukti / batas klaim"],
            [
                [
                    "Dimensi region/date berulang",
                    "Nama qualified hanya untuk nama yang ambigu setelah scope akses",
                    "Regression katalog dan akses; 50/50 query semantic oracle cocok",
                ],
                [
                    "Follow-up kehilangan konteks",
                    "Muat history user/assistant sesuai security context "
                    "dengan batas 12 pesan, 8.000 karakter",
                    "Trajectory worker, batas ukuran, role isolation, dan future-message exclusion",
                ],
                [
                    "Bukti revenue.region ditolak untuk permintaan region",
                    "Cocokkan qualified dimension ke alias keluaran tanpa menerima dataset lain",
                    "Trajectory query valid dan negative test dataset berbeda",
                ],
                [
                    "Cancellation merusak reuse koneksi",
                    "Tutup dan buang koneksi asyncmy 0.2.11 serta bangunkan penunggu pool",
                    "12/12 probe lifecycle dan 120/120 concurrent controls; "
                    "produksi tidak di-upgrade",
                ],
                [
                    "wait_agent menolak task_name yang baru dibuat",
                    "Dahulukan pencocokan ID, lalu resolve semua path relatif "
                    "dalam tree terotorisasi",
                    "44 tes kolaborasi/trajectory lulus. Pilot live selesai dengan path lengkap; "
                    "keberhasilan pilot tunggal tidak diatribusikan ke fix ini",
                ],
                [
                    "Fixture mencakup agent lain saat akses",
                    "Filter enumeration ke ID fixture sebelum otorisasi produksi",
                    "Unit test memastikan verifier tetap dijalankan "
                    "pada setiap agent yang diizinkan",
                ],
                [
                    "Outage dihitung sebagai kualitas model",
                    "Hentikan batch dan simpan infrastructure record terpisah",
                    "Unit test tidak memberi skor pada pertanyaan yang belum dijalankan",
                ],
            ],
        ),
        "Perbaikan tidak menambahkan pemetaan pertanyaan ke agent atau contoh jawaban benchmark "
        "ke prompt. Holdout tidak dipakai untuk menyetel aturan. Dampak statistik end-to-end "
        "dibatasi oleh ukuran sampel baseline dan perbedaan kondisi pilot.",
        "# G. Final Benchmark",
        table(
            ["Metrik", "Hasil", "Target", "Status"],
            [
                [
                    r["metric"],
                    percent(r["observed"]),
                    r["operator"] + " " + percent(r["target"]),
                    r["status"],
                ]
                for r in acceptance(primary, comparisons)
            ],
        ),
        table(
            ["Split", "N", "Exact routing", "Jawaban / 3"],
            [
                [
                    name,
                    value["n"],
                    percent(value["routing_accuracy"]),
                    percent(value["answer_normalized_accuracy"]),
                ]
                for name, value in primary.get("splits", {}).items()
            ],
        ),
        table(
            ["Kategori utama", "N", "Exact routing", "Jawaban / 3"],
            [
                [
                    category,
                    value["n"],
                    percent(value["routing_accuracy"]),
                    percent(value["answer_normalized_accuracy"]),
                ]
                for category, value in primary.get("categories", {}).items()
            ],
        ),
        table(
            ["Agent", "Precision", "Recall", "F1", "FP", "FN"],
            [
                [
                    name,
                    percent(v["precision"]),
                    percent(v["recall"]),
                    percent(v["f1"]),
                    v["false_positive"],
                    v["false_negative"],
                ]
                for name, v in primary.get("per_agent", {}).items()
            ],
        ),
        "Skor jawaban dinormalisasi sebagai jumlah skor dibagi 3N. Skor 0 berarti salah atau "
        "kosong, 1 sebagian benar, 2 benar dengan kekurangan, 3 benar dan lengkap. Empty answer "
        "dinilai 0 secara deterministik. Judge melihat pertanyaan, oracle dan jawaban akhir, "
        "tanpa label arm atau routing. Judge memakai model berat yang juga dipakai sistem, "
        "sehingga bias bersama tetap mungkin; audit LLM kedua disimpan terpisah.",
        "LLM judge bukan oracle sempurna. Penelitian MT-Bench mencatat bias posisi, "
        "verbosity dan self-enhancement serta keterbatasan reasoning. Di sini oracle SQL "
        "dan penilaian jawaban dipisahkan, dan audit kedua tidak dianggap pengganti evaluasi "
        "manusia. [Zheng et al.](https://arxiv.org/abs/2306.05685).",
        "SQL dinilai terpisah dari jawaban: hasil query harus cocok dengan seluruh oracle "
        "metrik yang diminta. Comparator konservatif dapat menolak alias keluaran yang berbeda; "
        "kasus ekuivalen perlu ditinjau. Kegagalan atau missing judge tetap terlihat.",
        "## Probe JEV dan robustness",
        table(
            ["Probe", "N", "Accepted", "Primary set tepat", "Top-1 satu domain", "Lexical top-1"],
            [
                [
                    name,
                    v["attempted"],
                    v["accepted"],
                    percent(v["primary_set_accuracy"]),
                    percent(v["jev_single_domain_top_one_accuracy"]),
                    percent(v["lexical_single_domain_top_one_accuracy"]),
                ]
                for name, v in probes.items()
            ],
        ),
        table(
            [
                "Perbandingan",
                "Pasangan",
                "Pasangan valid",
                "Modal agreement",
                "Perubahan semua",
                "Perubahan pada pasangan valid",
            ],
            [
                [
                    name,
                    v["paired_cases"],
                    v["valid_paired_cases"],
                    percent(v["modal_agreement"]),
                    percent(v["change_rate"]),
                    percent(v["valid_change_rate"]),
                ]
                for name, v in comparisons.items()
            ],
        ),
        "Primary set tepat mensyaratkan respons accepted dan himpunan primary persis sesuai "
        "domain yang dibutuhkan. Top-1 menghitung urutan keluaran hook, termasuk fallback lexical; "
        "keduanya memiliki denominator berbeda. Error tidak dianggap keputusan stabil. Confidence "
        "JEV adalah skor relevansi per kandidat, bukan probabilitas terkalibrasi untuk routing "
        "lima kelas. Bins dan setiap raw response tersedia dalam analysis.json dan probe-*.jsonl.",
        "Dokumentasi TypeSafe menjelaskan confidence sebagai statistik dari distribusi "
        "probabilitas pilihan. Threshold perlu diuji pada domain sendiri. Karena keputusan "
        "Nova berbentuk relevansi per agent, confidence itu tidak dapat dibaca sebagai "
        "jaminan kebenaran jawaban bisnis. "
        "[TypeSafe confidence](https://docs.typesafe.ai/confidence).",
        "Perubahan pada pasangan valid dihitung hanya jika semua run menghasilkan keputusan "
        "yang dapat dibandingkan. Selisih termasuk timeout/fallback tidak boleh langsung "
        "ditafsirkan sebagai positional bias. Kedua ukuran dilaporkan agar kegagalan layanan "
        "tetap terlihat tanpa mengacaukannya dengan perubahan pilihan semantik.",
        "Sales sudah dibuat saat fixture preparation, tetapi dikeluarkan dari kandidat utama. "
        "Agent ini baru dimasukkan ke candidate set pada extension setelah 200 kasus utama "
        "terekam. Tidak ada retraining atau aturan khusus untuk menyingkirkan Sales.",
        "# H. Before vs After",
        "Perbandingan berikut memakai pertanyaan DEV terpilih yang sama, setelah perbaikan "
        "arsitektur. Ini ablation JEV dengan answer model yang sama, bukan eksperimen kausal "
        "terkontrol di mesin terpisah.",
        table(
            ["Metrik", "Tanpa JEV", "JEV, model berat tetap", "Delta pp"],
            [
                [
                    label,
                    percent(baseline.get(key)),
                    percent(controlled.get(key)),
                    "Belum terukur"
                    if baseline.get(key) is None or controlled.get(key) is None
                    else f"{100 * (controlled[key] - baseline[key]):+.2f}",
                ]
                for label, key in [
                    ("Exact routing", "routing_accuracy"),
                    ("SQL oracle", "execution_oracle_accuracy"),
                    ("Jawaban / 3", "answer_normalized_accuracy"),
                    ("Completion", "completion_rate"),
                ]
            ],
        ),
        table(
            [
                "Kategori pasangan DEV",
                "N off / on",
                "Routing off",
                "Routing on",
                "Jawaban off / 3",
                "Jawaban on / 3",
            ],
            [
                [
                    category,
                    f"{before.get('n', 0)} / {after.get('n', 0)}",
                    percent(before.get("routing_accuracy")),
                    percent(after.get("routing_accuracy")),
                    percent(before.get("answer_normalized_accuracy")),
                    percent(after.get("answer_normalized_accuracy")),
                ]
                for category in sorted(
                    baseline.get("categories", {}).keys() | controlled.get("categories", {}).keys()
                )
                for before, after in [
                    (
                        baseline.get("categories", {}).get(category, {}),
                        controlled.get("categories", {}).get(category, {}),
                    )
                ]
            ],
        ),
        "Subset pasangan berjumlah 12 pertanyaan dengan 1 atau 2 kasus per kategori. "
        "Persentase pada kategori kecil merupakan deskripsi sampel, bukan estimasi presisi "
        "untuk domain bisnis. Selisih latency juga dapat dipengaruhi beban layanan sepanjang run.",
        "# I. Confusion Matrix",
        "Baseline off (subset DEV):",
        matrix(baseline),
        "Controlled-on (subset DEV yang sama):",
        matrix(controlled),
        "Final normal-on (seluruh cohort utama yang terekam):",
        matrix(primary),
        "ERROR berarti belum ada delegasi atau jawaban yang dapat diklasifikasikan; OTHER "
        "mencakup himpunan agent di luar dua domain utama. Angka matriks adalah jumlah kasus.",
        "# J. Latency",
        table(
            [
                "Arm",
                "N",
                "End-to-end p50 s",
                "p95 s",
                "Max s",
                "JEV p50 s",
                "JEV p95 s",
                "JEV max s",
            ],
            [
                [
                    name,
                    v["attempted"],
                    number(v["latency_seconds"]["p50"]),
                    number(v["latency_seconds"]["p95"]),
                    number(v["latency_seconds"]["max"]),
                    number(v["jev_latency_seconds"]["p50"]),
                    number(v["jev_latency_seconds"]["p95"]),
                    number(v["jev_latency_seconds"]["max"]),
                ]
                for name, v in smart.items()
                if name in required
            ],
        ),
        table(
            ["Komponen pada cohort utama", "p50 s", "p95 s", "Max s"],
            [
                [name, *(number(values.get(key)) for key in ["p50", "p95", "max"])]
                for name, values in [
                    ("Query bisnis (hasil non-kosong)", primary.get("query_latency_seconds", {})),
                    (
                        "Startup specialist",
                        primary.get("agent_latency", {}).get("startup_seconds", {}),
                    ),
                    (
                        "Eksekusi specialist",
                        primary.get("agent_latency", {}).get("execution_seconds", {}),
                    ),
                ]
            ],
        ),
        table(
            ["Arm", "Orchestration input", "Orchestration output", "JEV input", "JEV output"],
            [
                [
                    name,
                    value["tokens"]["prompt_tokens"],
                    value["tokens"]["completion_tokens"],
                    value["jev_tokens"]["input_tokens"],
                    value["jev_tokens"]["output_tokens"],
                ]
                for name, value in smart.items()
                if name in required
            ],
        ),
        "Waktu pembersihan setelah timeout dapat membuat latency tercatat melebihi 240 detik. "
        "Statistik startup/execution specialist, query bisnis, token orchestration dan token "
        "JEV ada di analysis.json. Token yang tidak dikembalikan provider tidak diperkirakan; "
        "jumlah ini bukan pengukuran biaya tagihan. Timeout juga dapat membuat token yang "
        "belum tersimpan di checkpoint tidak terhitung.",
        "# K. Remaining Failure Cases",
        "Setiap kasus yang tidak selesai, routing salah, SQL tidak cocok, atau skor jawaban "
        "di bawah 3 dicantumkan dalam [remaining-failures.md](remaining-failures.md) dan CSV "
        "pertanyaan lengkap. Raw trajectory dan judgment tetap tersedia, termasuk pilot "
        "serta percobaan infrastruktur. Kasus belum dijalankan tercantum pada coverage dan "
        "missing_case_ids, bukan disamarkan menjadi hasil model.",
        "Error perantara yang berhasil dipulihkan tetap tercatat di "
        "[observed-errors.json](observed-errors.json). Kasus dengan fallback JEV yang akhirnya "
        "menghasilkan routing, SQL dan jawaban lengkap tidak disebut kegagalan tersisa.",
        "Kandidat critical failure untuk audit manual: "
        f"{primary.get('critical_failure_candidates', [])}. "
        "Daftar otomatis ini tidak cukup untuk menyatakan critical failure nol; tinjauan "
        "jawaban-domain dan bukti harus dibaca bersama manual-review.json.",
        "# L. Regression Tests Added",
        "Tests berada pada backend/tests/unit/test_jev_multidomain_benchmark.py, "
        "test_semantic_qualified_catalog.py, test_system_pool_cancellation.py, serta "
        "backend/tests/eval/test_smart_bounded_history.py dan test_smart_data_routing.py. "
        "Cakupan meliputi freeze/split tanpa leakage, ratio/snapshot oracle, query compiler, "
        "permission scoping, redaction, outage accounting, bounded history dan hasil tool. "
        "Seluruh kategori bisnis tetap tersedia sebagai regression live yang membutuhkan "
        "provider; scripted tests tidak diklaim mengukur kemampuan semantik model nyata.",
        "Perintah dan output regression dicatat dalam regression-command.json dan "
        "verification.json. Scorecard trajectory, lint, dan probe cancellation dilaporkan "
        "terpisah. Tidak ada perubahan UI dalam putaran benchmark ini.",
        "# M. Final Assessment",
        "Status acceptance harus dibaca dari tabel G. Ranking agent yang tinggi sendiri "
        "belum membuktikan Smart dapat mengklarifikasi, menggabungkan evidence lintas domain, "
        "atau menghasilkan jawaban lengkap. Perbandingan controlled-on/off, holdout, oracle "
        "dan audit manual menjadi dasar penilaian akhir. Tidak ada klaim bahwa semua target "
        "tercapai jika coverage atau salah satu gate masih belum lengkap.",
        "Arah tindak lanjut yang dapat diuji pada cohort baru: kontrak keputusan yang secara "
        "eksplisit mencakup clarification dan multi-domain; representasi metrik yang ringkas "
        "untuk JEV; validasi kontrak planner sebelum memilih model ringan; pelestarian unit, "
        "periode dan definisi dalam sintesis berbasis bukti; serta pengujian worker daemon "
        "dengan resource isolation. Ini rekomendasi, bukan perubahan yang diam-diam "
        "dimasukkan setelah melihat holdout.",
        "Batas model ini konsisten dengan dokumentasi Jev 1.13: reasoning bertingkat, "
        "konten adversarial, konteks tidak relevan, aritmetika dan perbandingan tanggal "
        "memerlukan perhatian khusus. Perhitungan dan otorisasi tetap berada dalam kode "
        "dan SQL. [Batas Jev 1.13](https://docs.typesafe.ai/model-jaggedness/jev-1.13).",
    ]
    failures = []
    for name, summary in smart.items():
        for row in summary["cases"]:
            if row["unresolved"]:
                failures.append(
                    {
                        "experiment": name,
                        "case_id": row["case_id"],
                        "question": all_cases[row["case_id"]]["question"],
                        "expected": row["expected"],
                        "predicted": row["predicted"],
                        "status": row["status"],
                        "score": row["score"],
                        "sql_correct": row["execution"]["correct"],
                        "errors": ", ".join(row["errors"]),
                        "judge_reason": row["judge_reason"],
                    }
                )
    if failures:
        with (ARTIFACTS / "remaining-failures.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(failures[0]))
            writer.writeheader()
            writer.writerows(failures)
    (ARTIFACTS / "observed-errors.json").write_text(
        json.dumps(
            [
                {"experiment": name, **row}
                for name, summary in smart.items()
                for row in summary["cases"]
                if row["errors"]
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    (ARTIFACTS / "remaining-failures.md").write_text(
        "# Remaining measured failures\n\nAll measured failures, including diagnostic pilots. "
        "Incomplete coverage is reported separately. Full questions and judge reasons are "
        "in [CSV](remaining-failures.csv).\n\n"
        + table(
            ["Experiment", "Case", "Expected", "Actual", "Status", "Score", "SQL", "Errors"],
            [
                [
                    r[k]
                    for k in [
                        "experiment",
                        "case_id",
                        "expected",
                        "predicted",
                        "status",
                        "score",
                        "sql_correct",
                        "errors",
                    ]
                ]
                for r in failures
            ],
        )
        + "\n"
    )
    (ARTIFACTS / "report.md").write_text("\n\n".join(sections) + "\n")
    return {"complete": complete, "measured_failure_rows": len(failures), "coverage": coverage}


if __name__ == "__main__":
    print(json.dumps(render(), indent=2))
