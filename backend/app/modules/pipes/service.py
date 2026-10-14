"""Pipe administration through the authenticated caller's SQL pipeline."""

from __future__ import annotations

import json

from app.common.identifiers import check_identifier
from app.core.exceptions import NovaException
from app.modules.pipes.schemas import PipeCreate
from app.modules.query.service import query_service
from app.modules.query.sql_pipeline import redact_for_output


class PipeError(NovaException):
    """An invalid request or a redacted SQL execution failure."""

    def __init__(self, message: str):
        super().__init__(message, status_code=400)


def _qualified(name: str, database: str | None) -> str:
    name = check_identifier(name, field="pipe name")
    if database:
        return f"`{check_identifier(database, field='database')}`.`{name}`"
    return f"`{name}`"


def _properties(raw: str | dict | None) -> dict[str, str]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return _properties(parsed)
        except json.JSONDecodeError:
            pass
    properties = {}
    for token in raw.split(";" if ";" in raw else ","):
        if "=" in token:
            key, value = token.split("=", 1)
            properties[key.strip().strip('"')] = value.strip().strip('"')
    return properties


def _pipe(row: dict) -> dict:
    lower = {key.casefold(): value for key, value in row.items()}
    created = lower.get("created_time") or lower.get("create_time")
    return {
        "name": lower.get("pipe_name", lower.get("name", "")),
        "database": lower.get("database_name", lower.get("database", "")),
        "state": lower.get("state", "UNKNOWN"),
        "sql": lower.get("insert_sql") or lower.get("sql") or lower.get("source"),
        "properties": _properties(lower.get("properties")),
        "created_at": str(created) if created else None,
    }


class PipeService:
    async def _query(self, sql: str, *, database: str | None = None, **caller) -> list[dict]:
        result = await query_service.execute(sql=sql, database=database, **caller)
        if result.error:
            raise PipeError(redact_for_output(result.error))
        # SHOW/DESCRIBE may return a persisted source statement containing keys.
        return [
            {
                str(column): redact_for_output(value) if isinstance(value, str) else value
                for column, value in zip(result.columns, row, strict=True)
            }
            for row in result.rows
        ]

    async def list_pipes(self, database: str | None = None, **caller) -> list[dict]:
        sql = "SHOW PIPES"
        if database:
            sql += f" FROM `{check_identifier(database, field='database')}`"
        return [_pipe(row) for row in await self._query(sql, database=database, **caller)]

    async def get_pipe(self, name: str, database: str | None = None, **caller) -> dict | None:
        qualified = _qualified(name, database)
        sql = "SHOW PIPES"
        if database:
            sql += f" FROM `{database}`"
        sql += f" WHERE NAME = '{name}'"
        rows = await self._query(sql, database=database, **caller)
        if not rows:
            return None
        result = _pipe(rows[0])
        details = await self._query(f"DESCRIBE PIPE {qualified}", database=database, **caller)
        if details:
            detail = _pipe(details[0])
            result.update(sql=detail["sql"], properties=detail["properties"])
        return result

    async def create_pipe(self, data: dict, **caller) -> dict:
        body = PipeCreate.model_validate(data)
        database = body.database or None
        qualified = _qualified(body.name, database)
        sql = (
            f"CREATE PIPE {qualified} PROPERTIES ("
            f'"AUTO_INGEST"="{str(body.auto_ingest).lower()}", '
            f'"POLL_INTERVAL"="{body.poll_interval}", '
            f'"BATCH_SIZE"="{body.batch_size}", '
            f'"BATCH_FILES"="{body.batch_files}") AS {body.sql}'
        )
        await self._query(sql, database=database, **caller)
        result = await self.get_pipe(body.name, database=database, **caller)
        return result or {"name": body.name, "database": body.database, "state": "UNKNOWN"}

    async def suspend_pipe(self, name: str, database: str | None = None, **caller) -> dict:
        await self._query(
            f"ALTER PIPE {_qualified(name, database)} SUSPEND", database=database, **caller
        )
        return {"success": True, "message": f"Pipe '{name}' suspended"}

    async def resume_pipe(self, name: str, database: str | None = None, **caller) -> dict:
        await self._query(
            f"ALTER PIPE {_qualified(name, database)} RESUME", database=database, **caller
        )
        return {"success": True, "message": f"Pipe '{name}' resumed"}

    async def drop_pipe(self, name: str, database: str | None = None, **caller) -> dict:
        await self._query(
            f"DROP PIPE {_qualified(name, database)}",
            database=database,
            confirm_destructive=True,
            **caller,
        )
        return {"success": True, "message": f"Pipe '{name}' dropped"}

    async def list_pipe_files(self, name: str, database: str | None = None, **caller) -> list[dict]:
        _qualified(name, database)
        if await self.get_pipe(name, database=database, **caller) is None:
            return []
        where = f"pipe_name = '{name}' AND database_name = "
        where += f"'{database}'" if database else "DATABASE()"
        rows = await self._query(
            "SELECT file_name, load_state, file_size, error_msg, finish_load_time "
            f"FROM information_schema.pipe_files WHERE {where} "
            "ORDER BY staged_time DESC",
            database=database,
            **caller,
        )
        files = []
        for row in rows:
            lower = {key.casefold(): value for key, value in row.items()}
            finished = lower.get("finish_load_time")
            files.append(
                {
                    "file_name": lower.get("file_name", ""),
                    "state": lower.get("load_state", "UNKNOWN"),
                    "file_size": lower.get("file_size"),
                    "error_message": lower.get("error_msg"),
                    "loaded_at": str(finished) if finished else None,
                }
            )
        return files


pipe_service = PipeService()
