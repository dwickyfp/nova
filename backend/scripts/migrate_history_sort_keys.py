from __future__ import annotations

import argparse
import asyncio
import json

from app.common.history_sort_keys import apply_history_sort_keys, history_sort_key_plan
from app.core.database import db


async def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or apply Nova history sort keys")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor", default="nova-history-migration")
    args = parser.parse_args()
    await db.init_system_pool()
    try:
        result = (
            await apply_history_sort_keys(actor=args.actor)
            if args.apply
            else await history_sort_key_plan()
        )
        print(json.dumps(result, indent=2))
    finally:
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(main())
