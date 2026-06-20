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
async def test_stage_completion_requires_active_database_and_schema():
    service = QueryService()

    with (
        patch("app.modules.query.service.decrypt_password", return_value="password"),
        patch.object(service, "_list_stages", new=AsyncMock()) as list_stages,
    ):
        result = await service.get_completions(
            username="analyst",
            encrypted_password="encrypted",
            kind="stage",
            database="analytics",
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
            new=AsyncMock(return_value={"rows": [["stage-id"]]}),
        ) as execute_system,
        patch(
            "app.modules.stages.service.stage_service.list_files",
            new=AsyncMock(return_value=expected),
        ) as list_files,
    ):
        result = await service._list_stage_files(
            "sales",
            "analytics",
            "default",
            folder="raw.daily",
        )

    assert result == expected
    assert execute_system.await_args.args[1] == ["sales", "analytics", "default"]
    list_files.assert_awaited_once_with("stage-id", prefix="raw/daily")
