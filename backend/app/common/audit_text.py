"""Shared audit text limits, independent of either database adapter."""

AUDIT_TEXT_MAX_BYTES = 60_000


def fit_audit_text(text: str | None, limit: int = AUDIT_TEXT_MAX_BYTES) -> str | None:
    """Truncate to a UTF-8 byte budget and record the omitted byte count."""
    if text is None:
        return None
    encoded = text.encode("utf-8", errors="surrogateescape")
    if len(encoded) <= limit:
        return text
    marker_budget = 64
    kept = encoded[: limit - marker_budget].decode("utf-8", errors="ignore")
    removed = len(encoded) - len(kept.encode("utf-8", errors="surrogateescape"))
    return f"{kept} /* [truncated {removed} bytes] */"
