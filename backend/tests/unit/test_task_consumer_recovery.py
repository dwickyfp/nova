"""Redis pending-entry recovery continues past the first page."""

from app.modules.task_orchestration.consumer import GraphRunConsumer


class FakeRedis:
    def __init__(self) -> None:
        self.starts: list[str] = []

    async def xautoclaim(self, stream, group, consumer, *, min_idle_time, start_id, count):
        self.starts.append(start_id)
        if start_id == "0-0":
            return ("9-0", [("1-0", {"graph_run_id": "first"})], [])
        return ("0-0", [("9-0", {"graph_run_id": "last"})], [])


async def test_claim_cursor_advances_and_wraps():
    client = FakeRedis()
    consumer = GraphRunConsumer(client, consumer="worker-1")  # type: ignore[arg-type]

    assert (await consumer.claim_stale())[0][1]["graph_run_id"] == "first"
    assert (await consumer.claim_stale())[0][1]["graph_run_id"] == "last"
    assert (await consumer.claim_stale())[0][1]["graph_run_id"] == "first"
    assert client.starts == ["0-0", "9-0", "0-0"]


def test_default_consumer_names_are_unique():
    client = FakeRedis()
    first = GraphRunConsumer(client)  # type: ignore[arg-type]
    second = GraphRunConsumer(client)  # type: ignore[arg-type]

    assert first._consumer != second._consumer
