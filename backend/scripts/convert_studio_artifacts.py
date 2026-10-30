"""Convert Studio artifacts saved before BI for owners who are not signed in.

Each owner's artifacts are converted as that owner, through a delegated BI
session (docs/arch-17-superset.md, S5): the task worker account must be granted
impersonation of the owner (``scripts/provision_task_worker_access.py``), and
the owner's default role must be a BI author role. Owners delegation never
serves (``root``, ``nova_admin``) convert their own artifacts in Studio.

Prints one JSON report per owner: every artifact converted, kept (with the
features BI cannot carry) or failed. ``--force`` converts kept artifacts anyway.

    uv run python scripts/convert_studio_artifacts.py --all-owners
    uv run python scripts/convert_studio_artifacts.py --owner alice --force
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.core.database import db
from app.core.redis import session_store


async def legacy_owners() -> list[str]:
    result = await db.execute_system(
        "SELECT DISTINCT owner_name FROM NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS "
        "WHERE bi_object_id IS NULL ORDER BY owner_name"
    )
    return [str(row[0]) for row in result["rows"]]


async def convert(owner: str, *, force: bool) -> dict:
    from app.modules.superset import tokens
    from app.modules.superset.client import BiError
    from app.modules.superset.conversion import convert_owner
    from app.modules.superset.delegation import DelegationError, authorize

    try:
        role = await authorize(owner)
    except DelegationError as exc:
        return {"owner": owner, "error": f"not delegable: {exc}"}
    session_id = await tokens.create_delegated_session(owner, role)
    user = {"username": owner, "session_id": session_id, "active_role": role}
    try:
        outcomes = await convert_owner(user, force=force)
    except BiError as exc:
        return {"owner": owner, "error": exc.message}
    return {"owner": owner, "role": role, "outcomes": [o.as_dict() for o in outcomes]}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--owner", action="append")
    target.add_argument("--all-owners", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    await db.init_system_pool()
    await session_store.init()
    try:
        from app.modules.agents.artifact_repository import artifact_repository

        await artifact_repository.ensure_schema()
        owners = args.owner or await legacy_owners()
        reports = [await convert(owner, force=args.force) for owner in owners]
        print(json.dumps(reports, indent=2, default=str))
        print(f"Legacy artifacts remaining: {await artifact_repository.legacy_remaining()}")
    finally:
        await session_store.close()
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(main())
