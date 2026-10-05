"""Keep namespace syntax available without admitting an unverified provider."""

from app.core.config import settings
from app.sql_frontend.errors import CapabilityUnsupportedError


def require_stream_runtime() -> None:
    if not settings.STREAMS_ENABLED:
        raise CapabilityUnsupportedError("Streams are disabled")
    # A configuration flag cannot establish exclusive capture or policy equivalence.
    raise CapabilityUnsupportedError("Streams provider is unavailable")
