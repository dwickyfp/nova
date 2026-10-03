"""Teaching interactions only; this module never imports evaluator cases or SQL."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TeachingRule:
    key: str
    domain: str
    fact: str
    statement_id: str
    statement_en: str
    correction: str
    metric: str | None = None


RULES = (
    TeachingRule(
        "net_booked_revenue",
        "Finance",
        (
            "Net booked revenue sums gross_amount less posted refund_amount "
            "for completed or fulfilled orders."
        ),
        (
            "Di perusahaan kita, net booked revenue menjumlahkan nilai bruto "
            "dikurangi refund yang sudah diposting, hanya untuk pesanan "
            "completed atau fulfilled."
        ),
        (
            "Our net booked revenue includes completed and fulfilled orders, "
            "subtracts posted refunds, and excludes every other order status."
        ),
        (
            "Koreksi: refund yang masih diminta belum mengurangi pendapatan; "
            "hanya refund posted yang dikurangkan."
        ),
        "net_booked_revenue",
    ),
    TeachingRule(
        "healthy_order",
        "Operations",
        (
            "A healthy order has status completed or fulfilled; fraud_review "
            "and cancelled orders are excluded."
        ),
        (
            "Pesanan sehat di sini hanya completed atau fulfilled. Status "
            "fraud_review dan cancelled tidak termasuk."
        ),
        (
            "Our healthy-order rule accepts completed or fulfilled, and never "
            "accepts fraud_review or cancelled."
        ),
        "Koreksi: pemeriksaan fraud yang belum selesai bukan pesanan sehat.",
        "healthy_orders",
    ),
    TeachingRule(
        "qualified_checkout",
        "Marketing",
        (
            "Qualified checkout conversion divides converted shipping-selected "
            "sessions by all shipping-selected sessions."
        ),
        (
            "Checkout berkualitas dimulai saat metode pengiriman dipilih. "
            "Pengunjung yang berhenti di keranjang tidak masuk denominator "
            "konversi checkout."
        ),
        (
            "We measure qualified checkout conversion only among sessions with "
            "a selected shipping method, including their failed conversions."
        ),
        (
            "Koreksi: denominator checkout mencakup semua yang memilih "
            "pengiriman, bukan hanya pembayaran berhasil."
        ),
        "qualified_checkout_conversion",
    ),
    TeachingRule(
        "vip_growth",
        "Finance",
        (
            "VIP Growth requires trailing-90-day net booked revenue above IDR "
            "1500000 and at least 5 healthy orders per customer."
        ),
        (
            "VIP Growth berarti pelanggan dengan net booked revenue 90 hari "
            "terakhir di atas Rp1.500.000 dan sedikitnya lima pesanan sehat."
        ),
        (
            "Our VIP Growth customers exceed IDR 1,500,000 in trailing-90-day "
            "net booked revenue and have at least five healthy orders."
        ),
        (
            "Koreksi: lima pesanan saja belum cukup untuk VIP Growth; nilai "
            "net booked revenue juga harus lebih dari Rp1.500.000."
        ),
    ),
    TeachingRule(
        "red_zone_stockout",
        "Operations",
        (
            "Red-zone stockout means sellable coverage below 2 days for SKUs "
            "with daily demand of at least 20 units."
        ),
        (
            "Stok zona merah adalah SKU dengan permintaan minimal 20 unit "
            "sehari dan cakupan stok layak jual kurang dari dua hari."
        ),
        (
            "A SKU enters our red zone when demand is at least twenty units "
            "per day and sellable stock covers less than two days."
        ),
        (
            "Koreksi: stok rendah untuk SKU berpermintaan lima unit sehari "
            "tidak memenuhi aturan zona merah."
        ),
        "red_zone_skus",
    ),
    TeachingRule(
        "margin_safe_campaign",
        "Marketing",
        (
            "A margin-safe campaign requires incremental gross profit greater "
            "than incremental spend; positive ROAS alone is insufficient."
        ),
        (
            "Campaign margin-safe mensyaratkan tambahan laba kotor lebih besar "
            "dari tambahan belanja. ROAS positif saja belum cukup."
        ),
        (
            "We call a campaign margin-safe only when incremental gross profit "
            "exceeds incremental spend."
        ),
        "Koreksi: pertumbuhan revenue dengan ROAS positif tidak otomatis aman untuk margin.",
    ),
    TeachingRule(
        "recoverable_payment",
        "Operations",
        (
            "Recoverable payment failures are payment_timeout events; hard "
            "declines and risk_review blocks are excluded."
        ),
        (
            "Kegagalan pembayaran yang bisa dipulihkan hanya payment_timeout. "
            "Hard decline dan blokir fraud tidak termasuk."
        ),
        (
            "Our recoverable payment failures are timeouts, excluding hard "
            "declines and fraud or risk-review blocks."
        ),
        (
            "Koreksi: payment_hard_decline tidak boleh dianggap sebagai "
            "peluang retry yang dapat dipulihkan."
        ),
        "recoverable_payment_failures",
    ),
    TeachingRule(
        "revenue_authority",
        "Executive",
        (
            "Finance owns the canonical revenue definition; Marketing gross "
            "order value is a different metric."
        ),
        (
            "Finance adalah pemilik definisi revenue resmi. Nilai pesanan "
            "bruto milik Marketing merupakan metrik yang berbeda."
        ),
        (
            "Finance owns canonical business revenue even when Marketing has a "
            "similarly named gross-value metric."
        ),
        (
            "Koreksi: istilah revenue yang dipakai Marketing tidak "
            "menggantikan definisi resmi Finance."
        ),
        "net_booked_revenue",
    ),
    TeachingRule(
        "jakarta_alias",
        "Operations",
        (
            "The internal geography alias JKT Core means city Jakarta, "
            "excluding Bandung, Surabaya and Medan."
        ),
        "Kode internal JKT Core merujuk city Jakarta saja, tanpa Bandung, Surabaya, dan Medan.",
        "Within our governed geography, JKT Core maps precisely to city Jakarta.",
        "Koreksi: JKT Core tidak mencakup Bandung meskipun keduanya ada di Pulau Jawa.",
    ),
    TeachingRule(
        "active_customer",
        "Operations",
        (
            "An active customer has at least one healthy order in the 45 days "
            "before the observation cutoff."
        ),
        (
            "Pelanggan aktif di perusahaan kita memiliki setidaknya satu "
            "pesanan sehat dalam 45 hari sebelum cutoff observasi."
        ),
        (
            "We classify a customer as active after at least one healthy order "
            "during the trailing forty-five days."
        ),
        "Koreksi: active customer memakai jendela 45 hari, bukan sekadar bulan kalender berjalan.",
        "active_customers",
    ),
)


@dataclass(frozen=True)
class Interaction:
    id: str
    episode: int
    category: str
    domain: str
    content: str
    rule_key: str | None = None
    like: bool = False


def interactions() -> list[Interaction]:
    output = []
    for index, rule in enumerate(RULES):
        prompts = (
            ("definition", rule.statement_id, False),
            ("confirmation", rule.statement_en, False),
            ("correction", rule.correction, False),
            ("confirmation", f"Konfirmasi definisi lengkap: {rule.fact}", False),
            (
                "semantic_use",
                (
                    f"Hitung {rule.metric or 'order_count'} dari 2026-01-{index + 1:02} "
                    f"sampai sebelum 2026-01-{index + 8:02} untuk review {rule.key}."
                ),
                True,
            ),
            (
                "cross_domain",
                (
                    f"How would the rule for {rule.key.replace('_', ' ')} affect an investigation "
                    "combining orders, stock, payments and support? "
                    "Separate associations from causes."
                ),
                False,
            ),
            (
                "decision",
                (
                    "Saat mempertimbangkan inventory, discount, atau spend, apa asumsi dan "
                    f"batas bukti yang perlu dicatat terkait {rule.key.replace('_', ' ')}?"
                ),
                False,
            ),
            (
                "outcome",
                (
                    "Explain how to compare a recorded prediction and observed outcome for "
                    f"{rule.key.replace('_', ' ')} when no randomized control exists. "
                    "Do not invent an outcome."
                ),
                False,
            ),
        )
        for episode, (category, content, like) in enumerate(prompts, 1):
            output.append(
                Interaction(
                    f"L{index + 1:02}-{episode}",
                    episode,
                    category,
                    rule.domain,
                    content,
                    rule.key if episode <= 4 else None,
                    like,
                )
            )
    return sorted(output, key=lambda row: (row.episode, row.id))


def export_learning() -> list[dict]:
    return [asdict(row) for row in interactions()]
