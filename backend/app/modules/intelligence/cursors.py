"""Short-lived, identity-bound cursors do not expose filtered record identifiers."""

import json
from uuid import uuid4

from fastapi import HTTPException

from app.core.redis import session_store
from app.modules.intelligence.contracts import Scope, fingerprint


async def read_cursor(token: str, kind: str, scope: Scope) -> str:
    if not token:
        return ""
    client = session_store._redis
    value = await client.get(f"nova:intelligence:cursor:{token}") if client else None
    if value is None:
        raise HTTPException(status_code=410, detail="Page expired; reload the list")
    cursor = json.loads(value)
    if cursor["kind"] != kind or cursor["scope"] != fingerprint(scope.model_dump()):
        raise HTTPException(status_code=404, detail="Page unavailable")
    return cursor["after"]


async def write_cursor(after: str, kind: str, scope: Scope) -> str:
    client = session_store._redis
    if client is None:
        raise HTTPException(status_code=503, detail="Pagination is temporarily unavailable")
    token = uuid4().hex
    await client.setex(
        f"nova:intelligence:cursor:{token}",
        900,
        json.dumps({"after": after, "kind": kind, "scope": fingerprint(scope.model_dump())}),
    )
    return token
