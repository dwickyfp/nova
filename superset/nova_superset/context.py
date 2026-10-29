"""The signed-in Nova session of the request being served.

Kept in context variables rather than ``flask.g``: Superset runs synchronous
SQL Lab queries inside a fresh ``test_request_context``, which starts with an
empty ``g``, but stays on the same thread and context. Every request clears
these values first and again at teardown, so a worker thread never carries one
user's session into the next request.
"""

from __future__ import annotations

from contextvars import ContextVar

from nova_superset.client import DataToken, Identity

bi_handle: ContextVar[str | None] = ContextVar("nova_bi_handle", default=None)
identity: ContextVar[Identity | None] = ContextVar("nova_identity", default=None)
data_token: ContextVar[DataToken | None] = ContextVar("nova_data_token", default=None)


def clear() -> None:
    bi_handle.set(None)
    identity.set(None)
    data_token.set(None)
