"""Finds technical internals in text a person reads from the assistant.

The assistant serves people who look after Nova, not its developers. Its
answers, suggestions and the tool messages it repeats must not expose how Nova
is built: route paths, the words "endpoint" or "API", HTTP statuses, internal
tool, module, file or system-table names, stack traces or service addresses.
Besides confusing the reader, those names map the system for an attacker.

``find_technical_leaks`` is deterministic and offline. Tests use it to measure
leaks, and the answer guard uses it to keep them out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Internal tool names. A person never needs them; the assistant describes what
#: it did ("I checked the catalog") instead.
TOOL_NAMES = frozenset(
    {
        "load_skill",
        "search_knowledge",
        "catalog_search",
        "validate_sql",
        "inspect_query_error",
        "verify_query_repair",
        "inspect_agent_configuration",
        "invoke_client_capability",
        "data_to_chart",
        "query_execute",
        "query_mutate",
        "compute_metrics",
        "ml_execute",
        "ai_search",
        "semantic_view_query",
        "semantic_query",
        "feature_lookup",
        "inspect_role_access",
        "grant_role_access",
        "provision_user",
        "create_semantic_view",
        "create_agent",
        "web_search",
        "describe_agent",
        "dataviz_query",
        "ontology_resolve",
        "ontology_explain",
        "knowledge_propose",
    }
)


@dataclass(frozen=True)
class Leak:
    kind: str
    text: str


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("route", re.compile(r"(?<![\w.])/api/[\w/{}.\-]*", re.I)),
    ("route", re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/[\w/{}.\-]+")),
    ("endpoint", re.compile(r"\bend[- ]?points?\b", re.I)),
    ("api", re.compile(r"\bAPIs?\b")),
    ("http", re.compile(r"\bHTTP(?:S)?\s*(?:status\s*)?\d{3}\b|\bstatus code\b", re.I)),
    ("system_table", re.compile(r"\bNOVA_SYSTEM\.\w+|\bCONFIG_[A-Z][A-Z_]{3,}\b")),
    ("source_file", re.compile(r"\b[\w/]+\.(?:py|tsx?|cjs)\b")),
    ("module", re.compile(r"\bapp\.(?:modules|core|common)\.\w+")),
    (
        "stack_trace",
        re.compile(r"Traceback \(most recent call last\)|File \"[^\"]+\", line \d+"),
    ),
    (
        "address",
        re.compile(
            r"\bhttps?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|[\w.-]+:\d{2,5})\S*",
            re.I,
        ),
    ),
    (
        "tool_name",
        re.compile(
            r"(?<![\w.])(?:"
            + "|".join(sorted(TOOL_NAMES, key=len, reverse=True))
            + r")(?![\w])"
        ),
    ),
)


def find_technical_leaks(text: str) -> list[Leak]:
    """Every technical internal in ``text``, in order of appearance."""
    found: list[tuple[int, Leak]] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text or ""):
            found.append((match.start(), Leak(kind=kind, text=match.group(0))))
    found.sort(key=lambda item: item[0])
    return [leak for _, leak in found]
