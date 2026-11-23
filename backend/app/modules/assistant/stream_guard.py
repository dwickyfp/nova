"""Redaction for text that reaches the screen while the model is still writing.

``GuardChain.screen_answer`` replaces credential-shaped values in a finished
answer. Reasoning and narration are shown while they stream, so the same
screen has to run on fragments without letting a value slip through split
across two of them (``sk-ab`` + ``cdef…``).

``StreamingRedactor`` holds back the tail of the text: it releases only up to
a whitespace boundary at least ``HOLD`` characters before the end, and never
inside a credential match. Every credential pattern is either a single token
(no whitespace) or the PEM header, which is shorter than ``HOLD``; so any value
that starts in released text is complete, and screened, before it is released.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from app.modules.assistant.guards import REDACTED_ANSWER_VALUE, GuardChain, GuardEvent
from app.modules.assistant.tools.redaction import _CREDENTIAL_VALUE_PATTERNS

#: Characters kept back from the end of the stream. Longer than the longest
#: credential pattern that contains whitespace (the PEM header, 41 characters).
HOLD = 64

_WHITESPACE = re.compile(r"\s")


class StreamingRedactor:
    """Screen streamed text with the answer guard, fragment by fragment."""

    def __init__(self) -> None:
        self._pending = ""
        #: The guard decision, once any released text had a value replaced.
        self.event: GuardEvent | None = None

    def feed(self, text: str) -> str:
        """Add a fragment; return the screened text that is safe to show now."""
        if not text:
            return ""
        self._pending += text
        cut = self._safe_cut()
        if cut <= 0:
            return ""
        return self._release(cut)

    def flush(self) -> str:
        """The rest of the text, screened, once the stream has ended."""
        return self._release(len(self._pending)) if self._pending else ""

    def _safe_cut(self) -> int:
        limit = len(self._pending) - HOLD
        if limit <= 0:
            return 0
        # The last whitespace at or before the limit: a token still growing at
        # the end is never split.
        cut = 0
        for match in _WHITESPACE.finditer(self._pending, 0, limit):
            cut = match.end()
        # A complete credential that straddles the cut stays with the tail.
        for pattern in _CREDENTIAL_VALUE_PATTERNS:
            for match in pattern.finditer(self._pending):
                if match.start() < cut < match.end():
                    cut = match.start()
        return cut

    def _release(self, cut: int) -> str:
        released, self._pending = self._pending[:cut], self._pending[cut:]
        screened, event = GuardChain.screen_answer(released)
        if event is not None and self.event is None:
            self.event = event
        return screened


class LiveReasoning:
    """One model call's reasoning summary: redacted, then sent in readable pieces.

    Providers stream reasoning a few characters at a time. Each fragment would
    otherwise become its own event, numbered and kept in the run journal, so
    released text is gathered until it holds ``MIN_CHARS`` or ``MAX_WAIT``
    seconds passed since the last piece went out.
    """

    MIN_CHARS = 48
    MAX_WAIT = 0.15

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._redactor = StreamingRedactor()
        self._clock = clock
        self._ready = ""
        self._last = clock()
        #: Every piece released so far, for the trace.
        self.text = ""
        #: True once a piece was sent; the caller then closes the part.
        self.started = False
        #: Set by the caller once the part was closed.
        self.closed = False
        self._received = False

    @property
    def started_or_pending(self) -> bool:
        """Whether any reasoning arrived, shown yet or still held back."""
        return self._received

    @property
    def guard_event(self) -> GuardEvent | None:
        """The redaction decision to record, when a value was replaced."""
        return self._redactor.event

    def feed(self, fragment: str) -> str:
        """The next piece to show, or ``""`` while it is still gathering."""
        if self.closed or not fragment:
            return ""
        self._received = True
        self._ready += self._redactor.feed(fragment)
        now = self._clock()
        due = now - self._last >= self.MAX_WAIT
        if self._ready and (len(self._ready) >= self.MIN_CHARS or due):
            return self._take(now)
        return ""

    def close(self) -> str:
        """Whatever is left once the model call ended."""
        self._ready += self._redactor.flush()
        return self._take(self._clock()) if self._ready else ""

    def _take(self, now: float) -> str:
        piece, self._ready = self._ready, ""
        self._last = now
        self.text += piece
        self.started = True
        return piece


__all__ = ["HOLD", "REDACTED_ANSWER_VALUE", "LiveReasoning", "StreamingRedactor"]
