"""Retrieval fusion stays deterministic and independent of score scales."""

import pytest

from app.modules.intelligence.retrieval import (
    RetrievalError,
    SearchCandidate,
    evaluate_relevance,
    fuse_results,
)


def candidate(key, score):
    return SearchCandidate(key, key, {}, score)


def test_rrf_merges_shared_evidence_and_exposes_ranks():
    hits = fuse_results(
        [candidate("a", 9000), candidate("b", 1)],
        [candidate("b", 0.01), candidate("c", 0.99)],
        mode="HYBRID",
        top_k=3,
    )
    assert [hit.source_key for hit in hits] == ["b", "a", "c"]
    assert hits[0].lexical_rank == 2
    assert hits[0].semantic_rank == 1
    assert hits[0].score == pytest.approx(1 / 62 + 1 / 61)


def test_single_channel_and_stable_ties():
    lexical = [candidate("b", 5), candidate("a", 1)]
    assert [hit.source_key for hit in fuse_results(lexical, [], mode="LEXICAL", top_k=2)] == [
        "b",
        "a",
    ]
    hybrid = fuse_results([candidate("a", 1)], [candidate("b", 1)], mode="HYBRID", top_k=2)
    assert [hit.source_key for hit in hybrid] == ["a", "b"]


@pytest.mark.parametrize(
    "lexical,semantic,kwargs",
    [
        ([candidate("a", 1), candidate("a", 2)], [], {}),
        ([candidate("", 1)], [], {}),
        ([candidate("a", float("nan"))], [], {}),
        ([], [], {"top_k": 0}),
        ([], [], {"rrf_k": 0}),
    ],
)
def test_invalid_fusion_is_rejected(lexical, semantic, kwargs):
    with pytest.raises(RetrievalError):
        fuse_results(lexical, semantic, mode="HYBRID", **{"top_k": 2, **kwargs})


def test_relevance_metrics_include_graded_ndcg():
    metrics = evaluate_relevance(["a", "b", "c"], {"a": 2, "c": 1}, top_k=3)
    assert metrics.precision_at_k == pytest.approx(2 / 3)
    assert metrics.recall_at_k == 1.0
    assert metrics.mrr == 1.0
    assert 0 < metrics.ndcg_at_k < 1
    assert not metrics.zero_result


def test_empty_result_is_measured_without_division_by_zero():
    metrics = evaluate_relevance([], {}, top_k=5)
    assert metrics.zero_result
    assert metrics.recall_at_k == metrics.ndcg_at_k == 0.0


def test_invalid_retrieval_mode_and_relevance_input_are_rejected():
    with pytest.raises(RetrievalError, match="Unsupported retrieval mode"):
        fuse_results([], [], mode="UNKNOWN", top_k=1)
    with pytest.raises(RetrievalError, match="Invalid relevance judgment"):
        evaluate_relevance(["a"], {"a": -1}, top_k=1)
    with pytest.raises(RetrievalError, match="Ranked keys must be non-empty"):
        evaluate_relevance(["a", "a"], {"a": 1}, top_k=2)
