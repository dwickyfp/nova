"""Bounded, credential-safe Apache Ranger Admin REST client."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings

from .schemas import RangerPolicy, RangerRole

logger = logging.getLogger(__name__)


class RangerError(RuntimeError):
    """Base error for Ranger operations safe to expose to an operator."""


class RangerUnavailableError(RangerError):
    """Ranger could not be reached within the configured retry budget."""


class RangerConflictError(RangerError):
    """Ranger rejected state that conflicts with an existing object."""


class RangerClient:
    """Small async client for the Ranger APIs Nova owns.

    Authentication values are only passed through httpx's auth object and are
    never interpolated into URLs, exceptions, or logs.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._external_client = client

    def _new_client(self) -> httpx.AsyncClient:
        timeout = httpx.Timeout(
            connect=settings.RANGER_CONNECT_TIMEOUT_SECONDS,
            read=settings.RANGER_READ_TIMEOUT_SECONDS,
            write=settings.RANGER_READ_TIMEOUT_SECONDS,
            pool=settings.RANGER_CONNECT_TIMEOUT_SECONDS,
        )
        return httpx.AsyncClient(
            base_url=settings.RANGER_ADMIN_URL.rstrip("/"),
            auth=(settings.RANGER_USERNAME, settings.RANGER_PASSWORD),
            verify=settings.RANGER_TLS_VERIFY,
            timeout=timeout,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        attempts = max(1, settings.RANGER_MAX_RETRIES + 1)
        for attempt in range(attempts):
            owned = self._external_client is None
            client = self._external_client or self._new_client()
            try:
                response = await client.request(method, path, json=json, params=params)
                if response.status_code in expected:
                    if response.status_code == 204 or not response.content:
                        return None
                    return response.json()
                if response.status_code == 409:
                    raise RangerConflictError("Ranger object already exists or changed")
                if response.status_code in (401, 403):
                    raise RangerError("Ranger rejected the configured control-plane identity")
                if response.status_code == 404:
                    return None
                if response.status_code < 500:
                    raise RangerError(
                        f"Ranger rejected {method} {path} with status {response.status_code}"
                    )
                raise httpx.HTTPStatusError(
                    "Ranger server error", request=response.request, response=response
                )
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                if attempt + 1 >= attempts:
                    raise RangerUnavailableError(
                        f"Ranger unavailable after {attempts} bounded attempt(s)"
                    ) from exc
                delay = min(
                    settings.RANGER_RETRY_BACKOFF_SECONDS * (2**attempt),
                    settings.RANGER_RETRY_MAX_BACKOFF_SECONDS,
                )
                logger.warning(
                    "Ranger request failed; retrying method=%s path=%s attempt=%d",
                    method,
                    path,
                    attempt + 1,
                )
                await asyncio.sleep(delay)
            finally:
                if owned:
                    await client.aclose()
        raise AssertionError("unreachable")

    async def health(self) -> dict[str, Any]:
        service = await self.get_service(settings.RANGER_SERVICE_NAME)
        return {
            "reachable": service is not None,
            "service_name": settings.RANGER_SERVICE_NAME,
            "service_exists": service is not None,
        }

    async def get_service_definition(self, name: str = "starrocks") -> dict | None:
        return await self._request(
            "GET", f"/service/plugins/definitions/name/{quote(name, safe='')}"
        )

    async def put_service_definition(self, definition: dict[str, Any]) -> dict:
        existing = await self.get_service_definition(str(definition["name"]))
        if existing and existing.get("id") is not None:
            result = await self._request(
                "PUT",
                f"/service/plugins/definitions/{int(existing['id'])}",
                json={**definition, "id": existing["id"]},
                expected=(200,),
            )
        else:
            result = await self._request(
                "POST", "/service/plugins/definitions", json=definition, expected=(200, 201)
            )
        return dict(result or {})

    async def get_service(self, name: str) -> dict | None:
        return await self._request("GET", f"/service/plugins/services/name/{quote(name, safe='')}")

    async def put_service(self, service: dict[str, Any]) -> dict:
        existing = await self.get_service(str(service["name"]))
        if existing and existing.get("id") is not None:
            result = await self._request(
                "PUT",
                f"/service/plugins/services/{int(existing['id'])}",
                json={**service, "id": existing["id"]},
                expected=(200,),
            )
        else:
            result = await self._request(
                "POST", "/service/plugins/services", json=service, expected=(200, 201)
            )
        return dict(result or {})

    async def list_roles(self) -> list[dict[str, Any]]:
        result = await self._request(
            "GET", "/service/roles/roles", params={"serviceName": settings.RANGER_SERVICE_NAME}
        )
        if isinstance(result, list):
            return [dict(item) for item in result]
        return [dict(item) for item in (result or {}).get("vXRoles", [])]

    async def get_role(self, name: str) -> dict | None:
        return await self._request(
            "GET",
            f"/service/roles/roles/name/{quote(name, safe='')}",
            params={"serviceName": settings.RANGER_SERVICE_NAME},
        )

    async def put_role(self, role: RangerRole) -> dict:
        existing = await self.get_role(role.name)
        payload = role.model_dump(by_alias=True, exclude_none=True)
        if existing and existing.get("id") is not None:
            result = await self._request(
                "PUT",
                f"/service/roles/roles/{int(existing['id'])}",
                json={**payload, "id": existing["id"]},
                params={"serviceName": settings.RANGER_SERVICE_NAME},
                expected=(200,),
            )
        else:
            result = await self._request(
                "POST",
                "/service/roles/roles",
                json=payload,
                params={"serviceName": settings.RANGER_SERVICE_NAME},
                expected=(200, 201),
            )
        return dict(result or {})

    async def delete_role(self, name: str) -> None:
        await self._request(
            "DELETE",
            f"/service/roles/roles/name/{quote(name, safe='')}",
            params={"serviceName": settings.RANGER_SERVICE_NAME},
            expected=(200, 204),
        )

    async def put_user_attributes(self, username: str, attributes: dict[str, str]) -> None:
        """Persist attributes through Ranger's supported usersync endpoint."""
        current = await self._get_user(username)
        other = current.get("otherAttrsMap") or {}
        if not other and current.get("otherAttributes"):
            try:
                other = json.loads(current["otherAttributes"])
            except (TypeError, ValueError):
                other = {}
        merged = {**other, **attributes, "original_name": username, "sync_source": "NOVA"}
        user = {
            **current,
            "name": username,
            "firstName": current.get("firstName") or username,
            "description": current.get("description") or "Managed by Nova",
            "userSource": current.get("userSource") or "1",
            "status": current.get("status") or "1",
            "isVisible": current.get("isVisible") or "1",
            "userRoleList": current.get("userRoleList") or ["ROLE_USER"],
            "syncSource": "NOVA",
            "otherAttrsMap": merged,
            "otherAttributes": json.dumps(merged, sort_keys=True),
        }
        await self._request(
            "POST",
            "/service/xusers/ugsync/users",
            json={"totalCount": 1, "xuserInfoList": [user]},
            expected=(200,),
        )

    async def get_user_attributes(self, username: str) -> dict[str, str]:
        current = await self._get_user(username)
        attributes = current.get("otherAttrsMap") or {}
        if not attributes and current.get("otherAttributes"):
            try:
                attributes = json.loads(current["otherAttributes"])
            except (TypeError, ValueError):
                attributes = {}
        return {str(key): str(value) for key, value in attributes.items()}

    async def _get_user(self, username: str) -> dict[str, Any]:
        result = await self._request(
            "GET", "/service/xusers/users/", params={"name": username, "pageSize": 100}
        )
        users = (result or {}).get("xuserInfoList") or (result or {}).get("vXUsers") or []
        return next((dict(item) for item in users if item.get("name") == username), {})

    async def list_policies(self, *, page_size: int = 100) -> list[dict[str, Any]]:
        policies: list[dict[str, Any]] = []
        start = 0
        while True:
            result = await self._request(
                "GET",
                "/service/public/v2/api/policy",
                params={
                    "serviceName": settings.RANGER_SERVICE_NAME,
                    "pageSize": page_size,
                    "startIndex": start,
                },
            )
            page = result if isinstance(result, list) else (result or {}).get("policies", [])
            policies.extend(dict(item) for item in page)
            if len(page) < page_size:
                break
            start += page_size
        return policies

    async def get_policy(self, name: str) -> dict | None:
        return await self._request(
            "GET",
            "/service/public/v2/api/service/"
            f"{quote(settings.RANGER_SERVICE_NAME, safe='')}/policy/{quote(name, safe='')}",
        )

    async def put_policy(self, policy: RangerPolicy) -> dict:
        existing = await self.get_policy(policy.name)
        payload = policy.to_api()
        if existing and existing.get("id") is not None:
            result = await self._request(
                "PUT",
                f"/service/public/v2/api/policy/{int(existing['id'])}",
                json={**payload, "id": existing["id"]},
                expected=(200,),
            )
        else:
            result = await self._request(
                "POST", "/service/public/v2/api/policy", json=payload, expected=(200, 201)
            )
        return dict(result or {})

    async def delete_policy(self, policy_id: int) -> None:
        await self._request(
            "DELETE", f"/service/public/v2/api/policy/{policy_id}", expected=(200, 204)
        )


ranger_client = RangerClient()
