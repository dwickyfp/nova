from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import asyncmy
import httpx

from app.common.sql_guard import redact_sql_credentials


@dataclass(frozen=True)
class Target:
    host: str = "127.0.0.1"
    port: int = 4406
    user: str = "nova_admin"
    password: str = field(default="", repr=False)
    api_url: str = "http://127.0.0.1:8000"
    planner_timeout_ms: int | None = None
    role: str | None = "ACCOUNTADMIN"

    def safe(self, value: str) -> str:
        value = redact_sql_credentials(value)
        return value.replace(self.password, "***") if self.password else value

    def failure(self, exc: Exception) -> str:
        return self.safe(f"{type(exc).__name__}: {exc}")

    async def connect(self):
        connection = await asyncio.wait_for(
            asyncmy.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                autocommit=True,
                charset="utf8mb4",
                connect_timeout=10,
            ),
            timeout=15,
        )
        if self.role or self.planner_timeout_ms is not None:
            try:
                async with connection.cursor() as cursor:
                    if self.role:
                        quoted_role = self.role.replace("`", "``")
                        await asyncio.wait_for(cursor.execute(f"SET ROLE `{quoted_role}`"), 15)
                    if self.planner_timeout_ms is not None:
                        await asyncio.wait_for(
                            cursor.execute(
                                "SET new_planner_optimize_timeout="
                                + str(int(self.planner_timeout_ms))
                            ),
                            timeout=15,
                        )
            except BaseException:
                connection.close()
                raise
        return connection


class NovaAPI:
    def __init__(self, target: Target):
        self.target = target
        self.client = httpx.AsyncClient(base_url=target.api_url, timeout=120)

    async def login(self) -> None:
        response = await self.client.post(
            "/api/v1/auth/login",
            json={"username": self.target.user, "password": self.target.password},
        )
        response.raise_for_status()
        body = response.json()
        self.client.headers["Authorization"] = "Bearer " + body["access_token"]
        if body.get("must_change_password"):
            raise RuntimeError(
                "Test account requires password rotation; existing password preserved"
            )
        if self.target.role:
            role = await self.request(
                "POST", "/api/v1/auth/switch-role", json={"role": self.target.role}
            )
            assert role["active_role"] == self.target.role, "API role activation differs"

    async def request(self, method: str, path: str, **kwargs):
        response = await self.client.request(method, path, **kwargs)
        if response.is_error:
            raise RuntimeError(f"Nova API {method} {path}: HTTP {response.status_code}")
        return response.json() if response.content else None

    async def sql(self, sql: str, *, confirm: bool = False) -> list:
        body = await self.request(
            "POST",
            "/api/v1/query/execute",
            json={
                "sql": sql,
                "confirm_destructive": confirm,
            },
        )
        if not body or any(not item.get("success") for item in body):
            codes = [item.get("error_code") for item in body or []]
            raise RuntimeError(f"Nova API SQL refused: {codes}")
        return body

    async def close(self) -> None:
        try:
            if "Authorization" in self.client.headers:
                response = await self.client.post("/api/v1/auth/logout")
                response.raise_for_status()
        finally:
            await self.client.aclose()


async def mysql_cli(
    target: Target, sql: str, *, database: str | None = None
) -> tuple[int, str, str]:
    # The option file keeps the password out of process arguments and Docker metadata.
    with tempfile.TemporaryDirectory(prefix="nova-sql-client-") as directory:
        path = Path(directory) / "client.cnf"
        def option_value(value):
            return (
                value.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t")
            )

        path.write_text(
            f'[client]\nuser="{option_value(target.user)}"\n'
            f'password="{option_value(target.password)}"\n'
        )
        path.chmod(0o600)
        host = os.getenv("NOVA_SQL_MYSQL_CLI_HOST", "host.docker.internal")
        args = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--mount",
            f"type=bind,source={path},target=/run/nova-client.cnf,readonly",
            "mysql:8.0",
            "mysql",
            "--defaults-extra-file=/run/nova-client.cnf",
            "--protocol=TCP",
            "--ssl-mode=DISABLED",
            "--batch",
            "--raw",
            "--binary-as-hex",
            "--skip-column-names",
            "--host",
            host,
            "--port",
            str(target.port),
        ]
        if database:
            args.extend(["--database", database])
        if target.role:
            quoted_role = target.role.replace("`", "``")
            args.extend(["--init-command", f"SET ROLE `{quoted_role}`"])
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(sql.encode()), timeout=60)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        return process.returncode or 0, target.safe(stdout.decode()), target.safe(stderr.decode())
