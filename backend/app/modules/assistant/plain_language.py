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
        "list_semantic_views",
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


#: Nova's page addresses, by the menu a person clicks instead.
PAGE_MENUS: dict[str, str] = {
    "workspaces": "Data Management → Workspaces",
    "database-explorer": "Data Management → Database Explorer",
    "tasks": "Data Management → Tasks",
    "agents/skills": "AI & ML → Skills",
    "agents/tools": "AI & ML → Tools",
    "agents": "AI & ML → Agent",
    "semantic-views": "AI & ML → Semantic Views",
    "business-ontology": "AI & ML → Business Ontology",
    "ai-search": "AI & ML → AI Search",
    "studio": "AI & ML → Nova Studio",
    "analytics": "AI & ML → BI",
    "entities": "AI & ML → Entities",
    "feature-store": "AI & ML → Feature Store",
    "ml-models": "AI & ML → ML Models",
    "users": "Access Control → Users",
    "roles": "Access Control → Roles",
    "access-control": "Access Control → Data Access",
    "monitoring/audit": "Operations → Monitoring → Audit Trail",
    "monitoring/queries": "Operations → Monitoring → Queries",
    "monitoring/loads": "Operations → Monitoring → Data Loads",
    "monitoring/cluster": "Operations → Monitoring → Cluster Monitor",
    "monitoring/health": "Operations → Monitoring → Production Health",
    "monitoring/ai": "Operations → Monitoring → AI Monitoring",
    "monitoring": "Operations → Monitoring",
    "query-history": "Operations → Monitoring → Queries",
    "ai-providers": "Administrator → AI Providers",
    "settings": "Administrator → Admin Settings",
    "migration": "Operations → Migration",
}
_PAGE = re.compile(
    r"`?(?<![\w@.\-/])/("
    + "|".join(re.escape(path) for path in sorted(PAGE_MENUS, key=len, reverse=True))
    + r")(?:/[\w{}\-]+)*/?(?![\w\-])`?"
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("route", re.compile(r"(?<![\w.])/api/[\w/{}.\-]*", re.I)),
    ("page_address", _PAGE),
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


#: What each internal tool did, in words a maintainer uses.
_TOOL_WORDS = {
    "load_skill": "a Nova guide",
    "search_knowledge": "Nova's guides",
    "catalog_search": "the data catalog",
    "list_semantic_views": "the list of Semantic Views",
    "validate_sql": "a SQL check",
    "inspect_query_error": "the failed query",
    "verify_query_repair": "a rerun check",
    "inspect_agent_configuration": "the agent's settings",
    "invoke_client_capability": "the page",
    "data_to_chart": "a chart",
    "query_execute": "a query",
    "query_mutate": "a change",
    "compute_metrics": "a calculation",
    "ml_execute": "a model run",
    "ai_search": "AI search",
    "semantic_view_query": "the semantic view",
    "semantic_query": "the semantic view",
    "feature_lookup": "the feature store",
    "inspect_role_access": "an access check",
    "grant_role_access": "an access grant",
    "provision_user": "new user setup",
    "create_semantic_view": "semantic view setup",
    "create_agent": "agent setup",
    "web_search": "a web search",
    "describe_agent": "the agent's description",
    "dataviz_query": "the dashboard data",
    "ontology_resolve": "the business glossary",
    "ontology_explain": "the business glossary",
    "knowledge_propose": "a glossary proposal",
}

_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Traceback \(most recent call last\):[\s\S]*?(?=\n\S|\Z)"), ""),
    (re.compile(r"File \"[^\"]+\", line \d+[^\n]*"), ""),
    (re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/[\w/{}.\-]+"), "Nova"),
    (re.compile(r"(?<![\w.])/api/[\w/{}.\-]*", re.I), "Nova"),
    (
        re.compile(
            r"\bhttps?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|[\w.-]+:\d{2,5})\S*",
            re.I,
        ),
        "the service address",
    ),
    (re.compile(r"\bAPI[- ]keys?\b", re.I), "access key"),
    (re.compile(r"\bend[- ]?points?\b", re.I), "service address"),
    (re.compile(r"\bAPIs?\b"), "service"),
    (re.compile(r"\bHTTP(?:S)?\s*(?:status\s*)?\d{3}\b|\bstatus code\b", re.I), "an error"),
    (re.compile(r"\bNOVA_SYSTEM\.\w+|\bCONFIG_[A-Z][A-Z_]{3,}\b"), "Nova's internal records"),
    (re.compile(r"\bapp\.(?:modules|core|common)(?:\.\w+)+"), "Nova"),
    (re.compile(r"\b[\w/]+\.(?:py|tsx?|cjs)\b"), "Nova"),
    (
        re.compile(
            r"`?(?<![\w.])("
            + "|".join(sorted(_TOOL_WORDS, key=len, reverse=True))
            + r")(?![\w])`?"
        ),
        "",
    ),
)


def make_plain(text: str) -> str:
    """``text`` with technical internals replaced by plain words.

    Fenced code blocks are left exactly as written: code a person copies must
    still run, and its keys (a provider's ``endpoint`` property) are real.
    """
    if not text or "```" not in text:
        return _plain_prose(text)
    parts = text.split("```")
    # Even parts are prose, odd parts are inside a fence.
    return "```".join(part if index % 2 else _plain_prose(part) for index, part in enumerate(parts))


def _plain_prose(text: str) -> str:
    if not text:
        return text
    text = _PAGE.sub(lambda match: PAGE_MENUS[match.group(1)], text)
    for pattern, replacement in _REWRITES:
        if replacement == "" and pattern.pattern.startswith("`?"):
            text = pattern.sub(lambda match: _TOOL_WORDS[match.group(1)], text)
        else:
            text = pattern.sub(replacement, text)
    return text


#: Words that begin a multi-word or longer internal term; a trailing one is
#: held until the next chunk shows whether the term continues.
_STARTERS = frozenset(
    {"http", "https", "status", "end", "api", "traceback", "file", "get", "post", "put",
     "patch", "delete", "nova_system", "localhost"}
)
_VOCABULARY = tuple(sorted({*_STARTERS, "endpoint", "endpoints", *TOOL_NAMES}))


def _could_continue_a_term(token: str) -> bool:
    """True when ``token`` (the unfinished end of the text) may be part of an internal."""
    token = token.rstrip(".,;!?)\"'")
    if not token:
        return False
    if re.search(r"[_/:`\\]|\.\w", token) or (len(token) >= 2 and token.isupper()):
        return True
    lowered = token.lower()
    return any(word.startswith(lowered) for word in _VOCABULARY)


class PlainTextStream:
    """Makes streamed answer text plain without breaking a term across chunks.

    Text flows through at once except a short tail that may still turn into an
    internal term: an unfinished word that could begin one ("end" of
    "endpoint", "query_" of a tool name) and a finished starter word whose
    term may continue in the next chunk ("HTTP" before "503"). So ordinary
    text, and scripts without spaces, stream without delay. ``flush`` releases
    the tail at the end of a content block.
    """

    def __init__(self) -> None:
        self._buffer = ""
        #: Whether released text has left an open code fence.
        self._in_code = False

    def feed(self, text: str) -> str:
        self._buffer += text
        cut = self._safe_cut(self._buffer)
        # Never split a fence marker between two releases.
        partial = len(self._buffer[:cut]) - len(self._buffer[:cut].rstrip("`"))
        cut -= partial
        released, self._buffer = self._buffer[:cut], self._buffer[cut:]
        return self._release(released)

    def _release(self, text: str) -> str:
        parts = text.split("```")
        out: list[str] = []
        for index, part in enumerate(parts):
            if index:
                self._in_code = not self._in_code
            out.append(part if self._in_code else _plain_prose(part))
        return "```".join(out)

    @staticmethod
    def _safe_cut(buffer: str) -> int:
        match = re.search(r"(?:(\S+)(\s+))?(\S*)$", buffer)
        if match is None:
            return len(buffer)
        cut = len(buffer)
        tail = match.group(3)
        if tail and _could_continue_a_term(tail):
            cut = match.start(3)
        # A finished starter word before the tail may begin a longer term
        # ("HTTP" before "503", "status" before "code").
        previous = match.group(1)
        if previous and previous.strip("`(").rstrip(":,").lower() in _STARTERS:
            cut = match.start(1)
        return cut

    def flush(self) -> str:
        released, self._buffer = self._buffer, ""
        out = self._release(released)
        self._in_code = False
        return out


#: Frame fields a person reads, by event; values are plain-worded in place.
_READABLE_FIELDS = {
    "error": ("message",),
    "thinking": ("text",),
    "tool_progress": ("text",),
    "tool_detail": ("text",),
    "tool_call": ("result_summary", "error"),
}


def plain_frames():  # noqa: ANN201 - returns a small stateful filter
    """A filter for one turn's SSE frames: ``filter(frame) -> list[frame]``.

    Answer text goes through :class:`PlainTextStream`; the readable fields of
    other frames are rewritten with :func:`make_plain`. Held-back text is
    released before any other frame, so the order of content is preserved.
    """
    import json

    from app.modules.assistant import events

    stream = PlainTextStream()
    #: The ordered-content position of the text being held back.
    position: dict[str, object] = {}

    def parse(frame: str) -> tuple[str | None, dict | None]:
        name = None
        data = None
        for line in frame.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    data = None
        return name, data if isinstance(data, dict) else None

    def delta(text: str) -> str:
        return events.text_delta(
            text,
            content_index=position.get("content_index"),  # type: ignore[arg-type]
            content_id=position.get("content_id"),  # type: ignore[arg-type]
        )

    def flush() -> list[str]:
        rest = stream.flush()
        return [delta(rest)] if rest else []

    def apply(frame: str) -> list[str]:
        name, data = parse(frame)
        if name == events.EVENT_TEXT_DELTA and data is not None:
            incoming = {
                key: data[key] for key in ("content_index", "content_id") if key in data
            }
            out: list[str] = []
            if position and incoming and incoming != position:
                # A new content block: release the old block's text first.
                out = flush()
            position.clear()
            position.update(incoming)
            plain = stream.feed(str(data.get("text", "")))
            return [*out, delta(plain)] if plain else out
        out = flush()
        if data is None or name is None:
            return [*out, frame]
        changed = False
        for field in _READABLE_FIELDS.get(name, ()):
            value = data.get(field)
            if isinstance(value, str):
                data[field], changed = make_plain(value), True
        if name == "plan" and isinstance(data.get("steps"), list):
            for step in data["steps"]:
                if isinstance(step, dict) and isinstance(step.get("text"), str):
                    step["text"], changed = make_plain(step["text"]), True
        if name == "suggestions" and isinstance(data.get("suggestions"), list):
            data["suggestions"] = [make_plain(str(item)) for item in data["suggestions"]]
            changed = True
        return [*out, events.format_sse(name, data) if changed else frame]

    apply.flush = flush  # type: ignore[attr-defined]
    return apply
