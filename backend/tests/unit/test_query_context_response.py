"""Each result says which database the script is in once that statement ran.

The editor runs one statement per request when asked to (Run on the cursor's
statement, Run All step by step). A ``USE`` in one request must therefore be
reported back, so the editor can start the next request in that database
instead of failing with "No database selected".
"""

import pytest

from tests.unit.test_query_active_role import EXECUTE_ENDPOINT, _client


@pytest.fixture
def client(monkeypatch):
    test_client, _ = _client(monkeypatch, roles=["analyst"], active_role="analyst")
    return test_client


def test_a_use_alone_reports_the_database_it_selected(client):
    response = client.post(EXECUTE_ENDPOINT, json={"sql": "USE pg_shop.public"})

    assert response.status_code == 200
    (result,) = response.json()
    assert result["success"] is True
    assert result["context_database"] == "pg_shop.public"
    assert result["context_schema"] is None


def test_the_context_follows_the_script_statement_by_statement(client):
    response = client.post(
        EXECUTE_ENDPOINT,
        json={"sql": "SELECT 1; USE sales; SELECT 2", "database": "hr", "schema": "default"},
    )

    results = response.json()
    assert [r["context_database"] for r in results] == ["hr", "sales", "sales"]
    assert [r["context_schema"] for r in results] == ["default", None, None]


def test_without_a_use_the_requested_context_is_echoed(client):
    response = client.post(
        EXECUTE_ENDPOINT, json={"sql": "SELECT 1", "database": "hr", "schema": "default"}
    )

    (result,) = response.json()
    assert (result["context_database"], result["context_schema"]) == ("hr", "default")
