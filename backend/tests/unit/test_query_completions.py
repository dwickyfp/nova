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


@pytest.fixture
def stages(monkeypatch, fake_metadata_db):
    from app.modules.stages import metadata_service
    from tests.unit.stage_metadata_fakes import install_stages

    rows = []
    install_stages(monkeypatch, rows)
    monkeypatch.setattr(metadata_service, "check_stage_access", AsyncMock())
    return rows


async def test_list_stage_files_uses_slash_storage_prefix(stages):
    service = QueryService()
    expected = [{"name": "report.csv", "size": 12, "last_modified": None, "is_dir": False}]
    stage = {
        "id": "stage-id",
        "name": "sales",
        "database_name": "analytics",
        "schema_name": "default",
    }
    stages.append(stage)
    with (
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
            schema="default",
            username="analyst",
            password="pw",
            role="analyst",
        )
    assert result == expected
    list_files.assert_awaited_once_with(
        {
            **stage,
            "storage_connection": None,
            "base_prefix": None,
            "created_at": None,
            "created_by": None,
        },
        prefix="raw/daily",
    )


async def test_list_stages_is_scoped_to_database_and_requested_schema(stages):
    stages.extend(
        [
            {
                "id": "a",
                "name": "raw_data",
                "database_name": "NOVA_ANALYTICS",
                "schema_name": "default",
            },
            {"id": "b", "name": "foreign", "database_name": "other", "schema_name": "default"},
            {
                "id": "c",
                "name": "other_schema",
                "database_name": "NOVA_ANALYTICS",
                "schema_name": "private",
            },
        ]
    )
    with patch("app.modules.query.service.check_stage_access", new=AsyncMock()):
        assert await QueryService()._list_stages(
            "NOVA_ANALYTICS",
            schema="default",
            username="analyst",
            password="pw",
            role="analyst",
        ) == ["raw_data"]


async def test_stage_completions_hide_denied_scopes(stages):
    from app.modules.stages.access import StageAccessDenied

    stages.extend(
        [
            {"id": schema, "name": schema + "_stage", "database_name": "db", "schema_name": schema}
            for schema in ("bronze", "silver")
        ]
    )

    async def authorize(stage, **kwargs):
        if stage["schema_name"] == "silver":
            raise StageAccessDenied("Stage access denied")

    with patch("app.modules.query.service.check_stage_access", new=authorize):
        assert await QueryService()._list_stages(
            "db",
            schema=None,
            username="analyst",
            password="pw",
            role="analyst",
        ) == ["bronze_stage"]
