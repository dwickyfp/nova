import pytest

from app.modules.smart_reports.artifacts import artifact_visual
from app.modules.smart_reports.schemas import QuerySpec


@pytest.mark.parametrize("mark, expected", [("bar", "bar"), ("line", "line"), ("area", "area")])
def test_semantic_artifact_preserves_visual_fields_without_inferring_sql(mark, expected):
    query = QuerySpec(source="sales", metrics=["revenue"], dimensions=["store.city"])
    visual = artifact_visual(
        {
            "artifact_type": "chart",
            "chart_spec": {
                "mark": mark,
                "encoding": {
                    "x": {"field": "store__city", "type": "nominal"},
                    "y": {"field": "revenue", "type": "quantitative"},
                },
            },
        },
        query,
    )
    assert visual["kind"] == expected
    assert visual["encoding"].x == "store.city"
    assert visual["encoding"].y == ["revenue"]


@pytest.mark.parametrize(
    "channel",
    [
        {"field": "revenue", "aggregate": "sum"},
        {"field": "revenue", "scale": {"type": "log"}},
        {"field": "forged"},
        {"field": "revenue", "bin": True},
    ],
)
def test_artifact_requires_mapping_for_unrepresentable_visual_semantics(channel):
    query = QuerySpec(source="sales", metrics=["revenue"], dimensions=["city"])
    assert (
        artifact_visual(
            {
                "artifact_type": "chart",
                "chart_spec": {
                    "mark": "bar",
                    "encoding": {"x": {"field": "city"}, "y": channel},
                },
            },
            query,
        )
        is None
    )


def test_grouped_series_copies_native_stacking_and_rejects_unencoded_dimensions():
    query = QuerySpec(source="sales", metrics=["revenue"], dimensions=["city", "product"])
    artifact = {
        "artifact_type": "chart",
        "chart_spec": {
            "mark": "bar",
            "encoding": {
                "x": {"field": "city"},
                "y": {"field": "revenue"},
                "color": {"field": "product"},
            },
        },
    }
    visual = artifact_visual(artifact, query)
    assert visual["kind"] == "stacked_bar"
    assert visual["encoding"].series == "product"
    del artifact["chart_spec"]["encoding"]["color"]
    assert artifact_visual(artifact, query) is None
