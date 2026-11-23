"""Redaction of streamed reasoning and narration, fragment by fragment.

A credential split across provider deltas must never reach the screen in
pieces; released text must join back to the screened whole.
"""

from __future__ import annotations

import pytest

from app.modules.assistant.guards import REDACTED_ANSWER_VALUE, GuardChain
from app.modules.assistant.stream_guard import HOLD, LiveReasoning, StreamingRedactor

KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.c2lnbmF0dXJlc2lnbmF0dXJl"
PEM = "-----BEGIN RSA PRIVATE KEY-----"
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"


def _stream(text: str, size: int) -> list[str]:
    return [text[index:index + size] for index in range(0, len(text), size)]


def _released(redactor: StreamingRedactor, pieces: list[str]) -> list[str]:
    out = [redactor.feed(piece) for piece in pieces]
    out.append(redactor.flush())
    return out


@pytest.mark.parametrize("secret", [KEY, JWT, PEM, AWS])
@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 13, 64, 200])
def test_a_secret_split_across_deltas_is_never_released(secret: str, size: int) -> None:
    text = f"Reading the config. The value {secret} is set for the connection. Done here."
    redactor = StreamingRedactor()
    released = _released(redactor, _stream(text, size))
    joined = "".join(released)
    assert joined == GuardChain.screen_answer(text)[0]
    assert REDACTED_ANSWER_VALUE in joined
    # No released piece holds any fragment of the secret long enough to matter.
    for piece in released:
        assert secret[:10] not in piece
    assert redactor.event is not None


@pytest.mark.parametrize("size", [1, 4, 9, 50])
def test_plain_text_passes_through_unchanged(size: int) -> None:
    text = "The orders view has the customer and amount fields, so it answers the question. " * 3
    redactor = StreamingRedactor()
    released = _released(redactor, _stream(text, size))
    assert "".join(released) == text
    assert redactor.event is None
    # Text is released while it streams, not only at the end.
    assert any(released[:-1])


def test_nothing_is_released_inside_the_hold_window() -> None:
    redactor = StreamingRedactor()
    assert redactor.feed("x " * (HOLD // 2)) == ""
    assert redactor.flush() == "x " * (HOLD // 2)


def test_live_reasoning_gathers_pieces_and_closes_with_the_rest() -> None:
    now = [0.0]
    live = LiveReasoning(clock=lambda: now[0])
    text = "I should compare the regions by revenue and then rank them by growth. " * 2
    shown = []
    for piece in _stream(text, 3):
        shown.append(live.feed(piece))
    shown.append(live.close())
    assert "".join(shown) == text
    assert live.text == text and live.started
    # Gathered into a handful of pieces, not one per fragment.
    assert 1 < len([piece for piece in shown if piece]) < 10


def test_live_reasoning_single_chunk_is_shown_on_close() -> None:
    live = LiveReasoning(clock=lambda: 0.0)
    assert live.feed("**Calculating time difference**") == ""
    assert live.started_or_pending
    assert live.close() == "**Calculating time difference**"


def test_live_reasoning_records_the_redaction() -> None:
    live = LiveReasoning(clock=lambda: 0.0)
    for piece in _stream(f"The key is {KEY} and it should not be used here at all.", 4):
        live.feed(piece)
    live.close()
    assert KEY not in live.text and REDACTED_ANSWER_VALUE in live.text
    assert live.guard_event is not None and live.guard_event.action == "redacted"
