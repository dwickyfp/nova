"""NOVA-121 — ``ConsentBroker.open`` must not clobber a pending ``tool_call_id``.

The id is model-supplied and a provider may reuse one. The old ``open`` did a
plain ``self._pending[id] = ...``, silently replacing the first turn's entry and
orphaning its future: the first stream then hung until the loop's 60 s budget,
because the only id the client could resolve now belonged to the second entry.

The broker must refuse the second ``open`` and leave the original entry — and
its future — intact.
"""

from __future__ import annotations

import pytest

from app.modules.assistant.consent import ConsentBroker, ConsentConflictError


async def test_second_open_of_a_pending_id_raises_and_preserves_the_first():
    """AC 2: open X, open X again -> conflict; the first entry survives."""
    broker = ConsentBroker()
    first = broker.open("X", thread_id="t1", user_name="alice")

    with pytest.raises(ConsentConflictError) as exc:
        broker.open("X", thread_id="t2", user_name="bob")

    assert exc.value.tool_call_id == "X"
    # The original ownership is untouched — not the second caller's.
    assert broker.owner_of("X") == ("t1", "alice")
    # And resolving X still resolves the *original* future.
    assert broker.resolve("X", True, user_name="alice") is True
    assert await first is True


async def test_conflicting_open_does_not_leak_a_pending_entry():
    """A refused ``open`` must not leave its own future behind."""
    broker = ConsentBroker()
    broker.open("X", thread_id="t1", user_name="alice")

    with pytest.raises(ConsentConflictError):
        broker.open("X", thread_id="t2", user_name="bob")

    assert broker.resolve("X", True, user_name="bob") is False
    assert broker.owner_of("X") == ("t1", "alice")


async def test_open_after_resolution_is_allowed():
    """A reused id is fine once the first call has been resolved."""
    broker = ConsentBroker()
    first = broker.open("X", thread_id="t1", user_name="alice")
    broker.resolve("X", False, user_name="alice")
    assert await first is False

    second = broker.open("X", thread_id="t1", user_name="alice")
    assert broker.owner_of("X") == ("t1", "alice")
    assert broker.resolve("X", True, user_name="alice") is True
    assert await second is True


async def test_cancel_all_releases_so_the_id_can_be_reopened():
    """After shutdown cancels everything, a stale id is not a conflict."""
    broker = ConsentBroker()
    first = broker.open("X", thread_id="t1", user_name="alice")
    broker.cancel_all()
    assert await first is None

    reopened = broker.open("X", thread_id="t1", user_name="alice")
    broker.cancel_all()
    assert await reopened is None
