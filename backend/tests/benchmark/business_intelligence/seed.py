"""Load only generated observation fixtures into an isolated benchmark engine."""

import argparse
import asyncio
import json
from pathlib import Path

from app.core.database import db
from tests.benchmark.business_intelligence.dataset import load


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    await db.init_system_pool()
    try:
        manifest = await load(db.execute_system, args.data, replace=args.replace)
        print(
            json.dumps(
                {
                    "dataset_hash": manifest["dataset_hash"],
                    "tables": {name: table["rows"] for name, table in manifest["tables"].items()},
                }
            )
        )
    finally:
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(main())
