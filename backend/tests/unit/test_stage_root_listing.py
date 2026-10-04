from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import deps
from app.modules.stages import router as stage_router


def test_root_listing_authorizes_and_lists_the_stage_prefix(monkeypatch):
    async def caller():
        return {"username": "analyst", "active_role": "reader"}

    authorized = AsyncMock(return_value={"id": "fixture"})
    listing = AsyncMock(return_value=[{"name": "values.csv", "size": 12}])
    monkeypatch.setattr(stage_router, "_authorized_stage", authorized)
    monkeypatch.setattr(stage_router.stage_service, "list_files", listing)
    app = FastAPI()
    app.include_router(stage_router.router, prefix="/stages")
    app.dependency_overrides[deps.get_current_user] = caller
    with TestClient(app) as client:
        response = client.get("/stages/fixture/files")
    assert response.status_code == 200
    assert response.json() == {
        "files": [{"name": "values.csv", "size": 12}],
        "prefix": "",
        "count": 1,
    }
    authorized.assert_awaited_once_with(
        "fixture", {"username": "analyst", "active_role": "reader"}, "read"
    )
    listing.assert_awaited_once_with("fixture", prefix="")
