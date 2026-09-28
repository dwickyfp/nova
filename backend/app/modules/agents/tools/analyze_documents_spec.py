"""Model-facing contract of ``analyze_documents``, importable without its runtime."""

from __future__ import annotations

from typing import Any

MAX_DOCUMENTS = 50

DESCRIPTION = (
    "Count themes across documents in a Nova AI Search index (which complaints are "
    "most common, how feedback splits by topic). Returns counts and shares per label "
    "with citations. Labels are assigned by the model; counts are exact."
)

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "index": {"type": "string"},
        "query": {"type": "string", "description": "Which documents to analyse."},
        "labels": {
            "type": "array", "minItems": 2, "maxItems": 12, "items": {"type": "string"},
            "description": "Themes to count, e.g. ['refund', 'late delivery', 'damaged'].",
        },
        "top_k": {"type": "integer", "minimum": 5, "maximum": MAX_DOCUMENTS},
    },
    "required": ["index", "query", "labels"],
    "additionalProperties": False,
}
