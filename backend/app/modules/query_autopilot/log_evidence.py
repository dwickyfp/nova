"""Classify explicit execution errors only on lines naming the exact engine query."""

import re
from uuid import UUID


def log_summary(text: str, query_id: str) -> dict:
    identifier = str(UUID(query_id))
    match = re.compile(r"(?<![0-9a-f-])" + re.escape(identifier) + r"(?![0-9a-f-])", re.I)
    lines = [line for line in text.splitlines() if match.search(line)]
    memory = any(
        re.search(r"\b(?:memory limit exceeded|mem(?:ory)? limit exceed)\b", line, re.I)
        for line in lines
    )
    timeout = any(
        re.search(r"\bquery (?:timed out|timeout exceeded)\b", line, re.I) for line in lines
    )
    return {
        "correlated_line_count": len(lines),
        "query_id_verified": bool(lines),
        "facts": {"memory_limit_exceeded": True}
        if memory
        else ({"query_timed_out": True} if timeout else {}),
    }
