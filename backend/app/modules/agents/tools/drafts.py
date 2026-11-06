"""Drafts a Smart turn prepares for the user to submit.

A Smart turn runs unattended and cannot ask for consent, so a tool that would file
something on the user's behalf writes nothing there. It validates the change and
leaves a draft on the turn; Studio shows it as a card whose button files it through
the ordinary endpoint, as the user. The click is the consent.
"""

from __future__ import annotations

from typing import Any

#: Drafts one Smart answer may carry.
MAX_DRAFTS = 3


def add_draft(context: Any, draft: dict[str, Any]) -> bool:
    """Keep a draft on the turn; False when this context cannot carry one."""
    drafts = getattr(context, "smart_drafts", None)
    if not isinstance(drafts, list) or len(drafts) >= MAX_DRAFTS:
        return False
    drafts.append(draft)
    return True
