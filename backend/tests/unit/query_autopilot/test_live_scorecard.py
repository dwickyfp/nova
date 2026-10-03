from tests.benchmark.query_autopilot.live_scorecard import measured_scorecard


def test_unavailable_and_controlled_logs_cannot_inflate_live_accuracy():
    value = measured_scorecard(
        {"cases": [{"case": "I", "status": "PASS", "diagnosis": []}]}, None, None,
    )
    assert value["measured_cases"] == 0
    assert value["precision"] is None and value["recall"] is None
    assert value["rca"]["supported_cases"] == 0
    assert value["rca"]["top1"] is None
    assert value["coverage"]["controlled_log_fixture"] == "PASS"
    assert not value["complete_acceptance"]


def test_extra_detected_label_counts_as_false_positive_and_missing_root_is_incorrect():
    value = measured_scorecard(
        {"cases": [
            {"case": "F-detection", "findings": [
                {"detector": "high_frequency"}, {"detector": "materialized_view"},
                {"detector": "contention"},
            ]},
            {"case": "E", "phases": {
                "before": {"findings": [], "diagnosis": []},
                "after": {"findings": [], "diagnosis": []},
            }},
        ]}, None, None,
    )
    assert value["measured_cases"] == 3
    assert value["detector_counts"] == {
        "true_positive": 2, "false_positive": 1, "false_negative": 2,
    }
    assert value["precision"] == 2 / 3
    assert value["recall"] == 0.5
    assert value["rca"]["supported_cases"] == 1
    assert value["rca"]["top1"] == 0


def test_controlled_clock_regression_never_counts_as_live_window_acceptance():
    value = measured_scorecard({}, None, {
        "wall_clock_historical_acceptance": "NOT_RUN",
        "status": "PASS", "findings": [{"detector": "latency_regression"}],
    })
    assert value["measured_cases"] == 0
    assert {"case": "D", "status": "UNAVAILABLE"} in value["cases"]


def test_bound_natural_regression_counts_contention_and_missing_diagnosis_as_a_miss():
    regression = {
        "wall_clock_historical_acceptance": "PASS",
        "ground_truth": "same_query_slowed_by_controlled_admission_queue",
        "facts": {"saturated": True, "bound_pending_samples": 30,
                  "counter_pair_sample_count": 1, "queue_ms": 900, "execution_ms": 10},
        "after_query_ids": ["measured-query"],
        "findings": [{"detector": "latency_regression"}],
        "diagnosis": [], "negative_variants": {},
    }
    missed = measured_scorecard({}, None, regression)
    assert missed["detector_counts"]["false_negative"] == 1
    assert missed["rca"]["supported_cases"] == 1 and missed["rca"]["top1"] == 0
    regression["findings"].append({"detector": "contention"})
    regression["diagnosis"] = [{"category": "RESOURCE_CONTENTION"}]
    correct = measured_scorecard({}, None, regression)
    assert correct["detector_counts"]["false_positive"] == 0
    assert correct["detector_counts"]["false_negative"] == 0
    assert correct["rca"]["top1_correct"] == correct["rca"]["topk_correct"] == 1


def test_stronger_measured_contention_does_not_hide_the_absolute_slow_label():
    from app.modules.query_autopilot.statistics import Distribution

    latency = Distribution()
    latency.add(9000, 60)
    contention = {"phases": {
        "uncontended": {"count": 0, "latency": Distribution().as_dict(), "findings": []},
        "contended": {"count": 60, "latency": latency.as_dict(),
                      "findings": [{"detector": "contention"}], "diagnosis": []},
    }}
    missed = measured_scorecard({}, contention, None)
    assert missed["detector_counts"]["false_negative"] == 1
    contention["phases"]["contended"]["findings"].append({"detector": "absolute_slow"})
    correct = measured_scorecard({}, contention, None)
    assert correct["detector_counts"]["false_positive"] == 0
    assert correct["detector_counts"]["false_negative"] == 0
