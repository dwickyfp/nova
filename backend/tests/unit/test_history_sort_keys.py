from unittest.mock import AsyncMock

from app.common import history_sort_keys as migration


async def test_only_existing_mismatched_tables_are_migrated(monkeypatch):
    execute = AsyncMock(
        return_value={
            "rows": [
                ["CONFIG_ASSISTANT_MESSAGES", "`message_id`"],
                ["CONFIG_AGENT_SESSION_EVENTS", "`root_run_id`, `session_sequence`"],
                ["CONFIG_USER_PREFERENCES", "`user_name`, `pref_key`"],
            ]
        }
    )
    monkeypatch.setattr(migration.db, "execute_system", execute)
    plan = await migration.history_sort_key_plan()
    assert len(plan) == 1
    assert plan[0]["table"] == "CONFIG_ASSISTANT_MESSAGES"
    assert plan[0]["before"] == ("message_id",)
    assert plan[0]["after"] == ("thread_id", "seq")


async def test_completed_migration_is_audited_and_rerun_is_noop(monkeypatch):
    execute = AsyncMock(
        side_effect=[
            {"rows": [["CONFIG_ASSISTANT_MESSAGES", "`message_id`"]]},
            {},
            {"columns": ["State"], "rows": [["FINISHED"]]},
            {"rows": [["CONFIG_ASSISTANT_MESSAGES", "`thread_id`, `seq`"]]},
            {"rows": [["CONFIG_ASSISTANT_MESSAGES", "`thread_id`, `seq`"]]},
        ]
    )
    audit = AsyncMock()
    monkeypatch.setattr(migration.db, "execute_system", execute)
    monkeypatch.setattr(migration, "write_audit_log", audit)
    assert await migration.apply_history_sort_keys(actor="tester") == ["CONFIG_ASSISTANT_MESSAGES"]
    assert await migration.apply_history_sort_keys(actor="tester") == []
    assert [call.kwargs["status"] for call in audit.await_args_list] == ["STARTED", "SUCCESS"]
