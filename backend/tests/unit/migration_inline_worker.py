"""Exercise legacy read response shapes through the worker dispatcher in unit tests."""

from fastapi import HTTPException

from app.modules.migration import router as migration_router
from app.modules.migration.job_worker import MigrationJobWorker
from app.modules.migration.source import SourceConnectionError


def install_inline_read_worker(monkeypatch) -> None:
    async def _read(operation, body, user):
        worker = MigrationJobWorker(None)
        job = {
            "operation": operation,
            "actor": user["username"],
            "active_role": user.get("active_role"),
        }
        try:
            return await worker._read(
                job,
                body.model_dump(mode="json"),
                user.get("session_id"),
                user,
            )
        except SourceConnectionError as exc:
            status = 404 if str(exc).startswith("Unknown migration source") else 502
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    monkeypatch.setattr(migration_router, "_worker_read", _read)
