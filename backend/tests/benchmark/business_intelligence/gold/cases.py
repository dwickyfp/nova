"""Evaluator-only holdouts and scoring contracts, excluded from teaching inputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    question: str
    learning_sensitive: bool
    expected_plan: dict | None = None
    rubric: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ("answer_accuracy",)
    parameters: dict = field(default_factory=dict)


CATEGORIES = (
    "business_term",
    "canonical_metric",
    "business_rule",
    "semantic_resolution",
    "cross_domain_reasoning",
    "root_cause",
    "business_system_link",
    "forecast",
    "causal_effect",
    "decision_option",
    "policy",
    "lineage",
    "outcome",
    "confidence",
    "unknown_or_insufficient_evidence",
)

METRIC_QUESTIONS = (
    (
        "business_term",
        "net_booked_revenue",
        "orders.ordered_at",
        "orders.city",
        "Berapa omzet yang diakui",
        "Calculate the booked income after posted returns",
    ),
    (
        "canonical_metric",
        "net_booked_revenue",
        "orders.ordered_at",
        "orders.city",
        "Gunakan definisi revenue resmi Finance untuk menghitung pendapatan",
        "Report the authoritative Finance revenue",
    ),
    (
        "business_rule",
        "healthy_orders",
        "orders.ordered_at",
        "orders.city",
        "Hitung pesanan sehat menurut aturan perusahaan",
        "Count eligible healthy purchases under our company rule",
    ),
    (
        "semantic_resolution",
        "qualified_checkout_conversion",
        "checkout_events.occurred_at",
        "checkout_events.city",
        "Hitung konversi checkout berkualitas",
        "Calculate conversion among qualified checkout sessions",
    ),
    (
        "cross_domain_reasoning",
        "recognized_gross_profit",
        "orders.ordered_at",
        "orders.city",
        "Gabungkan eligibility pesanan dengan biaya item untuk menghitung laba kotor",
        "Combine eligible orders and their item costs to calculate recognized gross profit",
    ),
)

CONTRACT_QUESTIONS = {
    "root_cause": (
        (
            "Investigate the Jakarta Electronics revenue decline during 12–18 "
            "March 2026, rank stock availability, spend, and payment evidence, "
            "and reconcile arithmetic contributions."
        ),
        (
            "Selidiki penurunan omzet Electronics Jakarta tanggal 12–18 Maret "
            "2026. Bandingkan stok, belanja, dan pembayaran serta rekonsiliasi "
            "kontribusinya."
        ),
        (
            "rank_stockout_evidence",
            "exact_reconciliation",
            "preserve_unexplained_residual",
            "no_observational_causation",
        ),
    ),
    "business_system_link": (
        (
            "Compare payment-service latency, timeout events and checkout "
            "conversion during 5–8 March 2026 with the preceding matched "
            "weekdays. What evidence links the deployment and business change?"
        ),
        (
            "Bandingkan latency payment-service, timeout, dan konversi "
            "checkout tanggal 5–8 Maret 2026 dengan hari yang sama minggu "
            "sebelumnya. Jelaskan batas bukti deployment."
        ),
        (
            "aligned_time_and_business_scope",
            "separate_association_and_causation",
            "no_automatic_rollback",
        ),
    ),
    "forecast": (
        (
            "Forecast daily recognized revenue for seven days after 25 March "
            "2026, using chronological validation and prediction intervals. "
            "Explain missing or incomplete observations."
        ),
        (
            "Buat forecast omzet harian tujuh hari setelah 25 Maret 2026 "
            "dengan validasi kronologis dan interval prediksi. Jelaskan "
            "kelengkapan datanya."
        ),
        (
            "statsforecast_chronological_validation",
            "prediction_intervals",
            "no_future_training_data",
        ),
    ),
    "causal_effect": (
        (
            "Estimate the conversion effect of the independently randomized "
            "Growth experiment using one outcome per assigned customer and "
            "assignment-aware uncertainty."
        ),
        (
            "Estimasi efek konversi eksperimen Growth yang diacak independen, "
            "satu outcome per pelanggan dan ketidakpastian sesuai unit "
            "assignment."
        ),
        (
            "published_randomized_protocol_required",
            "independent_assignment_units",
            "assignment_aware_interval",
            "insufficient_without_protocol",
        ),
    ),
    "decision_option": (
        (
            "Compare Jakarta stock-transfer options with campaign spend under "
            "bounded cost and capacity. Show gross-profit economics, explicit "
            "assumptions and uncertainty; prepare recommendations only."
        ),
        (
            "Bandingkan opsi transfer stok Jakarta dan tambahan spend dengan "
            "batas biaya dan kapasitas. Tampilkan ekonomi laba kotor, asumsi, "
            "serta ketidakpastian sebagai rekomendasi."
        ),
        (
            "conditional_simulation",
            "bounded_options",
            "gross_profit_after_incremental_spend",
            "record_before_approval",
            "no_external_action",
        ),
    ),
    "policy": (
        (
            "A costly inventory transfer needs approval. Explain whether an "
            "approval can authorize data reads, survive changed inputs, or "
            "apply after a policy revision."
        ),
        (
            "Transfer stok berbiaya memerlukan approval. Apakah approval "
            "memberi akses data, berlaku untuk input berubah, atau bertahan "
            "setelah kebijakan direvisi?"
        ),
        (
            "no_data_access_grant",
            "exact_revision_approval",
            "changed_policy_invalidates",
            "no_execution_without_consent",
        ),
    ),
    "lineage": (
        (
            "What must the evidence chain contain for a revenue decision, from "
            "News through investigation, numerical tools, selected option and "
            "approval to an outcome? Mark unavailable references."
        ),
        (
            "Jelaskan bukti yang diperlukan untuk keputusan revenue dari News, "
            "investigasi, tools, opsi terpilih, approval, sampai outcome. "
            "Tandai referensi yang belum tersedia."
        ),
        (
            "critical_lineage_complete_or_unavailable",
            "query_and_method_references",
            "semantic_versions",
            "immutable_review_events",
        ),
    ),
    "outcome": (
        (
            "Evaluate a decision when its outcome window has missing "
            "observations and overlaps another decision. Separate observed "
            "change, forecast error, completeness and attributable effect."
        ),
        (
            "Evaluasi keputusan yang jendela outcomenya kekurangan data dan "
            "tumpang tindih dengan keputusan lain. Pisahkan perubahan "
            "teramati, error forecast, kelengkapan, dan efek teratribusi."
        ),
        (
            "missing_window_unavailable",
            "overlap_qualified",
            "observed_not_attributed",
            "no_verified_rule_rewrite",
        ),
    ),
    "confidence": (
        (
            "An exact dimensional decomposition explains a revenue change. "
            "Distinguish semantic, detection, statistical, causal and "
            "recommendation confidence without treating arithmetic "
            "reconciliation as causal proof."
        ),
        (
            "Dekomposisi dimensi tepat merekonsiliasi perubahan revenue. "
            "Pisahkan confidence semantik, deteksi, statistik, kausal, dan "
            "rekomendasi tanpa menganggap aritmetika sebagai bukti kausal."
        ),
        (
            "separate_confidence_dimensions",
            "arithmetic_not_causal",
            "unavailable_probabilities_remain_unavailable",
        ),
    ),
    "unknown_or_insufficient_evidence": (
        (
            "Did the Bandung catalog-v9 deployment cause the Jakarta "
            "Electronics stockout on 12 March 2026? Check scope, coincidence, "
            "sample sizes and counterfactual evidence."
        ),
        (
            "Apakah deployment catalog-v9 di Bandung menyebabkan stockout "
            "Electronics Jakarta tanggal 12 Maret 2026? Periksa scope, "
            "kebetulan waktu, sampel, dan bukti kontrafaktual."
        ),
        ("unrelated_deployment_rejected", "no_temporal_causation", "insufficient_causal_evidence"),
    ),
}


def cases(split: str) -> list[Case]:
    if split not in {"A", "B"}:
        raise ValueError("Choose held-out split A or B")
    output = []
    count = 4 if split == "A" else 2
    cities = ("Jakarta", "Bandung", "Surabaya", "Medan")
    for category, metric, time, city_field, indonesian, english in METRIC_QUESTIONS:
        for variant in range(count):
            city = cities[variant] if split == "A" else cities[3 - variant]
            start, end = (
                ("2026-02-01", "2026-03-01") if split == "A" else ("2026-03-09", "2026-03-16")
            )
            question = (f"{indonesian if variant % 2 == 0 else english} di {city}, "
                        f"{start} sampai sebelum {end}.")
            if split == "B":
                question += " Rinci berdasarkan hari dan urutkan kronologis."
            plan = {
                "metrics": [metric],
                "filters": [{"field": city_field, "operator": "=", "value": city}],
                "time": {
                    "dimension": time,
                    "range": f"{start}..{date.fromisoformat(end) - timedelta(days=1)}",
                    "grain": "day" if split == "B" else None,
                },
                "limit": 100,
                "order_by": [{"field": time, "direction": "asc"}] if split == "B" else [],
            }
            output.append(
                Case(
                    f"{split}-{category}-{variant + 1}",
                    category,
                    question,
                    True,
                    plan,
                    dimensions=("answer_accuracy", "rule_accuracy", "plan_accuracy"),
                    parameters={
                        "metric": metric,
                        "start": start,
                        "end": end,
                        "city": city,
                        "daily": split == "B",
                    },
                )
            )
    modifiers = (
        "Show only conclusions supported by authorized observations.",
        "Sebutkan bukti yang masih diperlukan sebelum keputusan disetujui.",
        "Keep competing hypotheses visible when evidence is inconclusive.",
        "Jelaskan apa yang perlu direvisi bila data datang terlambat.",
    )
    for category, (english, indonesian, rubric) in CONTRACT_QUESTIONS.items():
        for variant in range(4 if split == "A" else 3):
            base = english if variant % 2 == 0 else indonesian
            modifier = (
                modifiers[variant]
                if split == "A"
                else (
                    " Also consider a revoked dataset permission and an expired approval."
                    if variant == 0
                    else (
                        " Terapkan prinsip yang sama untuk Surabaya dan jelaskan "
                        "keterbatasan generalisasinya."
                    )
                    if variant == 1
                    else (
                        " Would your conclusion change with fewer than thirty independent "
                        "observations per group?"
                    )
                )
            )
            output.append(
                Case(
                    f"{split}-{category}-{variant + 1}",
                    category,
                    f"{base} {modifier}",
                    category in {"root_cause", "decision_option"},
                    rubric=rubric,
                )
            )
    return output


def export_holdout(split: str) -> list[dict]:
    return [asdict(row) for row in cases(split)]
