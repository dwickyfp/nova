"""Metric labels and Redis queue depth must remain useful and bounded."""

from fastapi import APIRouter, FastAPI
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


def test_http_metrics_preserve_nested_router_prefixes_and_aliases() -> None:
    app = FastAPI()
    app.add_middleware(HTTPMetricsMiddleware)
    router = APIRouter()

    @router.get("/metrics")
    def business_metrics() -> dict:
        return {}

    @router.get("/items/{item_id}")
    def item(item_id: str) -> dict:
        return {"id": item_id}

    parent = APIRouter(prefix="/domain")
    parent.include_router(router, prefix="/v1")
    app.include_router(parent, prefix="/api")
    app.include_router(router, prefix="/compat")
    app.add_api_route("/metrics", metrics_response, methods=["GET"])
    with TestClient(app) as client:
        for path in ("/api/domain/v1/metrics", "/compat/metrics"):
            assert client.get(path).status_code == 200
        assert client.get("/api/domain/v1/items/private-router-item-723").status_code == 200
        metrics = client.get("/metrics").text
    assert 'route="/api/domain/v1/metrics"' in metrics
    assert 'route="/compat/metrics"' in metrics
    assert 'route="/api/domain/v1/items/{item_id}"' in metrics
    assert "private-router-item-723" not in metrics
    assert 'route="/metrics"' not in metrics


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
