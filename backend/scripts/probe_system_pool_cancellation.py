"""Read-only live reproduction: a cancelled SELECT must not contaminate the next SELECT."""

import asyncio
import json
from contextlib import suppress

import asyncmy

from app.core.config import settings
from app.core.database import StarRocksConnectionFactory, _timezone_init_command


async def main() -> None:
    factory = StarRocksConnectionFactory()
    factory._system_pool = await asyncmy.create_pool(
        host=settings.STARROCKS_HOST,
        port=settings.STARROCKS_FE_MYSQL_PORT,
        user=settings.STARROCKS_ROOT_USER,
        password=settings.STARROCKS_ROOT_PASSWORD,
        minsize=1,
        maxsize=1,
        autocommit=True,
        init_command=_timezone_init_command(),
    )
    outcomes = []
    try:
        for attempt in range(10):
            cancelled = False
            try:
                await asyncio.wait_for(
                    factory.execute_system("SELECT 'cancelled_query' AS cancel_marker"), 0.001
                )
            except TimeoutError:
                cancelled = True
            await asyncio.sleep(0.1)
            result = await factory.execute_system("SELECT 'control_query' AS control_marker")
            outcomes.append(
                {
                    "attempt": attempt + 1,
                    "cancelled": cancelled,
                    "result": result,
                    "correct": result["columns"] == ["control_marker"]
                    and result["rows"] == [["control_query"]],
                }
            )
        entered = asyncio.Event()

        async def hold_connection() -> None:
            async with factory.system_conn():
                entered.set()
                await asyncio.Event().wait()

        holder = asyncio.create_task(hold_connection())
        await entered.wait()
        waiter = asyncio.create_task(factory.execute_system("SELECT 'waiting_query' AS marker"))
        await asyncio.sleep(.05)
        holder.cancel()
        with suppress(asyncio.CancelledError):
            await holder
        result = await asyncio.wait_for(waiter, 5)
        outcomes.append({"attempt": "queued_acquire", "result": result,
                         "correct": result["rows"] == [["waiting_query"]]})
        entered.clear()
        holder = asyncio.create_task(hold_connection())
        await entered.wait()
        factory._system_pool.close()
        closing = asyncio.create_task(factory._system_pool.wait_closed())
        await asyncio.sleep(.05)
        holder.cancel()
        with suppress(asyncio.CancelledError):
            await holder
        await asyncio.wait_for(closing, 5)
        outcomes.append({"attempt": "queued_shutdown", "correct": True})
    finally:
        await factory.close_system_pool()
    print(
        json.dumps({"attempts": outcomes, "all_correct": all(row["correct"] for row in outcomes)})
    )
    if not all(row["correct"] for row in outcomes):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
