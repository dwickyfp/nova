"""Context-window management for the bounded assistant loop (NOVA-124).

The loop replays the whole conversation transcript on every turn
(``service.AssistantLoop._build_messages``). That is correct for a short thread
and wrong for a long one: the history grows without bound, so a conversation
eventually exceeds the model's context window and the turn fails with an opaque
provider error. Even before that hard ceiling, a bloated context degrades
answer quality — models lose recall as the token count climbs (context rot).

This module curates the transcript so the loop sends the **smallest set of
high-signal tokens** that keeps the conversation coherent. It follows the
techniques in Anthropic's *Effective context engineering for AI agents*:

* **Recency windowing.** The system prompt and the most recent turns are always
  kept. The oldest turns are dropped first, because the tail of a conversation
  is what the current request depends on.
* **Tool-result clearing.** A tool result older than the recent window is
  replaced by a one-line marker. This is the lightest-touch form of compaction:
  "once a tool has been called deep in the message history, why would the agent
  need to see the raw result again?" The *fact* that a tool ran is preserved;
  its possibly-large body is not.
* **Summary of what was dropped.** When whole turns are dropped, their user and
  assistant text is folded into a single compact note so the model knows the
  conversation had earlier context it is not seeing. The note is deterministic
  (no model call) and bounded, so pruning never depends on a working provider.

The module is pure: it takes an ordered list of provider messages and a token
budget, and returns a new list. It opens no socket, reads no configuration, and
is safe to call from a request path. That is what makes it unit-testable without
a provider or an engine.

**What this is not.** It is not a summarizer that re-writes the whole history,
and it does not call a model. A turn that has already exceeded the budget with
nothing left to prune is reported by ``fits`` rather than silently sent; the
loop can then stop cleanly instead of relying on the provider to reject it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Characters per token for the estimate. The repository already uses ``len//4``
#: as its provider-agnostic token estimate (``skills._estimate_tokens``); the
#: same constant is kept here so a budget means the same number of tokens
#: wherever it is enforced. It deliberately does not depend on a tokenizer
#: library: the loop must run with only the standard library, and the estimate
#: is used to choose *what to drop*, not to bill anyone.
CHARS_PER_TOKEN = 4

#: Default token budget for a single turn's request. Sized to sit comfortably
#: inside a 32k-128k window with room for the system prompt (~1.4k tokens) and
#: the model's answer, while still being generous enough for a long transcript.
DEFAULT_CONTEXT_TOKEN_BUDGET = 24_000

#: How many of the most recent messages are always kept verbatim, regardless of
#: budget. This is the "working set" the current request most plausibly needs:
#: roughly the last three user/assistant exchanges. It is a floor, not a target:
#: if the recent messages alone exceed the budget they are still kept, and
#: ``fits`` reports the overage rather than the manager dropping the live turn.
DEFAULT_KEEP_RECENT_MESSAGES = 6

#: Tool results older than this many messages are replaced by a marker. Clearing
#: happens before whole-message dropping, so a thread of many tool calls collapses
#: cheaply without losing the surrounding dialogue.
DEFAULT_TOOL_RESULT_TTL_MESSAGES = 6

#: The marker a cleared tool result is replaced with. It states what was removed
#: and why, so the model does not mistake the silence for "the tool was never
#: called" and re-run it.
_CLEARED_TOOL_RESULT = "[tool result cleared to save context; the call already ran]"

#: Prefix the loop uses to fold a stored tool result into a user message
#: (``service._build_messages``). Context management recognises the same prefix.
_TOOL_RESULT_PREFIX = "[tool result]"

#: A dropped-turn note is bounded so pruning cannot itself overflow the budget.
_MAX_SUMMARY_CHARS = 800


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate for a string.

    Matches ``skills._estimate_tokens`` and is intentionally not a real
    tokenizer: it is a stable, dependency-free proxy for ordering decisions.
    """
    return len(text) // CHARS_PER_TOKEN


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Token estimate for one provider message, including its tool calls.

    The content is the bulk of the cost; tool-call arguments are counted too so a
    message proposing a large argument payload is not under-billed.
    """
    total = estimate_tokens(str(message.get("content") or ""))
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        total += estimate_tokens(str(function.get("name") or ""))
        total += estimate_tokens(str(function.get("arguments") or ""))
    return total


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Token estimate for an ordered list of provider messages."""
    return sum(estimate_message_tokens(message) for message in messages)


