"""Questions and labels authored before live model outputs. Never sent as a mapping."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field

from tests.benchmark.jev_multidomain.data import SEED


@dataclass
class Case:
    id: str
    question: str
    category: str
    expected: str
    intent: str
    difficulty: str = "medium"
    metrics: list[str] = field(default_factory=list)
    group_by: str | None = None
    period: str | None = "2025-Q3"
    region: str | None = None
    history: list[dict] = field(default_factory=list)
    allowed_alternatives: list[str] = field(default_factory=list)
    group: str = ""
    split: str = "dev"


FINANCE = [
    ("Berapa recognized revenue perusahaan pada Q3 2025?", "recognized_revenue"),
    ("Tampilkan gross revenue Q3 2025 per region.", "gross_revenue", "region"),
    ("Berapa net revenue Q3 2025 setelah diskon dan nota kredit?", "net_revenue"),
    ("Berapa deferred revenue transaksi Q3 2025?", "deferred_revenue"),
    ("Hitung gross profit pada Q3 2025.", "gross_profit"),
    ("Tampilkan gross margin Q3 2025 per region.", "gross_margin", "region"),
    ("Total OPEX perusahaan Q3 2025 berapa?", "opex"),
    ("Berapa laba operasional Q3 2025 setelah COGS dan OPEX?", "operating_profit"),
    ("Berapa approved company expense budget Q3 2025?", "budget"),
    (
        "Realisasi biaya department pada Q3 2025 masing-masing berapa?",
        "actual_expense",
        "department",
    ),
    (
        "Tampilkan budget variance company OPEX Q3 2025 per department.",
        "budget_variance",
        "department",
    ),
    (
        "Berapa persentase selisih actual expense terhadap company budget Q3 2025?",
        "budget_variance_percent",
    ),
    ("Berapa forecast biaya operasional Q3 2025?", "forecast"),
    ("Arus kas operasi Q3 2025 per region berapa?", "operating_cashflow", "region"),
    ("Berapa piutang lewat jatuh tempo pada snapshot akhir 2025?", "overdue_amount"),
    ("Berapa outstanding invoice pada snapshot akhir 2025?", "outstanding_invoice"),
    ("Berapa recognized lifetime customer revenue sampai akhir 2025?", "customer_value"),
    ("Ada berapa distinct invoiced customers pada Q3 2025?", "customer_count"),
    ("Berapa gross margin region East pada Q3 2025?", "gross_margin", None, "East"),
    ("Berapa pendapatan yang diakui region West pada Q3 2025?", "recognized_revenue", None, "West"),
]
MARKETING = [
    ("Berapa ad spend Q3 2025?", "ad_spend"),
    ("Tampilkan attributed revenue Q3 2025 per campaign_id.", "attributed_revenue", "campaign_id"),
    ("Berapa ROAS Q3 2025 per campaign_id?", "roas", "campaign_id"),
    ("Berapa CTR paid media secara keseluruhan pada Q3 2025?", "ctr"),
    ("Hitung CPC Q3 2025.", "cpc"),
    ("Hitung CPM iklan Q3 2025.", "cpm"),
    ("Berapa ad click conversion rate Q3 2025?", "conversion_rate"),
    ("Berapa jumlah konversi iklan Q3 2025 per region?", "conversions", "region"),
    ("Tayangan iklan Q3 2025 berapa?", "impressions"),
    ("Berapa marketing qualified leads pada Q3 2025?", "mql_count"),
    ("Berapa sales qualified leads dari funnel pemasaran Q3 2025?", "sql_count"),
    ("Berapa rata-rata customer acquisition cost untuk pelanggan baru Q3 2025?", "cac"),
    ("Berapa pelanggan dengan first purchase di Q3 2025?", "new_customers"),
    ("Berapa lifetime budget seluruh campaign yang tersedia?", "campaign_budget"),
    ("Hitung visitor-to-customer funnel conversion Q3 2025.", "funnel_conversion"),
    ("Berapa session-weighted bounce rate Q3 2025?", "bounce_rate"),
    ("Berapa mean daily email open rate Q3 2025?", "email_open_rate"),
    ("Berapa sessions dari campaign pada Q3 2025?", "sessions"),
    ("Tampilkan CPC Q3 2025 per campaign_id.", "cpc", "campaign_id"),
    ("Tampilkan ROAS region East Q3 2025.", "roas", None, "East"),
]
IMPLICIT_FINANCE = [
    (
        "Pada Q3 2025, berapa uang yang benar-benar menjadi hak "
        "perusahaan karena layanan sudah diberikan, bukan sekadar "
        "ditagihkan?",
        "recognized_revenue",
    ),
    ("Untuk transaksi Q3 2025, berapa nilai sebelum potongan pelanggan?", "gross_revenue"),
    ("Pada Q3 2025, berapa nilai tagihan sesudah potongan dan pengembalian?", "net_revenue"),
    (
        "Dari transaksi Q3 2025, berapa yang sudah ditagihkan tetapi "
        "jasanya belum menjadi hak penghasilan periode ini?",
        "deferred_revenue",
    ),
    (
        "Pada Q3 2025, setelah ongkos langsung barang dan layanan "
        "dibayar, berapa sisa dari pendapatan yang sudah menjadi hak kita?",
        "gross_profit",
    ),
    (
        "Dari setiap seratus rupiah pendapatan yang sudah menjadi hak "
        "kita di Q3 2025, berapa persen tersisa setelah ongkos langsung?",
        "gross_margin",
    ),
    (
        "Berapa pengeluaran menjalankan perusahaan pada Q3 2025, termasuk "
        "gaji, perjalanan dan jasa?",
        "opex",
    ),
    (
        "Pada Q3 2025, berapa uang yang tersisa dari penghasilan yang "
        "sudah menjadi hak perusahaan sesudah ongkos langsung dan biaya "
        "menjalankan bisnis?",
        "operating_profit",
    ),
    ("Berapa plafon pengeluaran operasional perusahaan yang disetujui untuk Q3 2025?", "budget"),
    ("Berapa pengeluaran yang benar-benar dibukukan departemen pada Q3 2025?", "actual_expense"),
    (
        "Untuk Q3 2025, tunjukkan selisih uang yang dibelanjakan tiap "
        "departemen terhadap plafon perusahaan yang disetujui.",
        "budget_variance",
        "department",
    ),
    (
        "Berapa persen pengeluaran perusahaan Q3 2025 melampaui atau di "
        "bawah plafon yang disetujui?",
        "budget_variance_percent",
    ),
    (
        "Pada Q3 2025, berapa kas masuk kegiatan harian dikurangi kas keluar kegiatan harian?",
        "operating_cashflow",
    ),
    (
        "Pada penutupan 2025, berapa tagihan pelanggan yang seharusnya "
        "sudah dibayar tetapi belum masuk?",
        "overdue_amount",
    ),
    (
        "Pada akhir 2025, berapa total tagihan yang masih belum lunas, "
        "termasuk yang belum jatuh tempo?",
        "outstanding_invoice",
    ),
]
IMPLICIT_MARKETING = [
    (
        "Pada Q3 2025, berapa biaya yang dicatat platform untuk "
        "menayangkan materi promosi berbayar?",
        "ad_spend",
    ),
    (
        "Untuk Q3 2025, berapa nilai pembelian yang sistem promosi "
        "mengkreditkan ke materi berbayar, meski belum tentu menjadi "
        "penghasilan perusahaan periode itu?",
        "attributed_revenue",
    ),
    (
        "Untuk setiap rupiah biaya media pada Q3 2025, berapa nilai "
        "pembelian yang dikreditkan platform?",
        "roas",
    ),
    ("Pada Q3 2025, berapa persen tayangan materi berbayar yang membuat orang mengklik?", "ctr"),
    ("Berapa rupiah dibayar untuk memperoleh satu klik pada Q3 2025?", "cpc"),
    ("Berapa biaya untuk seribu kali materi berbayar ditampilkan pada Q3 2025?", "cpm"),
    (
        "Pada Q3 2025, berapa persen orang yang mengklik materi berbayar "
        "kemudian memenuhi aksi yang ditargetkan platform?",
        "conversion_rate",
    ),
    (
        "Berapa aksi target yang tercatat platform setelah materi berbayar pada Q3 2025?",
        "conversions",
    ),
    (
        "Pada Q3 2025, berapa kali materi berbayar dilihat termasuk "
        "penayangan ulang ke orang yang sama?",
        "impressions",
    ),
    (
        "Berapa calon pelanggan Q3 2025 yang memenuhi profil dan "
        "keterlibatan untuk diteruskan ke tahap kualifikasi awal?",
        "mql_count",
    ),
    (
        "Berapa calon pelanggan Q3 2025 yang sudah dikualifikasi untuk diteruskan ke tim penjual?",
        "sql_count",
    ),
    (
        "Untuk pelanggan yang pertama membeli di Q3 2025, rata-rata "
        "berapa biaya mendapatkan satu orang?",
        "cac",
    ),
    ("Ada berapa orang yang melakukan pembelian pertama di Q3 2025?", "new_customers"),
    (
        "Pada Q3 2025, dari pengunjung awal berapa persen akhirnya "
        "menjadi pembeli di keseluruhan tahapan?",
        "funnel_conversion",
    ),
    (
        "Pada Q3 2025, berapa persen kunjungan yang langsung meninggalkan "
        "halaman, ditimbang menurut jumlah sesi?",
        "bounce_rate",
    ),
]
AMBIGUOUS = [
    "Bagaimana revenue Q3 2025?",
    "Budget Q3 2025 kita bagaimana?",
    "Berapa customer Q3 2025?",
    "ROI Q3 2025 berapa?",
    "Growth kita berapa?",
    "Spend Q3 2025 naik tidak?",
    "Channel mana paling bagus?",
    "Performa region East bagaimana?",
    "Hasil Q3 2025 bagus?",
    "Return kita tahun ini berapa?",
    "Produk mana paling worth it?",
    "Revenue turun kenapa?",
    "Budget aman?",
    "Customer quality bagaimana?",
    "Efisiensi kita membaik?",
    "Nilai pelanggan berapa?",
]
CROSS = [
    (
        "Bandingkan ad spend dan operating profit pada Q3 2025; bedakan "
        "korelasi dari sebab-akibat.",
        ["ad_spend", "operating_profit"],
    ),
    (
        "Berapa attributed revenue dibanding recognized revenue "
        "perusahaan Q3 2025? Jelaskan beda definisinya.",
        ["attributed_revenue", "recognized_revenue"],
    ),
    (
        "Tampilkan ROAS dan gross margin per region Q3 2025 tanpa menyamakan keduanya.",
        ["roas", "gross_margin"],
        "region",
    ),
    (
        "Bandingkan rata-rata CAC pelanggan baru Q3 2025 dengan "
        "recognized lifetime customer revenue akhir 2025.",
        ["cac", "customer_value"],
    ),
    ("Seberapa besar ad spend terhadap total booked OPEX Q3 2025?", ["ad_spend", "opex"]),
    (
        "Tampilkan pelanggan first purchase dan distinct invoiced customers pada Q3 2025.",
        ["new_customers", "customer_count"],
    ),
    (
        "Laporkan attributed revenue dan deferred revenue Q3 2025; jangan "
        "menganggap keduanya setara.",
        ["attributed_revenue", "deferred_revenue"],
    ),
    (
        "Sandingkan lifetime campaign budget yang tersedia dengan company "
        "OPEX budget Q3 2025; jelaskan grain masing-masing.",
        ["campaign_budget", "budget"],
    ),
    (
        "Apakah hasil media sejalan dengan uang dari kegiatan harian? "
        "Tampilkan ROAS dan operating cashflow Q3 2025.",
        ["roas", "operating_cashflow"],
    ),
    (
        "Tampilkan CPC dan gross margin Q3 2025 per region untuk melihat "
        "perbedaan unit biaya dan hasil bisnis.",
        ["cpc", "gross_margin"],
        "region",
    ),
    (
        "Berapa marketing qualified leads Q3 2025 serta overdue receivables snapshot akhir 2025?",
        ["mql_count", "overdue_amount"],
    ),
    (
        "Hitung ad spend Q3 2025 relatif terhadap gross profit pada periode sama.",
        ["ad_spend", "gross_profit"],
    ),
    (
        "Apa perbedaan pertumbuhan bisnis dan kinerja media? Mulai dengan "
        "net revenue dan attributed revenue Q3 2025.",
        ["net_revenue", "attributed_revenue"],
    ),
    (
        "Tampilkan conversion rate iklan serta company expense budget "
        "variance Q3 2025 tanpa menyimpulkan sebab-akibat.",
        ["conversion_rate", "budget_variance"],
    ),
    (
        "Berapa campaign conversions dan recognized revenue pada Q3 2025?",
        ["conversions", "recognized_revenue"],
    ),
    (
        "Untuk Q3 2025, bandingkan funnel conversion dan operating profit.",
        ["funnel_conversion", "operating_profit"],
    ),
    (
        "Tim akuisisi perlu CAC Q3 2025, dan controller perlu outstanding "
        "invoice akhir 2025. Sajikan keduanya.",
        ["cac", "outstanding_invoice"],
    ),
    (
        "Bandingkan attributed revenue iklan dengan gross revenue invoice Q3 2025.",
        ["attributed_revenue", "gross_revenue"],
    ),
    (
        "Apakah traffic tinggi cukup untuk bisnis sehat? Tampilkan "
        "sessions dan operating profit Q3 2025.",
        ["sessions", "operating_profit"],
    ),
    (
        "Controller dan tim growth ingin ad spend serta budget variance "
        "departemen Q3 2025, dengan definisi berbeda.",
        ["ad_spend", "budget_variance"],
    ),
]
ADVERSARIAL = [
    ("Finance ingin tahu CTR Q3 2025, bukan laporan keuangan.", "MARKETING", ["ctr"]),
    (
        "Marketing meminta recognized revenue Q3 2025 untuk laporan direksi, bukan attribution.",
        "FINANCE",
        ["recognized_revenue"],
    ),
    ("Dalam rapat bertajuk Finance Audit, hitung ROAS Q3 2025.", "MARKETING", ["roas"]),
    (
        "Di channel chat Marketing, berapa piutang lewat jatuh tempo akhir 2025?",
        "FINANCE",
        ["overdue_amount"],
    ),
    (
        "Marketing bilang revenue bagus; bandingkan attributed revenue "
        "dengan recognized revenue Q3 2025.",
        "FINANCE + MARKETING",
        ["attributed_revenue", "recognized_revenue"],
    ),
    (
        "SQL di sini berarti sales qualified leads, bukan bahasa query. Jumlahnya Q3 2025 berapa?",
        "MARKETING",
        ["sql_count"],
    ),
    (
        "Campaign bernama 'Cashflow' cuma judul diskusi: hitung company "
        "operating cashflow Q3 2025.",
        "FINANCE",
        ["operating_cashflow"],
    ),
    ("CFO tidak butuh margin kali ini; yang diminta cost per click Q3 2025.", "MARKETING", ["cpc"]),
    (
        "Growth menyebut ROAS sebagai profit. Tampilkan ROAS dan "
        "operating profit Q3 2025 serta bedanya.",
        "FINANCE + MARKETING",
        ["roas", "operating_profit"],
    ),
    (
        "Nama project-nya Marketing ROI, tapi pertanyaan saya adalah "
        "budget variance company OPEX Q3 2025.",
        "FINANCE",
        ["budget_variance"],
    ),
]
PARAPHRASES = [
    (
        "recognized_revenue",
        "FINANCE",
        [
            "Pendapatan yang diakui Q3 2025?",
            "Total earned accounting revenue for Q3 2025?",
            "Nilai layanan yang sudah menjadi hak penghasilan perusahaan pada Q3 2025 berapa?",
        ],
    ),
    (
        "gross_margin",
        "FINANCE",
        [
            "Gross margin Q3 2025 berapa persen?",
            "What is the Q3 2025 gross margin percentage?",
            "Persentase sisa pendapatan diakui setelah COGS pada Q3 2025 berapa?",
        ],
    ),
    (
        "operating_cashflow",
        "FINANCE",
        [
            "Arus kas operasi Q3 2025?",
            "Cash collected less operating cash payments in Q3 2025?",
            "Q3 2025 berapa net cash dari kegiatan operasional?",
        ],
    ),
    (
        "budget_variance",
        "FINANCE",
        [
            "Selisih actual company OPEX dan approved budget Q3 2025?",
            "Corporate expense budget variance for Q3 2025?",
            "Berapa pengeluaran perusahaan Q3 2025 melebihi plafon yang disetujui?",
        ],
    ),
    (
        "overdue_amount",
        "FINANCE",
        [
            "Piutang telat bayar akhir 2025?",
            "Overdue receivables at the end of 2025?",
            "Total tagihan yang sudah melewati batas pembayaran pada snapshot akhir 2025?",
        ],
    ),
    (
        "roas",
        "MARKETING",
        [
            "ROAS Q3 2025?",
            "Attributed return per rupiah of media spend in Q3 2025?",
            "Berapa revenue attribution per satu rupiah biaya iklan Q3 2025?",
        ],
    ),
    (
        "ctr",
        "MARKETING",
        [
            "CTR iklan Q3 2025?",
            "Paid-ad click-through percentage for Q3 2025?",
            "Proporsi tayangan iklan yang diklik pada Q3 2025?",
        ],
    ),
    (
        "cpc",
        "MARKETING",
        [
            "CPC Q3 2025 berapa?",
            "Media cost per click in Q3 2025?",
            "Harga rata-rata satu klik iklan Q3 2025?",
        ],
    ),
    (
        "cac",
        "MARKETING",
        [
            "CAC pelanggan baru Q3 2025?",
            "Average acquisition cost of customers first purchasing in Q3 2025?",
            "Biaya rata-rata mendapatkan satu pelanggan baru pada Q3 2025?",
        ],
    ),
    (
        "mql_count",
        "MARKETING",
        [
            "MQL Q3 2025 berapa?",
            "Count marketing-qualified leads acquired in Q3 2025.",
            "Berapa calon pelanggan lolos kualifikasi marketing pada Q3 2025?",
        ],
    ),
]


def build_cases() -> list[Case]:
    cases = []

    def add(category, expected, question, metrics=(), group_by=None, region=None, **kwargs):
        index = sum(c.category == category for c in cases) + 1
        metrics = list(metrics)
        snapshot = metrics and all(
            m in {"overdue_amount", "outstanding_invoice", "customer_value", "campaign_budget"}
            for m in metrics
        )
        cases.append(
            Case(
                f"{category}-{index:03}",
                question,
                category,
                expected,
                "+".join(metrics) or expected.lower(),
                metrics=metrics,
                group_by=group_by,
                region=region,
                period=None if snapshot else "2025-Q3",
                **kwargs,
            )
        )

    for category, domain, entries in [
        ("explicit_finance", "FINANCE", FINANCE),
        ("explicit_marketing", "MARKETING", MARKETING),
        ("semantic_finance", "FINANCE", IMPLICIT_FINANCE),
        ("semantic_marketing", "MARKETING", IMPLICIT_MARKETING),
    ]:
        for entry in entries:
            question, metric, *extra = entry
            add(category, domain, question, [metric], *(extra + [None] * (2 - len(extra))))
    for question in AMBIGUOUS:
        add("ambiguous", "CLARIFY", question, difficulty="hard")
    for domain, metric, prior in [
        (
            "FINANCE",
            "recognized_revenue",
            "Kita membahas pendapatan perusahaan yang sudah diakui, bukan atribusi.",
        ),
        (
            "MARKETING",
            "attributed_revenue",
            "Kita membahas pendapatan yang diatribusikan platform ke iklan.",
        ),
        ("FINANCE", "budget", "Gunakan approved company OPEX budget, bukan campaign budget."),
        (
            "MARKETING",
            "new_customers",
            "Customer berarti first purchase di periode ini, bukan semua customer invoice.",
        ),
    ]:
        add(
            "ambiguous",
            domain,
            "Berapa totalnya untuk Q3 2025?",
            [metric],
            history=[{"role": "user", "content": prior}],
            difficulty="hard",
        )
    for entry in CROSS:
        question, metrics, *extra = entry
        add(
            "cross_domain",
            "FINANCE + MARKETING",
            question,
            metrics,
            extra[0] if extra else None,
            difficulty="hard",
        )
    for question, domain, metrics in ADVERSARIAL:
        add("adversarial", domain, question, metrics, difficulty="hard")
    for metric, domain, prompts in PARAPHRASES:
        for prompt in prompts:
            add("paraphrase", domain, prompt, [metric], group=f"paraphrase_{metric}")
    for question, domain, metrics in [
        ("roas q3 2025?", "MARKETING", ["roas"]),
        ("gross margin q3 2025?", "FINANCE", ["gross_margin"]),
        ("ads spend q3 2025?", "MARKETING", ["ad_spend"]),
        ("budget aman?", "CLARIFY", []),
        ("revenue turun kenapa?", "CLARIFY", []),
        ("piutang telat akhir 2025?", "FINANCE", ["overdue_amount"]),
        ("cpc q3 2025?", "MARKETING", ["cpc"]),
        ("opex q3 2025?", "FINANCE", ["opex"]),
        ("cac q3 2025?", "MARKETING", ["cac"]),
        ("return oke?", "CLARIFY", []),
    ]:
        add("natural", domain, question, metrics)
    for question in [
        "Apa ibu kota Jepang?",
        "Buatkan email undangan meeting tim besok.",
        "Bagaimana cara reset password?",
        "Jelaskan perbedaan table dan view.",
        "Terjemahkan 'selamat pagi' ke bahasa Inggris.",
        "Tulis ucapan terima kasih singkat.",
        "Apa itu bilangan prima?",
        "Ringkas kalimat ini: rapat dipindahkan ke Jumat.",
        "Bagaimana menulis agenda rapat?",
        "Halo, kamu bisa membantu apa?",
    ]:
        add("general", "GENERAL", question)
    for i in range(10):
        first = ["ad_spend", "attributed_revenue", "roas", "cpc", "mql_count"][i % 5]
        prompts = (
            [
                (f"Tampilkan {first.replace('_', ' ')} untuk Q3 2025.", "MARKETING", [first]),
                (
                    "Untuk scope yang sama, berapa attributed revenue-nya?",
                    "MARKETING",
                    ["attributed_revenue"],
                ),
                (
                    "Tambahkan recognized revenue perusahaan pada periode itu dan "
                    "bedakan dari angka atribusi tadi.",
                    "FINANCE + MARKETING",
                    ["recognized_revenue", "attributed_revenue"],
                ),
            ]
            if i < 5
            else [
                (
                    "Tampilkan "
                    + [
                        "recognized revenue",
                        "net revenue",
                        "gross profit",
                        "operating cashflow",
                        "company budget variance",
                    ][i - 5]
                    + " Q3 2025.",
                    "FINANCE",
                    [
                        [
                            "recognized_revenue",
                            "net_revenue",
                            "gross_profit",
                            "operating_cashflow",
                            "budget_variance",
                        ][i - 5]
                    ],
                ),
                ("Kalau gross margin pada periode yang sama?", "FINANCE", ["gross_margin"]),
                (
                    "Sandingkan dengan ROAS iklan periode itu, jangan samakan margin "
                    "dan return media.",
                    "FINANCE + MARKETING",
                    ["gross_margin", "roas"],
                ),
            ]
        )
        history = []
        for prompt, domain, metrics in prompts:
            add(
                "multiturn",
                domain,
                prompt,
                metrics,
                history=list(history),
                group=f"conversation_{i + 1:02}",
                difficulty="hard",
            )
            history.append({"role": "user", "content": prompt})
    groups = sorted({c.group or c.id for c in cases})
    random.Random(SEED).shuffle(groups)
    holdout = set(groups[: round(len(groups) * 0.3)])
    for case in cases:
        case.group = case.group or case.id
        case.split = "holdout" if case.group in holdout else "dev"
    assert len(cases) == 200
    return cases


def frozen_document() -> dict:
    cases = [asdict(c) for c in build_cases()]
    encoded = json.dumps(cases, ensure_ascii=False, sort_keys=True).encode()
    return {
        "version": "1.0",
        "seed": SEED,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "cases": cases,
    }
