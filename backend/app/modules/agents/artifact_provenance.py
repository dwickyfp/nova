"""Short-lived proof of the statement behind a Semantic View result.

A Semantic View answer shows rows but never its physical SQL. When the user
saves such a result as an artifact, the chat sends this proof's identity
instead of SQL; the server swaps in the statement it recorded, so the saved
artifact re-runs exactly what produced the rows.

Proofs live in Redis for a day, partitioned by the caller's security context
and session, so another session or principal cannot redeem them.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from fastapi import HTTPException

from app.core.redis import session_store
from app.modules.agents.artifact_repository import validate_artifact_sql
from app.modules.assistant.security import session_security

PROOF_TTL_SECONDS = 86_400


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _scope(user: dict) -> str:
    security = session_security(user)
    return _digest([security.cache_partition(), security.session_id])


def _redis():
    if session_store._redis is None:
        raise HTTPException(503, "Result proofs are unavailable")
    return session_store._redis


def _key(user: dict, identity: str) -> str:
    return f"nova:artifacts:result-proof:{_scope(user)}:{identity}"


async def issue_provenance(result: dict, user: dict) -> str | None:
    """Record the statement behind a result; ``None`` when there is none."""
    sql = result.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return None
    try:
        statement = validate_artifact_sql(sql)
    except ValueError:
        return None
    identity = str(uuid4())
    await _redis().set(
        _key(user, identity), json.dumps({"sql": statement}), ex=PROOF_TTL_SECONDS
    )
    return identity


async def read_provenance(identity: str, user: dict) -> str:
    """The recorded statement for this caller's proof."""
    raw = await _redis().get(_key(user, identity))
    if not raw:
        raise HTTPException(410, "This result expired; run the question again to save it")
    return json.loads(raw)["sql"]
