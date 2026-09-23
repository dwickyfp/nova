"""Studio dashboard layouts stay bounded and owner-scoped."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.agents.studio_router import update_dashboard
from app.modules.agents.studio_schemas import DashboardLayout, DashboardUpdateRequest


def _tile(tile_id: str, artifact_id: str, x: int, y: int, w: int = 3, h: int = 2) -> dict:
    return {"tile_id": tile_id, "artifact_id": artifact_id, "x": x, "y": y, "w": w, "h": h}


def test_four_default_tiles_fill_the_grid() -> None:
    layout = DashboardLayout(
        tiles=[
            _tile("1", "a", 0, 0),
            _tile("2", "b", 3, 0),
            _tile("3", "c", 0, 2),
            _tile("4", "d", 3, 2),
        ]
    )
    assert len(layout.tiles) == 4


@pytest.mark.parametrize(
    "tiles",
    [
        [_tile("1", "a", 0, 0), _tile("2", "b", 2, 1)],
        [_tile("1", "a", 4, 0)],
        [_tile("1", "a", 0, 3)],
        [_tile("1", "a", 0, 0), _tile("1", "b", 3, 0)],
    ],
)
def test_invalid_or_overlapping_layout_is_rejected(tiles: list[dict]) -> None:
    with pytest.raises(ValidationError):
        DashboardLayout(tiles=tiles)


@pytest.mark.asyncio
async def test_dashboard_cannot_reference_another_users_artifact(monkeypatch) -> None:
    get_dashboard = AsyncMock(return_value={"dashboard_id": "dash", "owner_name": "alice"})
    list_artifacts = AsyncMock(return_value=[{"artifact_id": "mine"}])
    update = AsyncMock()
    monkeypatch.setattr("app.modules.agents.studio_router.dashboard_repository.get", get_dashboard)
    monkeypatch.setattr("app.modules.agents.studio_router.artifact_repository.list", list_artifacts)
    monkeypatch.setattr("app.modules.agents.studio_router.dashboard_repository.update", update)

    body = DashboardUpdateRequest(
        title="Sales",
        layout=DashboardLayout(tiles=[_tile("1", "someone-elses", 0, 0)]),
        expected_updated_at=datetime(2026, 9, 22),
    )
    with pytest.raises(HTTPException) as error:
        await update_dashboard("dash", body, user={"username": "alice"})
    assert error.value.status_code == 422
    update.assert_not_awaited()
    get_dashboard.assert_awaited_once_with("dash", owner_name="alice")