def _is_tool_result(message: dict[str, Any]) -> bool:
    """True when a message is a folded tool result from a previous turn.

    The loop folds a stored tool message into ``role: user`` text prefixed with
    ``[tool result]`` (``service._build_messages``). Within a live turn the loop
    appends the same prefix. Both shapes are recognised so clearing works on the
    replayed history the manager actually sees.
    """
    if message.get("role") != "user":
        return False
    return str(message.get("content") or "").startswith(_TOOL_RESULT_PREFIX)


@dataclass
class ContextStats:
    """What the manager did, for the trace and the tests.

    ``dropped_turns`` counts user/assistant exchanges removed; ``cleared_tool_results``
    counts tool-result bodies replaced. Both are surfaced so the turn trace can
    explain a shrinkage the user might otherwise find surprising.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    budget: int = 0
    dropped_turns: int = 0
    cleared_tool_results: int = 0
    summarized: bool = False
    messages_in: int = 0
    messages_out: int = 0

    @property
    def fits(self) -> bool:
        """True when the curated output is within budget."""
        return self.output_tokens <= self.budget

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "budget": self.budget,
            "dropped_turns": self.dropped_turns,
            "cleared_tool_results": self.cleared_tool_results,
            "summarized": self.summarized,
            "messages_in": self.messages_in,
            "messages_out": self.messages_out,
            "fits": self.fits,
        }


@dataclass
class CuratedContext:
    """The curated message list plus the stats of how it was produced."""

    messages: list[dict[str, Any]]
    stats: ContextStats = field(default_factory=ContextStats)


class ContextManager:
    """Curtail a provider message list to a token budget.

    The manager is stateless and deterministic. ``curate`` may be called once per
    turn; it never mutates its input.

    Order of operations (cheapest and least destructive first):

    1. **Clear old tool results.** Replace the body of any tool result older than
       ``tool_result_ttl`` messages with a marker. The message and its position
       survive; only the payload goes. The system prompt and recent window are
       never touched by this step.
    2. **Drop oldest turns.** While still over budget, remove the oldest
       user+assistant exchange from the mutable region, folding its text into a
       summary note that is re-inserted after the system prompt.
    3. **Report.** If the recent window alone still exceeds the budget, the
       result does not fit and the loop decides what to do. The manager never
       removes the current turn.
    """

    def __init__(
        self,
        *,
        token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
        keep_recent: int = DEFAULT_KEEP_RECENT_MESSAGES,
        tool_result_ttl: int = DEFAULT_TOOL_RESULT_TTL_MESSAGES,
    ) -> None:
        if token_budget <= 0:
            raise ValueError("token_budget must be positive")
        if keep_recent < 0:
            raise ValueError("keep_recent must not be negative")
        if tool_result_ttl < 0:
            raise ValueError("tool_result_ttl must not be negative")
        self.token_budget = token_budget
        self.keep_recent = keep_recent
        self.tool_result_ttl = tool_result_ttl

    def curate(self, messages: list[dict[str, Any]]) -> CuratedContext:
        """Return a budget-curtailed copy of ``messages``.

        The system prompt (any leading ``role: system`` messages) is always
        preserved verbatim: it is the agent's contract, not history. The most
        recent ``keep_recent`` messages are always preserved verbatim: they are
        the working set. Everything between them is candidate for clearing and
        dropping.
        """
        stats = ContextStats(
            budget=self.token_budget,
            input_tokens=estimate_messages_tokens(messages),
            messages_in=len(messages),
        )

        head, body = _split_leading_system(messages)
        if not body:
            stats.output_tokens = stats.input_tokens
            stats.messages_out = len(messages)
            return CuratedContext(messages=list(messages), stats=stats)

        # The mutable region excludes the pinned recent window.
        pinned_count = min(self.keep_recent, len(body))
        mutable = body[: len(body) - pinned_count] if pinned_count else list(body)
        pinned = body[len(body) - pinned_count :] if pinned_count else []

        mutable, stats.cleared_tool_results = self._clear_tool_results(mutable)
        mutable, stats.dropped_turns, summary = self._drop_oldest_until_fits(head, mutable, pinned)
        stats.summarized = summary is not None

        curated = list(head)
        if summary is not None:
            curated.append({"role": "user", "content": summary})
        curated.extend(mutable)
        curated.extend(pinned)

        stats.output_tokens = estimate_messages_tokens(curated)
        stats.messages_out = len(curated)
        return CuratedContext(messages=curated, stats=stats)

    def _clear_tool_results(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], int]:
        """Replace old tool-result bodies with a marker.

        "Old" is measured from the end of the mutable region: the last
        ``tool_result_ttl`` messages are left alone so the model can still read
        the result it most recently acted on.
        """
        if self.tool_result_ttl <= 0:
            cutoff = len(messages)
        else:
            cutoff = max(0, len(messages) - self.tool_result_ttl)
        out: list[dict[str, Any]] = []
        cleared = 0
        for index, message in enumerate(messages):
            if index < cutoff and _is_tool_result(message):
                if str(message.get("content")) != _CLEARED_TOOL_RESULT:
                    cleared += 1
                out.append({**message, "content": _CLEARED_TOOL_RESULT})
            else:
                out.append(message)
        return out, cleared

    def _drop_oldest_until_fits(
        self,
        head: list[dict[str, Any]],
        mutable: list[dict[str, Any]],
        pinned: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        """Drop the oldest exchanges until the result fits, or the region is empty.

        Dropped user/assistant text is collected into a bounded summary note so
        the model retains a trace of what came before. Tool results are not
        summarised (their marker already carries the signal) and are dropped with
        their exchange.
        """
        head_tokens = estimate_messages_tokens(head)
        pinned_tokens = estimate_messages_tokens(pinned)

        def tokens_of(region: list[dict[str, Any]]) -> int:
            return head_tokens + pinned_tokens + estimate_messages_tokens(region)

        dropped_turns = 0
        dropped_lines: list[str] = []
        region = list(mutable)
        while region and tokens_of(region) > self.token_budget:
            consumed = _take_one_turn(region)
            if not consumed:
                # Nothing droppable (a stray assistant/system entry); stop rather
                # than loop forever. The remaining region is what the model sees.
                break
            region = region[len(consumed) :]
            dropped_turns += 1
            for message in consumed:
                line = _summary_line(message)
                if line:
                    dropped_lines.append(line)

        summary = _build_summary(dropped_lines) if dropped_lines else None
        # A newly inserted summary is charged against the budget too. If adding
        # it pushes us back over, the summary is trimmed to fit rather than sent
        # as-is; the note is an aid, never the reason a turn overflows.
        if summary is not None:
            over = tokens_of(region) + estimate_tokens(summary) - self.token_budget
            if over > 0:
                keep_chars = max(0, (estimate_tokens(summary) - over) * CHARS_PER_TOKEN)
                summary = summary[:keep_chars].rstrip() or None
        return region, dropped_turns, summary


def _split_leading_system(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split leading system messages from the rest."""
    index = 0
    while index < len(messages) and messages[index].get("role") == "system":
        index += 1
    return list(messages[:index]), list(messages[index:])


