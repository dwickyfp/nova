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
from app.core.metadata_actor import acting_as, metadata_transaction, service_actor
from app.core.metadata_process import close_process_metadata, open_process_metadata
from app.core.redis import session_store
from app.modules.access_control.security_context import SecurityContext


async def legacy_owners() -> list[str]:
    database = await open_process_metadata("maintenance")
    try:
        actor = service_actor("nova-artifact-converter", "NOVA_WORKER")
        async with metadata_transaction(actor, read_only=True) as (connection, _):
            cursor = await connection.execute(
                "SELECT owner_name FROM nova_meta.legacy_artifact_owners()"
            )
            return [row["owner_name"] for row in await cursor.fetchall()]
    finally:
        await close_process_metadata(database, "artifact-converter-discovery")


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
    user = {"username": owner, "session_id": session_id, "active_role": role, "roles": [role]}
    try:
        with acting_as(SecurityContext.from_session(user)):
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
    owners = args.owner or await legacy_owners()
    database = await open_process_metadata("web")
    try:
        await db.init_system_pool()
        await session_store.init()
        reports = [await convert(owner, force=args.force) for owner in owners]
        print(json.dumps(reports, indent=2, default=str))
    finally:
        await session_store.close()
        await db.close_system_pool()
        await close_process_metadata(database, "artifact-converter")


if __name__ == "__main__":
    asyncio.run(main())
