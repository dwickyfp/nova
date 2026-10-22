from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.modules.query import router as query_router


@pytest.mark.parametrize(
    "session",
    [
        {"username": "analyst", "active_role": None},
        {"username": "analyst", "active_role": None, "assigned_roles": ["reader"]},
        {"username": "analyst", "active_role": "ACCOUNTADMIN", "assigned_roles": ["reader"]},
    ],
)
def test_query_requires_an_assigned_active_role(session):
    with pytest.raises(HTTPException) as caught:
        query_router._resolve_active_role(session)
    assert caught.value.status_code == 403


def test_query_runs_under_the_session_active_role():
    session = {"username": "analyst", "active_role": "reader", "assigned_roles": ["reader"]}
    assert query_router._resolve_active_role(session) == "reader"
