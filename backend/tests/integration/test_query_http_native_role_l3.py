from __future__ import annotations

import pytest


@pytest.mark.engine
async def test_query_http_executes_with_native_rbac_session(client, admin_token):
    client.headers["Authorization"] = f"Bearer {admin_token}"
    response = await client.post(
        "/api/v1/query/execute", json={"sql": "SELECT 42 AS answer", "max_rows": 1}
    )
    assert response.status_code == 200, response.text
    result = response.json()[0]
    assert result["success"] is True, result
    assert result["rows"] == [[42]]
