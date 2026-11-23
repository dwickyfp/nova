"""What a stored answer shows when its thread is read back."""

from __future__ import annotations

import pytest

from app.modules.agents import router

USER = {"username": "alice", "active_role": "analyst", "roles": ["analyst"],
        "session_id": "device", "security_context_version": 1}
PAGE = 'root = Page([h])\nh = Header("Sales overview")'


def test_action_history_displays_message_without_internal_form_envelope():
    from datetime import datetime

    row = {"message_id": "message", "role": "user", "created_at": datetime.now(),
           "content": 'Explain Retail.\n\nUI form input (user supplied):\n{"state":{}}',
           "steps": [{"kind": "ui_action"}]}
    assert router._message_view(row, USER).content == "Explain Retail."
    row["steps"] = []
    assert router._message_view(row, USER).content == row["content"]


@pytest.mark.parametrize(("changed", "visible"), [
    ({}, True),
    # Another sign-in restarts the version count; principal and role still match.
    ({"security_context_version": 2}, True),
    ({"principal": "bob"}, False),
    ({"active_role": "viewer"}, False),
    ({"session_id": "device", "security_context_version": 2}, False),
])
def test_history_projection_keeps_ui_only_in_its_original_security_context(changed, visible):
    from datetime import datetime

    stamp = {"principal": "alice", "active_role": "analyst", "security_context_version": 1,
             "session_id": "another-device", **changed}
    row = {"message_id": "message", "role": "assistant", "content": "Markdown fallback.",
           "created_at": datetime.now(), "security_context": stamp,
           "steps": [{"kind": "event_journal", "protocol_version": 2, "events": [
                         {"event": "run.started", "data": {"seq": 0, "role": "analyst"}},
                         {"event": "part.start", "data": {"seq": 1, "part_id": "r:0", "type": "ui",
                                                          "block": {"fallback": "f"}}},
                         {"event": "part.delta", "data": {"seq": 2, "part_id": "r:0",
                                                          "text": PAGE}},
                         {"event": "part.end",
                          "data": {"seq": 3, "part_id": "r:0", "state": "done"}},
                         {"event": "part.start",
                          "data": {"seq": 4, "part_id": "r:1", "type": "text"}},
                     ]},
                     {"kind": "ui", "program": PAGE, "catalog_version": "nova-layout-1"},
                     {"kind": "answer"}]}
    projection = router._message_view(row, USER)
    assert any(step.get("kind") == "ui" for step in projection.steps) == visible
    assert projection.content == "Markdown fallback."
    assert {"kind": "answer"} in projection.steps
    # The stored v2 journal hides the interactive block with the step that carries it.
    assert projection.protocol_version == 2
    journal = projection.steps[0]["events"]
    assert any(entry["data"].get("text") == PAGE for entry in journal) == visible
    assert [entry["event"] for entry in journal][-1] == "part.start"
    assert len(journal) == (5 if visible else 2)
