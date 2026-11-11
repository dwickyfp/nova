"""Provision the Nova Streams writer account (``STREAM_WRITER_USER``).

The writer commits every captured write to a stream source together with its
change journal, in one engine transaction. Nova grants it each source table
and journal through Ranger when a stream is created; this script creates the
account and its role only. Run with administrative engine and Ranger
configuration (the backend's environment):

    uv run python scripts/provision_stream_writer.py --write-env .env

``--write-env`` stores the generated password in that env file (it is never
printed). Without it, pass the password in ``STREAM_WRITER_PASSWORD``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import secrets

from app.common.audit import write_audit_log
from app.common.identifiers import check_identifier
from app.core.config import settings
from app.core.database import db
from app.integrations.ranger.client import ranger_client
from app.integrations.ranger.schemas import RangerRole, RangerRoleMember


def _set_env(path: pathlib.Path, values: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    kept = [line for line in lines if line.split("=", 1)[0] not in values]
    kept += [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(kept) + "\n")
    path.chmod(0o600)


async def provision(user: str, role: str, env_file: str | None) -> None:
    check_identifier(user, field="stream writer user")
    check_identifier(role, field="stream writer role")
    if user.lower() in {"root", "nova_admin"} or role.upper() == "ACCOUNTADMIN":
        raise ValueError("The stream writer must be a dedicated, unprivileged account")
    password = os.environ.get("STREAM_WRITER_PASSWORD") or secrets.token_urlsafe(32)
    await db.execute_system(f"CREATE USER IF NOT EXISTS '{user}' IDENTIFIED BY %s", [password])
    await db.execute_system(f"ALTER USER '{user}' IDENTIFIED BY %s", [password])
    await db.execute_system(f"CREATE ROLE IF NOT EXISTS {role}")
    await db.execute_system(f"GRANT {role} TO USER '{user}'")
    await db.execute_system(f"SET DEFAULT ROLE {role} TO '{user}'")
    await ranger_client.put_user_attributes(user, {})
    existing = await ranger_client.get_role(role)
    ranger_role = RangerRole.model_validate(existing) if existing else RangerRole(name=role)
    if not any(member.name == user for member in ranger_role.users):
        ranger_role.users.append(RangerRoleMember(name=user))
    await ranger_client.put_role(ranger_role)
    if env_file:
        _set_env(pathlib.Path(env_file), {
            "STREAM_WRITER_USER": user, "STREAM_WRITER_PASSWORD": password,
            "STREAM_WRITER_ROLE": role,
        })
    del password
    await write_audit_log(
        event_type="stream", user_name="nova", action="PROVISION_STREAM_WRITER",
        object_type="USER", object_name=user, status="SUCCESS",
    )
    print(f"Stream writer {user} with role {role} is ready"
          + (f"; settings written to {env_file}" if env_file else ""))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default=settings.STREAM_WRITER_USER or "nova_stream_writer")
    parser.add_argument("--role", default=settings.STREAM_WRITER_ROLE or "nova_stream_writer_role")
    parser.add_argument("--write-env", default=None)
    args = parser.parse_args()
    await db.init_system_pool()
    try:
        await provision(args.user, args.role, args.write_env)
    finally:
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(main())
