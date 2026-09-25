"""Studio file validation, persistence contract, and agent handoff."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.modules.agents import router as agent_router
from app.modules.assistant import repository as repository_module
from app.modules.assistant.attachments import (
    attachment_prompt,
    provider_user_content,
    validate_attachments,
)
from app.modules.assistant.context import ContextManager, estimate_message_tokens
from app.modules.assistant.intelligence import TurnIntent
from app.modules.assistant.planning import validate_turn_plan
from app.modules.assistant.provider import ProviderConfig
from app.modules.assistant.repository import MESSAGES_DDL, AssistantRepository
from app.modules.assistant.router import _message_view
from app.modules.assistant.schemas import AgentMessageRequest
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantMessage, AssistantThread
from app.modules.assistant.tools import ToolRegistry


def _test_pdf(text: str | None = None) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    if text:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _test_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (1, 1), (255, 0, 0)).save(output, format="PNG")
    return output.getvalue()


def test_pdf_is_extracted_and_scanned_pdf_is_rejected():
    raw = _test_pdf("The answer is 42.")
    request = AgentMessageRequest.model_validate(
        {"attachments": [{
            "name": "facts.pdf", "media_type": "application/pdf",
            "content": base64.b64encode(raw).decode("ascii"),
        }]}
    )
    file = request.prepared_attachments[0]
    assert file["content"] == "The answer is 42."
    assert file["size_bytes"] == len(raw)
    assert "The answer is 42." in attachment_prompt("Read this", [file])
    assert base64.b64encode(raw).decode("ascii") not in attachment_prompt("Read this", [file])
    with pytest.raises(ValidationError, match="no selectable text"):
        AgentMessageRequest.model_validate(
            {"attachments": [{
                "name": "scan.pdf", "media_type": "application/pdf",
                "content": base64.b64encode(_test_pdf()).decode("ascii"),
            }]}
        )


def test_image_is_sent_as_vision_part_without_exposing_bytes_in_text_or_history():
    image = base64.b64encode(_test_png()).decode("ascii")
    request = AgentMessageRequest.model_validate(
        {"content": "What is in this image?", "attachments": [{
            "name": "photo.png", "media_type": "image/png", "content": image,
        }]}
    )
    file = request.prepared_attachments[0]
    prompt = attachment_prompt(request.content, [file])
    provider_content = provider_user_content(prompt, [file])
    assert file["width_pixels"] == file["height_pixels"] == 1
    assert provider_content == [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {
            "url": f"data:image/png;base64,{image}", "detail": "auto",
        }},
    ]
    assert image not in prompt
    assert '"width_pixels": 1' in prompt
    assert image not in _message_view({
        "message_id": "m1", "role": "user", "content": request.content,
        "created_at": "2026-09-23T00:00:00Z", "attachments": [file],
    }).model_dump_json()
    assert estimate_message_tokens({"role": "user", "content": provider_content}) >= 2_048
    curated = ContextManager(token_budget=100, keep_recent=1).curate(
        [{"role": "system", "content": "system"}, {"role": "user", "content": provider_content}]
    )
    assert not curated.stats.fits


def test_image_rejects_bad_encoding_and_mismatched_media_type():
    for content, media_type in (("not-base64", "image/png"), ("YQ==", "image/png"),
                                ("YQ==", "image/jpeg")):
        with pytest.raises(ValidationError):
            AgentMessageRequest.model_validate({"attachments": [{
                "name": "photo.png", "media_type": media_type, "content": content,
            }]})


def test_pdf_and_image_share_one_provider_turn_without_binary_in_text():
    pdf = base64.b64encode(_test_pdf("Red means blocked.")).decode("ascii")
    png = base64.b64encode(_test_png()).decode("ascii")
    files = AgentMessageRequest.model_validate({
        "content": "Can release proceed?",
        "attachments": [
            {"name": "legend.pdf", "media_type": "application/pdf", "content": pdf},
            {"name": "status.png", "media_type": "image/png", "content": png},
        ],
    }).prepared_attachments
    prompt = attachment_prompt("Can release proceed?", files)
    parts = provider_user_content(prompt, files)
    assert isinstance(parts, list)
    assert "Red means blocked." in parts[0]["text"]
    assert "status.png" in parts[0]["text"]
    assert pdf not in parts[0]["text"] and png not in parts[0]["text"]
    assert parts[1]["image_url"]["url"] == f"data:image/png;base64,{png}"


def test_attachment_questions_route_to_the_file_unless_database_access_is_explicit():
    file = validate_attachments([{"name": "brief.txt", "content": "Budget is 12."}])
    assert file[0]["content"] == "Budget is 12."
    document_plan = validate_turn_plan(
        {"intent": "direct_answer", "tools": [], "required_tools": [], "ml_task": None},
        {"query_execute"},
    )
    query_plan = validate_turn_plan(
        {"intent": "raw_sql_query", "tools": ["query_execute"],
         "required_tools": ["query_execute"], "ml_task": None},
        {"query_execute"},
    )
    assert document_plan.route.intent == TurnIntent.DIRECT_ANSWER
    assert query_plan.route.intent == TurnIntent.RAW_SQL_QUERY


def test_attachment_request_rejects_bad_files_and_allows_file_only():
    request = AgentMessageRequest.model_validate(
        {"attachments": [{"name": "facts.txt", "content": "The answer is 42."}]}
    )
    assert request.content == ""
    assert validate_attachments([item.model_dump() for item in request.attachments])[0][
        "size_bytes"
    ] == 17
    assert attachment_prompt("", request.prepared_attachments).startswith("Attached files")
    invalid_files = (
        ("secret.exe", "bad"),
        ("../facts.txt", "bad"),
        ("huge.txt", "a" * 32_769),
    )
    for name, content in invalid_files:
        with pytest.raises(ValidationError):
            AgentMessageRequest.model_validate(
                {"content": "Read this", "attachments": [{"name": name, "content": content}]}
            )
    with pytest.raises(ValidationError):
        AgentMessageRequest.model_validate({"content": "  "})


def test_attachment_history_exposes_metadata_only():
    file = validate_attachments([{"name": "facts.txt", "content": "The answer is 42."}])[0]
    row = {
        "message_id": "m1", "role": "user", "content": "Read this",
        "created_at": "2026-09-23T00:00:00Z", "attachments": [file],
    }
    for view_message in (_message_view, agent_router._message_view):
        view = view_message(row)
        assert view.attachments[0].name == "facts.txt"
        assert "The answer is 42." not in view.model_dump_json()
    assert "attachments       JSON" in MESSAGES_DDL


async def test_attachment_survives_starrocks_message_roundtrip(monkeypatch):
    class RecordingDB:
        inserted = None

        async def execute_system(self, sql, params=None):
            if "COUNT(*) AS n" in sql and "CONFIG_ASSISTANT_MESSAGES" in sql:
                return {"rows": [[params[0], params[1], 0]]}
            if "SELECT COUNT" in sql:
                return {"rows": [[0]]}
            if "INSERT INTO NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES" in sql:
                self.inserted = params
            if "SELECT message_id, role, content" in sql:
                row = self.inserted
                return {"rows": [[
                    row[0], row[4], row[5], row[6], row[8], row[9], row[10],
                    row[11], row[12], row[14], None, row[15],
                ]]}
            return {"rows": [], "affected": 1}

    db = RecordingDB()
    monkeypatch.setattr(repository_module, "db", db)
    file = validate_attachments([{"name": "facts.txt", "content": "The answer is 42."}])[0]
    repo = AssistantRepository()
    await repo.append_message(
        "t1", user_name="alice", role="user", content="Read this", attachments=[file],
    )
    restored = await repo.list_messages("t1", user_name="alice")
    assert restored[0]["content"] == "Read this"
    assert restored[0]["attachments"] == [file]
    assert "The answer is 42." not in _message_view(restored[0]).model_dump_json()


class _FileAnswerProvider:
    def __init__(self):
        self.messages = []

    async def resolve(self, *, provider_id=None, model=None):
        return ProviderConfig(
            provider_id="fake", model="file-reader", endpoint="https://example.test/v1",
            api_key="test",
        )

    async def plan_turn(self, *, user_content, available_tools):
        return {
            "intent": "direct_answer",
            "tools": [],
            "required_tools": [],
            "skills": [],
            "ml_task": None,
        }

    async def stream(self, *, messages, tools=None, provider=None):
        self.messages = messages
        payload = messages[-1]["content"].split("Attached files", 1)[1]
        files = json.loads(payload.split("\n", 1)[1])
        answer = "The file says 42." if "42" in files[0]["content"] else "No answer found."
        yield ("delta", answer)
        yield ("message", {"role": "assistant", "content": answer})


async def test_simple_file_is_attached_sent_to_provider_and_answered(tmp_path):
    path = tmp_path / "facts.txt"
    path.write_text("The answer is 42.", encoding="utf-8")
    request = AgentMessageRequest.model_validate(
        {
            "content": "What does the file say?",
            "attachments": [{"name": path.name, "content": path.read_text()}],
        }
    )
    files = validate_attachments([item.model_dump() for item in request.attachments])
    prompt = attachment_prompt(request.content, files)
    provider = _FileAnswerProvider()
    loop = AssistantLoop(provider=provider, registry=ToolRegistry())
    thread = AssistantThread(thread_id="t1", user_name="alice", title="File test")
    thread.messages.append(AssistantMessage(message_id="u1", role="user", content=prompt))

    async def deny_unexpected_tool(_invocation, _classification):
        raise AssertionError("A text file answer should not call a tool")

    frames = [
        frame async for frame in loop.run(
            thread=thread, user_content=prompt,
            context=LoopContext(user_name="alice", thread_id="t1"),
            resolve_consent=deny_unexpected_tool,
        )
    ]
    user_messages = [message for message in provider.messages if message["role"] == "user"]
    assert sum("The answer is 42." in message["content"] for message in user_messages) == 1
    assert any("The file says 42." in frame for frame in frames)


async def test_pdf_file_is_sent_as_extracted_text_and_answered(tmp_path):
    path = tmp_path / "facts.pdf"
    path.write_bytes(_test_pdf("The answer is 42."))
    request = AgentMessageRequest.model_validate({
        "content": "What does this PDF say?",
        "attachments": [{"name": path.name, "media_type": "application/pdf",
                         "content": base64.b64encode(path.read_bytes()).decode("ascii")}],
    })
    prompt = attachment_prompt(request.content, request.prepared_attachments)
    provider = _FileAnswerProvider()
    loop = AssistantLoop(provider=provider, registry=ToolRegistry())
    thread = AssistantThread(thread_id="pdf", user_name="alice", title="PDF test")
    thread.messages.append(AssistantMessage(message_id="u1", role="user", content=prompt))
    frames = [frame async for frame in loop.run(
        thread=thread, user_content=prompt,
        context=LoopContext(user_name="alice", thread_id="pdf"),
        resolve_consent=lambda *_args: None,
    )]
    assert any("The file says 42." in frame for frame in frames)
    assert "The answer is 42." in provider.messages[-1]["content"]
    assert base64.b64encode(path.read_bytes()).decode("ascii") not in str(provider.messages)


async def test_image_file_reaches_vision_provider_and_agent_answer(tmp_path):
    image_bytes = _test_png()
    path = tmp_path / "photo.png"
    path.write_bytes(image_bytes)
    request = AgentMessageRequest.model_validate({
        "content": "What is in the picture?",
        "attachments": [{"name": path.name, "media_type": "image/png",
                         "content": base64.b64encode(path.read_bytes()).decode("ascii")}],
    })
    files = request.prepared_attachments
    prompt = attachment_prompt(request.content, files)

    class VisionProvider(_FileAnswerProvider):
        async def stream(self, *, messages, tools=None, provider=None):
            self.messages = messages
            parts = messages[-1]["content"]
            assert parts[0]["text"] == prompt
            assert parts[1]["image_url"]["url"] == (
                "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
            )
            yield ("delta", "I can see the picture.")
            yield ("message", {"role": "assistant", "content": "I can see the picture."})

    provider = VisionProvider()
    loop = AssistantLoop(provider=provider, registry=ToolRegistry())
    thread = AssistantThread(thread_id="image", user_name="alice", title="Image test")
    thread.messages.append(AssistantMessage(
        message_id="u1", role="user", content=prompt, attachments=files,
    ))
    frames = [frame async for frame in loop.run(
        thread=thread, user_content=prompt,
        context=LoopContext(user_name="alice", thread_id="image", attachments=files),
        resolve_consent=lambda *_args: None,
    )]
    assert any("I can see the picture." in frame for frame in frames)
    assert any('"finish_reason":"stop"' in frame.replace(" ", "") for frame in frames)
    assert not any("data:image/png;base64" in frame for frame in frames)


async def test_studio_endpoint_persists_attachment_and_streams_answer(monkeypatch):
    class FakeRepository:
        def __init__(self):
            self.messages = []

        async def list_messages(self, _thread_id, *, user_name):
            assert user_name == "alice"
            return []

        async def append_message(self, _thread_id, **fields):
            self.messages.append(fields)

        async def rename_thread(self, *_args, **_kwargs):
            return None

    class FakeRequest:
        async def is_disconnected(self):
            return False

    repo = FakeRepository()
    provider = _FileAnswerProvider()
    runtime = AssistantThread(thread_id="t1", user_name="alice", title="File test")
    agent = {
        "agent_id": "a1", "name": "Reader", "database_name": "sales",
        "schema_name": None, "policy": "auto_read_only", "model_name": "file-reader",
    }
    monkeypatch.setattr(agent_router, "assistant_repository", repo)
    monkeypatch.setattr(agent_router, "assistant_provider", provider)
    monkeypatch.setattr(agent_router, "_require_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(
        agent_router, "_require_agent_thread", AsyncMock(return_value={"title": "File test"})
    )
    monkeypatch.setattr(agent_router, "_resolve_database", AsyncMock(return_value="sales"))
    monkeypatch.setattr(agent_router, "_generate_thread_title", AsyncMock(return_value="File test"))
    monkeypatch.setattr(agent_router, "remember_user_message", AsyncMock())
    monkeypatch.setattr(agent_router, "write_audit_log", AsyncMock())
    monkeypatch.setattr(agent_router.run_journal, "start", AsyncMock())
    monkeypatch.setattr(agent_router.run_journal, "append", AsyncMock())
    monkeypatch.setattr(agent_router.run_journal, "append_batch", AsyncMock())
    monkeypatch.setattr(agent_router.run_journal, "finish", AsyncMock())
    monkeypatch.setattr(agent_router.memory_repository, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        agent_router.agent_service, "build_loop_inputs",
        AsyncMock(return_value=(ToolRegistry(), "Read the file", 60, None)),
    )
    monkeypatch.setattr(agent_router.thread_store, "register", lambda **_kwargs: runtime)

    response = await agent_router.send_agent_message(
        "a1", "t1",
        AgentMessageRequest(
            content="What does this file say?",
            attachments=[{"name": "facts.txt", "content": "The answer is 42."}],
        ),
        FakeRequest(),
        {"username": "alice", "active_role": "analyst", "assigned_roles": ["analyst"],
         "session_id": "s1", "security_context_version": 1},
    )
    frames = [frame async for frame in response.body_iterator]
    assert any("The file says 42." in frame for frame in frames)
    assert repo.messages[0]["attachments"][0]["content"] == "The answer is 42."
    assert repo.messages[0]["content"] == "What does this file say?"
    assert provider.messages[-1]["role"] == "user"
    assert "The answer is 42." in provider.messages[-1]["content"]
    assert any(
        call.kwargs.get("event_type") == "AGENT_ATTACHMENT"
        for call in agent_router.write_audit_log.await_args_list
    )
