"""Metric labels and Redis queue depth must remain useful and bounded."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.task_orchestration.consumer import GraphRunConsumer
from app.observability.http import HTTPMetricsMiddleware, metrics_response


def test_http_metrics_use_route_template_and_do_not_record_scrapes() -> None:
    app = FastAPI()
    app.add_middleware(HTTPMetricsMiddleware)

    @app.get("/items/{item_id}")
    def item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    app.add_api_route("/metrics", metrics_response, methods=["GET"])

    with TestClient(app) as client:
        response = client.get("/items/private-object-723")
        metrics = client.get("/metrics")

    assert response.status_code == 200
    assert metrics.status_code == 200
    assert 'route="/items/{item_id}"' in metrics.text
    assert "private-object-723" not in metrics.text
    assert 'route="/metrics"' not in metrics.text


class _GroupClient:
    def __init__(self, groups: list[dict]) -> None:
        self.groups = groups

    async def xinfo_groups(self, stream_key: str) -> list[dict]:
        assert stream_key == "graph-runs"
        return self.groups


async def test_worker_queue_depth_includes_pending_and_reports_unknown_lag() -> None:
    client = _GroupClient([{"name": "workers", "lag": 7, "pending": 3}])
    consumer = GraphRunConsumer(client, group="workers", stream_key="graph-runs")

    assert await consumer.queue_depth() == 10

    client.groups = [{"name": "workers", "lag": None, "pending": 3}]
    assert await consumer.queue_depth() is None
