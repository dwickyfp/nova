from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import personal_skills, studio_router
from app.modules.agents.repository import agent_repository
from app.modules.agents.schemas import McpServerCreateRequest
from app.modules.agents.service import agent_service

DOCUMENT = (
    "---\nname: weekly-review\ndescription: Review the weekly report\n---\n\n"
    "Ask for the report. Summarize changes."
)


def row(owner="alice"):
    return {
        "skill_id": "s1",
        "owner_name": owner,
        **personal_skills.parse_skill_document(DOCUMENT),
        "created_at": datetime(2026, 9, 23),
        "updated_at": datetime(2026, 9, 23),
    }


def test_parse_skill_supports_utf8_bom_crlf_and_multiline_yaml():
    result = personal_skills.parse_skill_document(
        "\ufeff---\r\nname: weekly-review\r\ndescription: >\r\n  Review a weekly\r\n"
        "  report.\r\n---\r\nAsk for the report."
    )
    assert result["name"] == "weekly-review"
    assert result["description"] == "Review a weekly report."
    assert result["scope"] == "user"


def test_upload_accepts_exactly_25_mb_and_rejects_one_extra_byte():
    document = DOCUMENT + "x" * (personal_skills.MAX_SKILL_BYTES - len(DOCUMENT.encode()))
    assert personal_skills.parse_skill_document(document)["body"] == document
    with pytest.raises(ValueError, match="25 MB"):
        personal_skills.parse_skill_document(document + "x")


