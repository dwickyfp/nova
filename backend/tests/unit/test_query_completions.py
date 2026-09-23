from unittest.mock import AsyncMock, patch

import pytest

from app.modules.query.service import QueryService


def test_stage_completion_items_filter_and_append_dot():
    items = QueryService._stage_completion_items(
        ["archive", "sales", "silver"],
        "sa",
    )

    assert items == [
        {
            "label": "sales",
            "type": "stage",
            "insert_text": "sales.",
            "detail": "Stage",
        }
    ]


def test_stage_file_completion_items_distinguish_folders_and_files():
    items = QueryService._stage_file_completion_items(
        [
            {
                "name": "raw",
                "size": 0,
                "last_modified": None,
                "is_dir": True,
            },
            {
                "name": "report.csv",
                "size": 128,
                "last_modified": "2026-06-21T00:00:00",
                "is_dir": False,
            },
        ],
        "r",
    )

    assert items == [
        {
            "label": "raw",
            "type": "stage_folder",
            "insert_text": "raw.",
            "detail": "Folder",
            "size": 0,
            "last_modified": None,
        },
        {
            "label": "report.csv",
            "type": "stage_file",
            "insert_text": "report.csv",
            "detail": "Stage file",
            "size": 128,
            "last_modified": "2026-06-21T00:00:00",
        },
    ]


def test_stage_file_completion_items_limit_results():
    items = [{"name": f"file-{index}.csv", "is_dir": False} for index in range(60)]

    assert len(QueryService._stage_file_completion_items(items, "file")) == 50


@pytest.mark.asyncio
async def test_stage_completion_requires_active_database():
    service = QueryService()

    with (
        patch("app.modules.query.service.decrypt_password", return_value="password"),
        patch.object(service, "_list_stages", new=AsyncMock()) as list_stages,
    ):
        result = await service.get_completions(
            username="analyst",
            encrypted_password="encrypted",
            kind="stage",
            database=None,
            schema=None,
        )

    assert result == {"items": []}
    list_stages.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_stage_files_uses_slash_storage_prefix():
    service = QueryService()
    expected = [{"name": "report.csv", "size": 12, "last_modified": None, "is_dir": False}]

    with (
        patch(
            "app.modules.query.service.db.execute_system",
            new=AsyncMock(return_value={"rows": [["stage-id", "default"]]}),
        ) as execute_system,
        patch(
            "app.modules.stages.service.stage_service.list_files",
            new=AsyncMock(return_value=expected),
        ) as list_files,
        patch("app.modules.query.service.check_stage_access", new=AsyncMock()),
    ):
        result = await service._list_stage_files(
            "sales",
            "analytics",
            folder="raw.daily",
            schema="default", username="analyst", password="pw", role="analyst",
        )

    assert result == expected
    assert execute_system.await_args.args[1] == ["sales", "analytics"]
    list_files.assert_awaited_once_with("stage-id", prefix="raw/daily")


@pytest.mark.asyncio
async def test_list_stages_is_scoped_to_database_only():
    service = QueryService()

    with patch(
        "app.modules.query.service.db.execute_system",
        new=AsyncMock(return_value={"rows": [["raw_data", "default"]]}),
    ) as execute_system, patch(
        "app.modules.query.service.check_stage_access", new=AsyncMock()
    ):
        result = await service._list_stages(
            "NOVA_ANALYTICS", schema="default", username="analyst",
            password="pw", role="analyst",
        )

    assert result == ["raw_data"]
    assert execute_system.await_args.args[1] == ["NOVA_ANALYTICS"]
    assert "schema_name" not in execute_system.await_args.args[0].split("WHERE", 1)[1]


@pytest.mark.asyncio
async def test_stage_completions_hide_denied_scopes():
    from app.modules.stages.access import StageAccessDenied

    service = QueryService()

    async def authorize(stage, **kwargs):
        if stage["schema_name"] == "silver":
            raise StageAccessDenied("Stage access denied")

    with (
        patch(
            "app.modules.query.service.db.execute_system",
            new=AsyncMock(return_value={"rows": [["bronze_stage", "bronze"],
                                                 ["silver_stage", "silver"]]}),
        ),
        patch("app.modules.query.service.check_stage_access", new=authorize),
    ):
        result = await service._list_stages(
            "db", schema=None, username="analyst", password="pw", role="analyst"
        )
    assert result == ["bronze_stage"]
