"""Superset's REST API, called as the signed-in Nova user.

Nova never calls Superset with a service identity for user content. A client is
opened for one Nova session: it issues a short-lived BI handle for that session
and signs in to Superset with it exactly as the browser does, so Superset's
role for the session's active role, and Ranger behind the proxy, decide what the
call may do. The handle is revoked when the client closes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.core.config import settings

from . import tokens
from .router import BI_COOKIE, BI_PATH

logger = logging.getLogger(__name__)


_NOT_AUTHOR = (
    "Your active role cannot create or change BI content. Ask an administrator "
    "to make it a BI author role."
)


class BiError(Exception):
    """Superset refused or could not complete a request.

    ``status`` is Superset's HTTP status (0 when Superset was unreachable);
    ``message`` is safe to show the user.
    """

    def __init__(self, message: str, *, status: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"BI answered {response.status_code}"
    detail = body.get("message") or body.get("errors") or body.get("msg")
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        detail = detail[0].get("message")
    if isinstance(detail, dict):
        detail = json.dumps(detail)[:500]
    return str(detail or f"BI answered {response.status_code}")[:500]


class SupersetClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http
        self._csrf = ""
        self._nova_database_id: int | None = None

    @classmethod
    @asynccontextmanager
    async def for_user(
        cls, user: dict[str, Any], *, author: bool = True
    ) -> AsyncIterator[SupersetClient]:
        """A client signed in as ``user``'s Nova session.

        ``author`` requests refuse a session whose active role maps to the BI
        viewer role before Superset is called, with a message the user can act on.
        """
        session_id = user.get("session_id")
        if not session_id:
            raise BiError("BI content needs a signed-in Nova session.", status=401)
        if author:
            from .access import SUPERSET_VIEWER, superset_role_for

            if await superset_role_for(str(user.get("active_role") or "")) == SUPERSET_VIEWER:
                raise BiError(_NOT_AUTHOR, status=403)
        try:
            handle = await tokens.issue_handle(session_id)
        except tokens.BiCredentialError as exc:
            raise BiError(str(exc), status=401) from exc
        base = settings.SUPERSET_INTERNAL_URL.rstrip("/")
        http = httpx.AsyncClient(
            base_url=f"{base}{BI_PATH}",
            timeout=settings.SUPERSET_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"Referer": f"{base}{BI_PATH}/"},
        )
        http.cookies.set(BI_COOKIE, handle)
        try:
            yield cls(http)
        finally:
            await http.aclose()
            await tokens.revoke_handle(handle)

    async def request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = {}
        if method != "GET":
            headers["X-CSRFToken"] = await self._csrf_token()
        try:
            response = await self._http.request(method, path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("Superset %s %s failed: %s", method, path, type(exc).__name__)
            raise BiError("BI is unavailable right now. Try again shortly.") from None
        if response.status_code >= 400 or response.status_code in (301, 302):
            if response.status_code in (401, 302):
                raise BiError("The Nova session is not signed in to BI.", status=401)
            if response.status_code == 403:
                raise BiError(_NOT_AUTHOR, status=403)
            raise BiError(_message(response), status=response.status_code)
        if not response.content:
            return {}
        return response.json()

    async def _csrf_token(self) -> str:
        if not self._csrf:
            body = await self.request("GET", "/api/v1/security/csrf_token/")
            self._csrf = str(body.get("result") or "")
        return self._csrf

    async def nova_database_id(self) -> int:
        if self._nova_database_id is None:
            body = await self.request("GET", "/api/v1/database/")
            for row in body.get("result", []):
                if row.get("database_name") == "Nova":
                    self._nova_database_id = int(row["id"])
                    break
            else:
                raise BiError("BI has no Nova database. Run the Superset bootstrap.")
        return self._nova_database_id