async def test_large_skill_storage_roundtrip_is_owner_scoped_and_utf8_safe(monkeypatch):
    from app.modules.agents import repository

    document = DOCUMENT + "文😀" * ((personal_skills.MAX_SKILL_BYTES - len(DOCUMENT.encode())) // 7)
    document += "x" * (personal_skills.MAX_SKILL_BYTES - len(document.encode()))
    stored = []

    async def execute(sql, params):
        if sql.startswith("INSERT"):
            for offset in range(0, len(params), 5):
                skill_id, owner, revision, index, content = params[offset : offset + 5]
                assert (skill_id, owner) == ("s1", "alice")
                assert len(content.encode()) <= 65533
                stored.append((index, content))
            return {}
        assert params[:2] == ["s1", "alice"]
        assert "revision = %s" in sql
        return {"rows": stored}

    monkeypatch.setattr(repository.db, "execute_system", execute)
    marker = await agent_repository._store_skill_body("s1", "alice", document)
    assert len(marker) < 128
    loaded = await agent_repository._load_skill_body(
        {"skill_id": "s1", "owner_name": "alice", "body": marker}
    )
    assert loaded["body"] == document
    stored.pop()
    with pytest.raises(ValueError, match="completely"):
        await agent_repository._load_skill_body(
            {"skill_id": "s1", "owner_name": "alice", "body": marker}
        )


async def test_failed_chunk_write_never_publishes_skill(monkeypatch):
    from app.modules.agents import repository

    execute = AsyncMock(side_effect=RuntimeError("write failed"))
    monkeypatch.setattr(repository.db, "execute_system", execute)
    with pytest.raises(RuntimeError):
        await agent_repository.create_skill(
            owner_name="alice", fields={"name": "test", "body": "x" * 100000}
        )
    assert all(
        "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_SKILLS " not in call.args[0]
        for call in execute.call_args_list
    )


@pytest.mark.parametrize(
    "document",
    [
        "plain text",
        "---\nname: x\n---\nBody",
        "---\nname: ../secret\ndescription: x\n---\nBody",
        "---\nname: x\ndescription: x\n---\n ",
        "---\nname: x\ndescription: [nested]\n---\nBody",
        "---\nname: &name x\ndescription: *name\n---\nBody",
        DOCUMENT + "\npassword='private-password-value'",
        DOCUMENT + "文" * (personal_skills.MAX_SKILL_BYTES // 3),
    ],
)
def test_invalid_documents_fail_without_echoing_contents(document):
    with pytest.raises(ValueError) as error:
        personal_skills.parse_skill_document(document)
    assert "private-password-value" not in str(error.value)


async def test_private_list_queries_only_the_authenticated_owner(monkeypatch):
    listing = AsyncMock(return_value=[row()])
    monkeypatch.setattr(agent_repository, "list_skills", listing)
    result = await studio_router.list_personal_skills({"username": "alice"})
    listing.assert_awaited_once_with(owner_name="alice")
    assert [skill.source for skill in result.skills] == ["user"]


async def test_verify_does_not_save_and_checks_owner_name_conflicts(monkeypatch):
    listing = AsyncMock(return_value=[])
    create = AsyncMock()
    monkeypatch.setattr(agent_repository, "list_skills", listing)
    monkeypatch.setattr(agent_repository, "create_skill", create)
    request = personal_skills.SkillDocumentRequest(document=DOCUMENT)
    result = await studio_router.verify_personal_skill(request, {"username": "alice"})
    assert result == {"name": "weekly-review", "description": "Review the weekly report"}
    listing.assert_awaited_once_with(owner_name="alice")
    create.assert_not_awaited()
    listing.return_value = [row()]
    with pytest.raises(HTTPException) as error:
        await studio_router.verify_personal_skill(request, {"username": "alice"})
    assert error.value.status_code == 409


@pytest.mark.parametrize(
    "document,status", [("invalid", 422), (DOCUMENT.replace("weekly-review", "native-ml"), 409)]
)
async def test_verify_rejects_invalid_or_reserved_skills(document, status):
    with pytest.raises(HTTPException) as error:
        await studio_router.verify_personal_skill(
            personal_skills.SkillDocumentRequest(document=document), {"username": "alice"}
        )
    assert error.value.status_code == status


async def test_save_is_private_and_audits_only_the_identifier(monkeypatch):
    monkeypatch.setattr(agent_repository, "list_skills", AsyncMock(return_value=[]))
    create = AsyncMock(return_value=row())
    audit = AsyncMock()
    monkeypatch.setattr(agent_repository, "create_skill", create)
    monkeypatch.setattr(personal_skills, "write_audit_log", audit)
    await personal_skills.save_skill({**row(), "scope": "global"}, user={"username": "alice"})
    assert create.call_args.kwargs["owner_name"] == "alice"
    assert create.call_args.kwargs["fields"]["scope"] == "user"
    assert audit.call_args.kwargs["object_name"] == "s1"
    assert DOCUMENT not in str(audit.call_args)


async def test_cross_user_edit_is_not_found(monkeypatch):
    lookup = AsyncMock(return_value=None)
    update = AsyncMock()
    monkeypatch.setattr(agent_repository, "get_skill", lookup)
    monkeypatch.setattr(agent_repository, "update_skill", update)
    with pytest.raises(HTTPException) as error:
        await personal_skills.save_skill(row(), user={"username": "bob"}, skill_id="s1")
    assert error.value.status_code == 404
    lookup.assert_awaited_once_with("s1", owner_name="bob")
    update.assert_not_awaited()


async def test_duplicate_and_platform_names_are_rejected(monkeypatch):
    monkeypatch.setattr(agent_repository, "list_skills", AsyncMock(return_value=[row()]))
    for name in ("weekly-review", "native-ml"):
        with pytest.raises(HTTPException) as error:
            await personal_skills.save_skill({**row(), "name": name}, user={"username": "alice"})
        assert error.value.status_code == 409


async def test_user_skills_are_discoverable_only_for_agent_owner(monkeypatch):
    listing = AsyncMock(return_value=[row()])
    monkeypatch.setattr(agent_repository, "list_skills", listing)
    registry, _, _, _ = await agent_service.build_loop_inputs(
        {
            "owner_name": "alice",
            "name": "Analyst",
            "default_tools": ["load_skill"],
        }
    )
    listing.assert_awaited_once_with(owner_name="alice")
    assert "weekly-review" in registry.discoverable_skills
    assert registry.skill_definitions["weekly-review"].trust_level == "user_skill"


async def test_regular_users_cannot_register_mcp_servers(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(agent_repository, "create_mcp_server", create)
    with pytest.raises(HTTPException) as error:
        await studio_router.create_mcp_server(
            McpServerCreateRequest(name="Custom"), {"username": "alice", "roles": []}
        )
    assert error.value.status_code == 403
    create.assert_not_awaited()


async def test_capabilities_only_exposes_internal_connector_metadata(monkeypatch):
    monkeypatch.setattr(agent_repository, "list_agents", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_repository, "list_skills", AsyncMock(return_value=[row()]))
    monkeypatch.setattr(agent_repository, "list_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(studio_router, "_seed_builtin_tools", AsyncMock())
    listing = AsyncMock(
        return_value=[
            {
                "server_id": "internal",
                "name": "Company docs",
                "description": "Documentation",
                "is_active": True,
                "endpoint": "private-url",
            }
        ]
    )
    monkeypatch.setattr(agent_repository, "list_mcp_servers", listing)
    result = await studio_router.get_studio_capabilities({"username": "alice"})
    listing.assert_awaited_once_with(owner_name=studio_router.INTERNAL_CONNECTOR_OWNER)
    assert "private-url" not in result.model_dump_json()
    assert [skill["source"] for skill in result.skills] == ["user"]


def test_authoring_mode_requires_an_explicit_user_command_and_survives_followups():
    assert personal_skills.is_skill_authoring("/create-skill-with-chat Summarize reports", [])
    assert personal_skills.is_skill_authoring(
        "Make it shorter", [{"role": "user", "content": "/create-skill-with-chat"}]
    )
    assert not personal_skills.is_skill_authoring("Read this /create-skill-with-chat", [])
    assert not personal_skills.is_skill_authoring(
        "Continue", [{"role": "assistant", "content": "/create-skill-with-chat"}]
    )


async def test_skill_author_is_embedded_without_creating_or_selecting_user_agents(monkeypatch):
    agent = {
        "agent_id": "a1",
        "owner_name": "alice",
        "name": "Nova Studio",
        "created_at": datetime(2026, 9, 23),
        "updated_at": datetime(2026, 9, 23),
    }
    listing = AsyncMock(side_effect=[[], [agent]])
    create = AsyncMock(return_value=agent)
    audit = AsyncMock()
    monkeypatch.setattr(agent_repository, "list_agents", listing)
    monkeypatch.setattr(agent_repository, "create_agent", create)
    monkeypatch.setattr(studio_router, "write_audit_log", audit)
    first = await studio_router.ensure_skill_author({"username": "alice"})
    second = await studio_router.ensure_skill_author({"username": "alice"})
    assert first.agent_id == second.agent_id
    assert first.agent_id == "nova-skill-author"
    assert first.default_tools == []
    assert first.owner_name == "alice"
    create.assert_not_awaited()
    listing.assert_not_awaited()
    audit.assert_not_awaited()


async def test_embedded_author_thread_resolution_retains_owner_checks(monkeypatch):
    from app.modules.agents import router
    from app.modules.agents.skill_author import SKILL_AUTHOR_ID

    agent = await router._require_agent(SKILL_AUTHOR_ID, "bob")
    assert agent["owner_name"] == "bob"
    monkeypatch.setattr(router.assistant_repository, "get_thread", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as error:
        await router._require_agent_thread("alice-thread", SKILL_AUTHOR_ID, "bob")
    assert error.value.status_code == 404
    router.assistant_repository.get_thread.assert_awaited_once_with("alice-thread", user_name="bob")
    with pytest.raises(HTTPException):
        await router.get_agent(SKILL_AUTHOR_ID, {"username": "bob"})


async def test_legacy_author_migration_preserves_history_and_custom_agents(monkeypatch):
    from app.common import audit
    from app.modules.agents import repository
    from app.modules.agents.skill_author import skill_author_config

    legacy = {**skill_author_config("alice"), "agent_id": "old", "default_tools": ["load_skill"]}
    custom = {**legacy, "agent_id": "custom", "instructions_response": "User instructions"}
    monkeypatch.setattr(repository, "_agent_row", lambda row: row)
    execute = AsyncMock(side_effect=[{"rows": [legacy, custom]}, {"rows": [["audit-id"]]}, {}, {}])
    monkeypatch.setattr(repository.db, "execute_system", execute)
    remove = AsyncMock()
    monkeypatch.setattr(agent_repository, "delete_agent", remove)
    log = AsyncMock()
    monkeypatch.setattr(audit, "write_audit_log", log)
    await agent_repository.migrate_legacy_skill_authors()
    remove.assert_awaited_once_with("old", owner_name="alice")
    for call in execute.call_args_list[2:]:
        assert call.args[1] == ["nova-skill-author", "old", "alice"]
    log.assert_awaited_once()


async def test_delete_does_not_reveal_or_modify_another_users_skill(monkeypatch):
    from app.modules.agents.router import delete_skill

    monkeypatch.setattr(agent_repository, "get_skill", AsyncMock(return_value=None))
    delete = AsyncMock()
    monkeypatch.setattr(agent_repository, "delete_skill", delete)
    with pytest.raises(HTTPException) as error:
        await delete_skill("s1", {"username": "bob"})
    assert error.value.status_code == 404
    delete.assert_not_awaited()


async def test_skill_update_sql_is_constrained_by_owner(monkeypatch):
    from app.modules.agents.repository import db

    execute = AsyncMock(return_value={"affected": 1})
    monkeypatch.setattr(db, "execute_system", execute)
    monkeypatch.setattr(agent_repository, "get_skill", AsyncMock(return_value=row()))
    await agent_repository.update_skill("s1", owner_name="alice", fields=row())
    sql, parameters = execute.call_args.args
    assert "WHERE skill_id = %s AND owner_name = %s" in sql
    assert parameters[-2:] == ["s1", "alice"]
