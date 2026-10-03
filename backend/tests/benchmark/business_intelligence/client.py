"""Benchmark driver using the same authenticated HTTP contracts as Studio."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from uuid import uuid4

import httpx


@dataclass(frozen=True)
class Turn:
    thread_id: str
    message_id: str
    message: dict
    events: list[dict]
    latency_ms: float
    finish_reason: str


class StudioClient:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http

    async def request(self, method: str, path: str, body: dict | None = None):
        response = await self.http.request(method, f"/api/v1/{path}", json=body)
        if response.is_error:
            # Never copy credential entry, stored state, or raw server errors to reports.
            reasons = {
                "Semantic View not found": "semantic_view_unavailable",
                "Semantic version not found": "semantic_version_unavailable",
                "Record unavailable": "record_unavailable",
                "Evidence unavailable": "evidence_unavailable",
                "Intelligence cycle budget exhausted": "cycle_budget_exhausted",
                "Intelligence cycle time budget exhausted; retry the operation": "cycle_deadline",
            }
            try:
                payload = response.json()
                detail = payload.get("detail") if isinstance(payload, dict) else None
            except ValueError:
                detail = None
            reason = reasons.get(detail) if isinstance(detail, str) else None
            raise RuntimeError(
                f"Studio API {method} {path.split('?')[0]}: HTTP {response.status_code}"
                + (f" ({reason})" if reason else "")
            )
        return response.json() if response.content else None

    async def login(self, username: str, password: str, role: str) -> dict:
        session = await self.request(
            "POST", "auth/login", {"username": username, "password": password}
        )
        if session["status"] != "AUTHENTICATED" or not session.get("access_token"):
            raise RuntimeError("Benchmark identity must be ready for authenticated use")
        self.http.headers["Authorization"] = f"Bearer {session['access_token']}"
        await self.request("POST", "auth/switch-role", {"role": role})
        identity = await self.request("GET", "auth/me")
        if identity["username"] != username or identity["active_role"] != role:
            raise RuntimeError("Benchmark identity did not activate the requested role")
        return identity

    async def turn(
        self, agent_id: str, content: str, *, learning: bool, thread_id: str | None = None
    ) -> Turn:
        if not thread_id:
            thread = await self.request(
                "POST",
                f"agents/{agent_id}/threads",
                {
                    "title": "Business learning" if learning else "Isolated business evaluation",
                    "learning_enabled": learning,
                },
            )
            thread_id = thread["thread_id"]
        started = time.monotonic()
        events, event_name, data = [], "message", []
        size = 0
        async with self.http.stream(
            "POST",
            f"/api/v1/agents/{agent_id}/threads/{thread_id}/messages",
            json={"content": content},
        ) as response:
            if response.is_error:
                raise RuntimeError(f"Studio turn failed: HTTP {response.status_code}")
            async for line in response.aiter_lines():
                size += len(line)
                if size > 8_000_000:
                    raise RuntimeError("Studio event stream exceeded benchmark bound")
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif not line and data:
                    events.append({"event": event_name, "data": json.loads("\n".join(data))})
                    data, event_name = [], "message"
        done = [event["data"] for event in events if event["event"] == "done"]
        if len(done) != 1:
            raise RuntimeError("Studio did not persist exactly one completed turn")
        detail = await self.request("GET", f"agents/{agent_id}/threads/{thread_id}")
        message_id = done[0]["message_id"]
        message = next((row for row in detail["messages"] if row["message_id"] == message_id), None)
        if message is None or message["role"] != "assistant":
            raise RuntimeError("Studio completion is missing its durable assistant message")
        return Turn(
            thread_id,
            message_id,
            message,
            events,
            (time.monotonic() - started) * 1000,
            done[0]["finish_reason"],
        )

    async def like(self, agent_id: str, turn: Turn) -> dict:
        return await self.request(
            "PUT",
            f"agents/{agent_id}/threads/{turn.thread_id}/messages/{turn.message_id}/feedback",
            {"feedback": "like"},
        )

    async def review_changes(self, view_id: str, agent_id: str, changes: list[dict]) -> dict:
        view = await self.request("GET", f"semantic-views/{view_id}")
        active = next(row for row in view["versions"] if row["version"] == view["active_version"])
        proposal = await self.request(
            "POST",
            f"semantic-views/{view_id}/autopilot/proposals",
            {
                "operation_id": str(uuid4()),
                "agent_id": agent_id,
                "base": {
                    "view_id": view_id,
                    "version": active["version"],
                    "fingerprint": active["fingerprint"],
                },
                "changes": changes,
            },
        )
        path = f"semantic-views/{view_id}/autopilot/proposals/{proposal['proposal_id']}"
        preview = await self.request("POST", f"{path}/preview")
        if not preview["validation"]["valid"]:
            raise RuntimeError("The teaching proposal failed production semantic validation")
        await self.request(
            "POST",
            f"{path}/approve",
            {
                "proposed_fingerprint": proposal["proposed_fingerprint"],
                "acknowledge_regressions": True,
            },
        )
        return await self.request("GET", f"semantic-views/{view_id}")
