"""What a person who knows the business, and nothing about Nova, asks Smart."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Case:
    id: str
    turns: tuple[str, ...]
    expect: str
    gold: tuple[str, ...] = ()
    #: Stream artifacts the last turn must carry: chart, table, automation_proposal.
    artifacts: tuple[str, ...] = ()
    #: The specialists that can also answer it alone, as a baseline.
    direct: tuple[str, ...] = field(default=())
    #: Gold the judge may check a detail against, though the answer need not state it.
    reference: tuple[str, ...] = ()


CATALOG = (
    "Describes two subject areas, finance (revenue, expense, net income) and employees "
    "(headcount, active headcount, salary, payroll), in plain business words."
)
PROPOSAL = (
    "Says it has prepared {what} and that it starts only after the user confirms it. It must "
    "not claim the schedule is already running, and must not ask the user to use another "
    "feature or screen to set it up."
)

CASES = (
    Case("catalog_id", ("data apa yang kamu punya?",), CATALOG),
    Case("catalog_en", ("What metrics can you calculate for me?",), CATALOG),
    Case("identity", ("Halo, kamu siapa dan bisa bantu apa?",),
         "Introduces itself and describes its help in terms of the finance and employee data "
         "it has; no invented data sources."),
    Case("fin_total", ("Berapa total expense tahun 2025?",),
         "States total 2025 expense matching gold.", ("expense_2025",), direct=("finance",)),
    Case("fin_total_en", ("What was our total expense in 2025?",),
         "States total 2025 expense matching gold, in English.", ("expense_2025",)),
    Case("fin_breakdown", ("Tampilkan net income per departemen untuk tahun 2025",),
         "Net income for all five departments in 2025 matching gold.",
         ("net_income_2025_by_department",), direct=("finance",)),
    Case("hr_total", ("Berapa jumlah karyawan aktif saat ini?",),
         "States active headcount matching gold.", ("active_headcount",), direct=("hr",)),
    Case("hr_breakdown", ("What is the average monthly salary by job level?",),
         "Average monthly salary for Junior, Mid, Senior and Lead matching gold.",
         ("avg_salary_by_job_level",), direct=("hr",)),
    Case("cross", ("Berapa total expense tahun 2025 per departemen, dan berapa jumlah karyawan "
                   "aktif di tiap departemen?",),
         "One combined answer with 2025 expense and active headcount for all five departments.",
         ("expense_2025_by_department", "active_headcount_by_department")),
    Case("cross_en", ("Show me 2025 expense and the number of active employees for each "
                      "department.",),
         "One combined answer with 2025 expense and active headcount for all five departments, "
         "in English.",
         ("expense_2025_by_department", "active_headcount_by_department")),
    Case("ratio", ("Berapa expense tahun 2025 per karyawan aktif di tiap departemen?",),
         "Expense per active employee for all five departments, matching gold.",
         ("expense_per_active_employee",)),
    Case("share", ("Berapa persen porsi tiap departemen dari total expense tahun 2025?",),
         "Each of the five departments' share of total 2025 expense, in percent, matching gold.",
         ("expense_share_2025_by_department",)),
    Case("growth", ("Berapa persen kenaikan atau penurunan total expense dari April 2026 ke "
                    "Mei 2026?",),
         "States the percentage change of total expense from April 2026 to May 2026 matching "
         "gold, with the right direction.",
         ("expense_growth_april_to_may_2026",)),
    Case("why", ("Kenapa total expense Mei 2026 berubah dibanding April 2026?",),
         "States how total expense moved between April and May 2026 and names what drove the "
         "change, from data: a breakdown by category, by department, or by both is each a "
         "complete answer. It must not refuse or speculate without figures.",
         ("expense_april_2026", "expense_may_2026"),
         reference=("expense_change_april_to_may_2026_by_category",
                    "expense_change_april_to_may_2026_by_department")),
    Case("chart", ("Buatkan grafik total expense tahun 2025 per departemen",),
         "A chart is shown and the five department expense values match gold.",
         ("expense_2025_by_department",), ("chart",)),
    Case("chart_cross", ("Buatkan grafik yang membandingkan total expense 2025 dan jumlah "
                         "karyawan aktif per departemen",),
         "A chart is shown that covers both expense and active headcount per department.",
         ("expense_2025_by_department", "active_headcount_by_department"), ("chart",)),
    Case("forecast", ("Prediksi total expense untuk 3 bulan ke depan",),
         "Gives predicted total expense for the next three months from the forecast shown, in "
         "plain words. The data ends in September 2026, so the months are October, November "
         "and December 2026; any other months are wrong. It must not refuse. The predicted "
         "values come from a model and have no gold: judge them as plausible against the "
         "monthly history in GOLD, and hold any past month the answer states to GOLD.",
         reference=("expense_by_month",), direct=("finance",)),
    Case("forecast_unavailable", ("Prediksi jumlah karyawan aktif untuk 6 bulan ke depan",),
         "Says plainly that forecasting is not set up for the employee data yet, invents no "
         "predicted numbers, and offers what it can answer."),
    Case("followup", ("Berapa total expense tahun 2025?", "kalau dipecah per kategori?"),
         "The second turn breaks 2025 expense down by category matching gold. Listing the "
         "revenue-only categories with 0 expense is correct data.",
         ("expense_2025_by_category",)),
    Case("chain", ("Berapa total expense tahun 2025 per departemen?", "buatkan grafiknya",
                   "sekarang yang per kategori"),
         "The third turn gives 2025 expense by category matching gold, keeping the year from "
         "the first turn. Listing the revenue-only categories with 0 expense is correct data.",
         ("expense_2025_by_category",)),
    Case("weekly_report", ("Kirimi saya laporan total expense per departemen setiap Senin jam "
                           "8 pagi",),
         PROPOSAL.format(what="a weekly report for Mondays at 08:00"),
         artifacts=("automation_proposal",)),
    Case("alert", ("Kabari saya setiap pagi kalau total expense bulan ini sudah melewati 5 "
                   "miliar",),
         PROPOSAL.format(what="a daily check that alerts when expense passes 5 billion"),
         artifacts=("automation_proposal",)),
    Case("out_of_scope", ("Berapa stok barang di gudang saat ini?",),
         "Says stock data is not available and names what data it does have; invents nothing."),
)
