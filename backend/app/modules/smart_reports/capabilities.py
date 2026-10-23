"""Executable report capabilities; unsupported configurations fail validation."""

SUPPORTED_CHARTS = frozenset(
    {
        "kpi",
        "bar",
        "line",
        "area",
        "table",
        "stacked_bar",
        "grouped_bar",
        "combo",
        "scatter",
        "donut",
        "funnel",
        "pivot",
        "heatmap",
        "waterfall",
        "map",
    }
)


def capabilities() -> dict:
    from app.core.database import configured_timezone

    return {
        "schema_version": "1.0.0",
        "default_timezone": configured_timezone(),
        "charts": {
            name: {
                "backend": name in SUPPORTED_CHARTS,
                "renderer": name in SUPPORTED_CHARTS,
                "reason": None if name in SUPPORTED_CHARTS else reason,
            }
            for name, reason in {
                **{name: None for name in sorted(SUPPORTED_CHARTS)},
            }.items()
        },
        "result_cache": {
            "available": False,
            "reason": ("Reusable data caches require an execution-confirmed policy fingerprint"),
        },
    }
