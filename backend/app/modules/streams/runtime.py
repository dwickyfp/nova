"""Keep namespace syntax available without admitting an unverified provider."""

from app.sql_frontend.errors import CapabilityUnsupportedError


def require_stream_runtime() -> None:
    # Exclusive capture and policy equivalence are not established yet.
    raise CapabilityUnsupportedError("Streams provider is unavailable")
