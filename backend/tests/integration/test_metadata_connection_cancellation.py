"""A delayed real-engine response cannot leak into a later metadata query."""

import asyncio
from contextlib import suppress

import asyncmy
import pytest

from app.core.database import StarRocksConnectionFactory
from tests.integration._stack import require_shared_stack, shared_stack_host_port

pytestmark = pytest.mark.engine


@pytest.mark.parametrize("waiting_borrower", [False, True])
async def test_cancelled_metadata_response_discards_real_mysql_connection(
    request, docker_services, waiting_borrower,
):
    require_shared_stack(request)
    target_port = shared_stack_host_port("NOVA_TEST_FE_MYSQL_PORT", 29030)
    armed = False
    response_ready, release_response = asyncio.Event(), asyncio.Event()
    connections, handlers = [], set()

    async def relay(reader, writer):
        nonlocal armed
        task = asyncio.current_task()
        handlers.add(task)
        upstream = None
        pumps = []
        try:
            remote, upstream = await asyncio.open_connection("127.0.0.1", target_port)
            connections.append(writer)

            async def pump(source, destination, *, response=False):
                nonlocal armed
                while chunk := await source.read(65536):
                    if response and armed:
                        # Delay bytes without parsing or retaining authentication traffic.
                        armed = False
                        response_ready.set()
                        await release_response.wait()
                    destination.write(chunk)
                    await destination.drain()

            pumps = [
                asyncio.create_task(pump(reader, upstream)),
                asyncio.create_task(pump(remote, writer, response=True)),
            ]
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pending in pumps:
                pending.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for stream in (writer, upstream):
                if stream:
                    stream.close()
                    with suppress(ConnectionError):
                        await stream.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(relay, "127.0.0.1", 0)
    proxy_port = server.sockets[0].getsockname()[1]
    factory = StarRocksConnectionFactory()
    pending = following = None
    try:
        # One connection makes reuse of an interrupted response observable.
        factory._system_pool = await asyncmy.create_pool(
            host="127.0.0.1", port=proxy_port, user="root", password="",
            minsize=1, maxsize=1, autocommit=True, connect_timeout=10,
        )
        assert (await factory.execute_system("SELECT 7 AS control_value"))["rows"] == [[7]]
        armed = True
        pending = asyncio.create_task(factory.execute_system("SELECT 41 AS control_value"))
        await asyncio.wait_for(response_ready.wait(), timeout=20)
        if waiting_borrower:
            following = asyncio.create_task(factory.execute_system("SELECT 99 AS control_value"))
            await asyncio.sleep(0)
            assert not following.done()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        release_response.set()
        if following is None:
            following = asyncio.create_task(factory.execute_system("SELECT 99 AS control_value"))
        result = await asyncio.wait_for(following, timeout=20)
        assert result["rows"] == [[99]] and result["row_count"] == 1
        assert len(connections) == 2
        assert (await factory.execute_system("SELECT 103 AS control_value"))["rows"] == [[103]]
    finally:
        release_response.set()
        for query in (pending, following):
            if query and not query.done():
                query.cancel()
                await asyncio.gather(query, return_exceptions=True)
        await factory.close_system_pool()
        server.close()
        await server.wait_closed()
        for handler in list(handlers):
            handler.cancel()
        await asyncio.gather(*list(handlers), return_exceptions=True)
