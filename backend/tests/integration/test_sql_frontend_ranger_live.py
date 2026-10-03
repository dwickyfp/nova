import os
from uuid import uuid4

import httpx
import pytest

pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        not os.getenv("NOVA_FRONTEND_RANGER_URL") or not os.getenv("NOVA_FRONTEND_RANGER_TOKEN"),
        reason=(
            "Requires isolated patched StarRocks/Ranger fixture: NOVA_FRONTEND_RANGER_URL "
            "and NOVA_FRONTEND_RANGER_TOKEN (security-admin session)"
        ),
    ),
]


async def test_frontend_ranger_managed_role_and_strict_rejection():
    role = "frontend_ranger_" + uuid4().hex[:12]
    async with httpx.AsyncClient(
        base_url=os.environ["NOVA_FRONTEND_RANGER_URL"],
        headers={"Authorization": "Bearer " + os.environ["NOVA_FRONTEND_RANGER_TOKEN"]},
        timeout=30,
    ) as client:

        async def run(sql, confirm=False):
            return await client.post(
                "/api/v1/query/execute", json={"sql": sql, "confirm_destructive": confirm}
            )

        try:
            created = await run(f"CREATE ROLE {role}")
            assert created.status_code == 200
            assert created.json()[0]["rows"][0][2] == "Ranger"
            unsupported = await run(f"GRANT ALL ON *.* TO ROLE {role}")
            assert unsupported.status_code >= 400 or not unsupported.json()[0]["success"]
            protected = await run("DROP ROLE ACCOUNTADMIN", True)
            assert protected.status_code == 403
        finally:
            removed = await run(f"DROP ROLE {role}", True)
            assert removed.status_code == 200
