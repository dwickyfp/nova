"""The L3 scorer accepts correct answers in another shape and nothing else."""

from tests.benchmark.studio_accuracy.gold import normalize_rows
from tests.benchmark.studio_accuracy.run import filter_answered

CHANNEL = {"metrics": ["order_count"], "filters": {"sales_channel": "Mobile App"}}
CITIES = {"metrics": ["total_revenue"], "filters": {"city": ["Jakarta", "Bandung"]}}


def test_a_breakdown_holding_the_filtered_row_answers_a_filtered_total():
    table = {"rows": [["Marketplace", 13], ["Mobile App", 19], ["Store", 16]]}
    assert filter_answered(CHANNEL, table, [("19.00",)])
    assert not filter_answered(CHANNEL, table, [("20.00",)])


def test_a_multi_value_filter_needs_exactly_the_filtered_rows():
    exact = {"rows": [["Bandung", "976689000.00"], ["Jakarta", "1034992000.00"]]}
    wider = {"rows": [*exact["rows"], ["Medan", "1116226000.00"]]}
    assert filter_answered(CITIES, exact, [("2011681000.00",)])
    assert not filter_answered(CITIES, wider, [("2011681000.00",)])
    assert not filter_answered(CITIES, {"rows": exact["rows"][:1]}, [("2011681000.00",)])


def test_grouped_gold_is_never_answered_by_a_filter_breakdown():
    grouped = {**CHANNEL, "dimensions": ["city"]}
    assert not filter_answered(grouped, {"rows": [["Mobile App", 19]]}, [("19.00",)])


def test_percent_cells_compare_as_numbers():
    assert normalize_rows([["Jakarta", "20.4763%"]]) == [("20.48", "Jakarta")]