def _take_one_turn(region: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The next droppable unit from the front of the mutable region.

    A turn is one user message plus everything up to (not including) the next
    user message: the user's request and the assistant/tool messages it produced.
    A leading assistant/tool message with no preceding user is taken alone so a
    malformed transcript still makes progress.
    """
    if not region:
        return []
    if region[0].get("role") != "user":
        return [region[0]]
    consumed = [region[0]]
    for message in region[1:]:
        if message.get("role") == "user":
            break
        consumed.append(message)
    return consumed


def _summary_line(message: dict[str, Any]) -> str:
    """One compact line for a dropped message, or empty for tool results."""
    role = message.get("role")
    if role == "tool" or _is_tool_result(message):
        return ""
    content = " ".join(str(message.get("content") or "").split())
    if not content:
        return ""
    label = "User" if role == "user" else "Assistant"
    return f"{label}: {content[:160]}"


def _build_summary(lines: list[str]) -> str:
    """Fold dropped-turn lines into one bounded note.

    The note is a plain ``user`` message placed after the system prompt. It is
    explicitly labelled as prior context so the model treats it as history, not
    as a new instruction from the user.
    """
    joined = "\n".join(lines)
    if len(joined) > _MAX_SUMMARY_CHARS:
        joined = joined[-_MAX_SUMMARY_CHARS:]
    return "[earlier conversation, summarized because it exceeded the context budget]\n" + joined


#: A manager at the default budget, for callers that do not need a custom one.
default_context_manager = ContextManager()
