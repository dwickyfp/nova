"""Repository doubles for facade tests; real PG suites verify transactions and RLS."""

from contextlib import asynccontextmanager

from app.modules.access_control.security_context import SecurityContext
from app.modules.agents import releases, repository, versions


def install_agent_store(monkeypatch, store, *, principal="alice", role="analyst"):
    store.actor = SecurityContext(principal, role, session_id="fixture-login")

    @asynccontextmanager
    async def scope(owner=None, *, read_only=False):
        yield store

    for module in (repository, versions, releases):
        monkeypatch.setattr(module, "agent_metadata", scope)
    return store
