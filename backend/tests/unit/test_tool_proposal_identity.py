from uuid import UUID

import pytest

from app.modules.assistant.service import AssistantLoop, _tool_message


def test_server_proposal_ids_are_independent_of_provider_fields():
    call = {
        "id": "provider-id", "tool_call_id": "forged-public-id",
        "_nova_call_id": "forged-internal-id",
        "function": {"name": "query_execute", "arguments": '{"sql":"SELECT 1"}'},
    }
    first = AssistantLoop._parse_tool_call(call)
    second = AssistantLoop._parse_tool_call(call)
    assert first is not None and second is not None
    assert first.tool_call_id != second.tool_call_id
    for invocation in (first, second):
        assert UUID(invocation.tool_call_id).version == 4
        assert invocation.provider_call_id == "provider-id"
        assert invocation.arguments == {"sql": "SELECT 1"}
        assert _tool_message(True, invocation, {"ok": True})["tool_call_id"] == "provider-id"
    assert call["id"] == "provider-id"


def test_synthetic_call_without_a_wire_id_gets_a_separate_protocol_identity():
    invocation = AssistantLoop._parse_tool_call({"function": {"name": "catalog_search"}})
    assert invocation is not None and invocation.provider_call_id is not None
    assert UUID(invocation.provider_call_id).version == 4
    assert invocation.tool_call_id != invocation.provider_call_id
    assert _tool_message(True, invocation, {"ok": True})["tool_call_id"] == (
        invocation.provider_call_id
    )


@pytest.mark.parametrize("wire_id", [False, 7, {}, [], ""])
def test_malformed_provider_id_is_rejected_before_consent(wire_id):
    assert AssistantLoop._parse_tool_call({
        "id": wire_id, "function": {"name": "query_execute"},
    }) is None
