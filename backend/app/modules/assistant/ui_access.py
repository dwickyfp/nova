"""Interactive answer blocks as the caller may see them, for any thread.

Studio agents and Neva render the same page blocks. A stored block is shown
only in the security context it was made in; a block from a retired catalog is
replayed read-only through the layout renderer (:mod:`ui_legacy`). Page
controls are local to the browser, so nothing here writes state or accepts
actions. Each router first proves the caller owns the thread (an agent thread
or a Neva thread) and then calls this.
"""

from __future__ import annotations

from app.modules.assistant.security import observation_context, session_security


def visible_message(row: dict, user: dict | None) -> tuple[str, list]:
    """A stored message's text and trace as the caller may see them now.

    Interactive blocks stay only in the security context they were made in, the
    form input an older action carried is not shown back as the person's words,
    and the trace is reduced to its public projection.
    """
    from app.modules.agents.public_projections import sanitize_workflow_payload
    from app.modules.assistant.security import same_ui_context
    from app.modules.assistant.ui_legacy import migrate_steps

    steps = row.get("steps") or []
    stamp = row.get("security_context") or {}
    current = observation_context(session_security(user)) if user is not None and any(
        isinstance(item, dict) and item.get("kind") == "ui" for item in steps
    ) else {}
    if not (bool(current) and same_ui_context(stamp, current)):
        from app.modules.assistant.events_v2 import without_part_types

        steps = [
            without_part_types(item, {"ui"}) for item in steps
            if not (isinstance(item, dict) and item.get("kind") == "ui")
        ]
    content = row.get("content") or ""
    if row.get("role") == "user" and any(
        isinstance(item, dict) and item.get("kind") == "ui_action" for item in steps
    ):
        content = content.split("\n\nUI form input (user supplied):\n", 1)[0]
    return content, sanitize_workflow_payload(migrate_steps(steps))
