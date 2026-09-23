import json

from app.modules.access_control.security_context import SecurityContext
from app.modules.assistant.security import observation_context, secured_thread
from app.modules.assistant.service import AssistantLoop, LoopContext, _latest_thread_result
from app.modules.assistant.state import AssistantMessage, AssistantThread
from app.modules.assistant.tools import ToolRegistry


def test_finance_observations_never_enter_marketing_context():
    finance = SecurityContext("alice", "finance", session_id="s", security_context_version=6)
    thread = AssistantThread("t", "alice", "Report")
    thread.messages = [AssistantMessage(
        "m", "assistant", "Finance salary is 999999",
        steps=[{"kind": "table", "columns": ["salary"], "rows": [[999999]]}],
        security_context=observation_context(finance),
    )]
    user = {
        "username": "alice", "roles": ["marketing", "finance"],
        "active_role": "marketing", "security_context_version": 7, "session_id": "s",
    }
    loop = AssistantLoop(provider=None, registry=ToolRegistry())
    messages = loop._build_messages(thread, "What did we find?", LoopContext("alice", user=user))
    assert "999999" not in json.dumps(messages)
    assert "salary" not in json.dumps(messages)
    assert _latest_thread_result(secured_thread(thread, SecurityContext.from_session(user))) is None
    assert _latest_thread_result(secured_thread(thread, finance))["rows"] == [[999999]]
