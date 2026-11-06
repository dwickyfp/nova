"""Signed drafts of investigation findings that Smart prepares and the user submits.

A finding may cite only results verified in the run that produced it, but the user
submits the draft after that run has ended. The draft tool therefore checks the
citations while the run's verified tables are in hand and signs what it checked,
bound to the caller's identity, active role and security-context version. The
submit endpoint accepts only an intact, unexpired draft for that same caller, so
the server never trusts a client-supplied evidence digest.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta

from fastapi import HTTPException

from app.core.config import settings
from app.modules.intelligence.contracts import Contract, utc_now
from app.modules.intelligence.investigation_contracts import Claim
from app.modules.ontology.contracts import Actor

#: How long a prepared finding stays submittable.
DRAFT_TTL = timedelta(hours=24)
_NAMESPACE = b"nova-investigation-draft:v1:"
UNUSABLE = "This draft can no longer be submitted. Ask Studio to prepare it again."


class FindingDraft(Contract):
    incident_id: str
    summary: str
    claims: list[Claim]
    #: Digest of each cited verified result, taken when the draft was prepared.
    evidence: dict[str, str]
    operation_id: str
    mission_id: str | None = None
    actor: Actor
    expires_at: str


def _mac(body: bytes) -> str:
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, _NAMESPACE + body, hashlib.sha256).hexdigest()


def sign(draft: FindingDraft) -> str:
    body = json.dumps(draft.model_dump(mode="json"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(body).decode() + "." + _mac(body)


def prepare(
    *, incident_id: str, summary: str, claims: list[Claim], evidence: dict[str, str],
    operation_id: str, mission_id: str | None, user: dict,
) -> str:
    return sign(FindingDraft(
        incident_id=incident_id, summary=summary, claims=claims, evidence=evidence,
        operation_id=operation_id, mission_id=mission_id, actor=Actor.from_user(user),
        expires_at=(utc_now() + DRAFT_TTL).isoformat(),
    ))


def verify(token: str, user: dict) -> FindingDraft:
    """The draft as prepared, only for the caller it was prepared for."""
    try:
        encoded, mac = token.rsplit(".", 1)
        body = base64.urlsafe_b64decode(encoded.encode())
        intact = hmac.compare_digest(mac, _mac(body))
        draft = FindingDraft.model_validate(json.loads(body)) if intact else None
    except (ValueError, TypeError):
        draft = None
    if draft is None or draft.actor != Actor.from_user(user):
        raise HTTPException(status_code=403, detail=UNUSABLE)
    if datetime.fromisoformat(draft.expires_at) < utc_now():
        raise HTTPException(status_code=410, detail=UNUSABLE)
    return draft

