"""Drive Studio over its public HTTP API, as a signed-in person does.

Every call goes through the same routes as the Studio client: sign-in and role
activation, the agent and thread routes, the protocol-v2 SSE turn stream, the
human-input answer route and run cancellation. Credentials are read by the
caller and never logged; errors carry the HTTP status and a fixed reason only.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

#: Decide how to answer one human-input request: ``(action, answer)``.
HitlPolicy = Callable[[str, dict[str, Any]], tuple[str, dict[str, Any] | None]]

#: Bound on one turn's event stream, so a runaway turn cannot fill the disk.
MAX_STREAM_BYTES = 12_000_000
#: Sign in again after this long; Nova access tokens expire after about an hour.
SESSION_REFRESH_S = 30 * 60


class StudioError(RuntimeError):
    """A Studio call failed; the message holds no response body."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class TurnResult:
    thread_id: str
    question: str
    events: list[dict[str, Any]] = field(default_factory=list)
    run_id: str | None = None
    started_at: float = 0.0
    total_s: float | None = None
    http_status: int | None = None
    transport_error: str | None = None
    hitl_answers: list[dict[str, Any]] = field(default_factory=list)
    cancelled_by_runner: bool = False


class StudioClient:
    def __init__(self, base_url: str, *, timeout: float = 600.0):
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=20.0),
        )
        self.username: str | None = None
        self.role: str | None = None
        self._credentials: tuple[str, str, str | None] | None = None
        self._signed_in_at = 0.0
        self._relogin = asyncio.Lock()

    async def close(self) -> None:
        await self.http.aclose()

    async def fresh_session(self, *, force: bool = False) -> None:
        """Sign in again before the session token expires, or after a 401."""
        if self._credentials is None:
            return
        async with self._relogin:
            if force or time.monotonic() - self._signed_in_at > SESSION_REFRESH_S:
                await self.login(*self._credentials)

    async def request(self, method: str, path: str, body: Any = None) -> Any:
        if not path.startswith("auth/"):
            await self.fresh_session()
        response = await self.http.request(method, f"/api/v1/{path.lstrip('/')}", json=body)
        if response.status_code == 401 and self._credentials and not path.startswith("auth/"):
            await self.fresh_session(force=True)
            response = await self.http.request(method, f"/api/v1/{path.lstrip('/')}", json=body)
        if response.is_error:
            raise StudioError(
                f"Studio API {method} {path.split('?')[0]}: HTTP {response.status_code}",
                response.status_code,
            )
        return response.json() if response.content else None

    # ── identity ────────────────────────────────────────────────────────

    async def login(self, username: str, password: str, role: str | None = None) -> dict:
        session = await self.request("POST", "auth/login",
                                     {"username": username, "password": password})
        if session.get("status") != "AUTHENTICATED" or not session.get("access_token"):
            raise StudioError(f"{username} could not sign in ({session.get('status')})")
        self.http.headers["Authorization"] = f"Bearer {session['access_token']}"
        wanted = role or session.get("default_role") or session.get("active_role")
        if wanted and session.get("active_role") != wanted:
            await self.request("POST", "auth/switch-role", {"role": wanted})
        identity = await self.request("GET", "auth/me")
        active = identity.get("active_role")
        if identity.get("username") != username or (wanted and active != wanted):
            raise StudioError("The benchmark identity did not activate the requested role")
        self.username, self.role = username, identity.get("active_role")
        self._credentials = (username, password, role)
        self._signed_in_at = time.monotonic()
        return identity

    # ── agents and threads ──────────────────────────────────────────────

    async def agent_by_name(self, name: str) -> dict:
        listing = await self.request("GET", "agents")
        items = listing if isinstance(listing, list) else listing.get("agents", [])
        exact = [a for a in items if a.get("name") == name]
        if len(exact) != 1:
            raise StudioError(f"Expected one agent named {name!r}, found {len(exact)}")
        return exact[0]

    async def new_thread(self, agent_id: str, title: str) -> str:
        thread = await self.request(
            "POST", f"agents/{agent_id}/threads", {"title": title[:120], "learning_enabled": False}
        )
        return str(thread["thread_id"])

    async def thread(self, agent_id: str, thread_id: str) -> dict:
        return await self.request("GET", f"agents/{agent_id}/threads/{thread_id}")

    async def replay(self, agent_id: str, thread_id: str, run_id: str) -> list[dict[str, Any]]:
        """The stored run journal, read back as the Studio client does on reload."""
        out: list[dict[str, Any]] = []
        async with self.http.stream(
            "GET", f"/api/v1/agents/{agent_id}/threads/{thread_id}/runs/{run_id}/events",
            params={"after": -1}, timeout=60.0,
        ) as response:
            if response.is_error:
                raise StudioError(f"Replay: HTTP {response.status_code}", response.status_code)
            async for event in _events(response.aiter_lines()):
                out.append(event)
                if event["event"] in {"run.finished", "done"}:
                    break
        return out

    async def cancel(self, agent_id: str, thread_id: str, run_id: str) -> None:
        await self.request("POST", f"agents/{agent_id}/threads/{thread_id}/runs/{run_id}/cancel")

    async def deep_research(self, agent_id: str, question: str, *,
                            poll_s: float = 5.0, limit_s: float = 900.0) -> dict:
        started = await self.request("POST", f"agents/{agent_id}/deep-research",
                                     {"question": question})
        deadline = time.monotonic() + limit_s
        state = started
        while time.monotonic() < deadline:
            state = await self.request(
                "GET", f"agents/{agent_id}/deep-research/{started['run_id']}")
            if str(state.get("status")) in {"completed", "failed", "cancelled", "done", "error"}:
                break
            await asyncio.sleep(poll_s)
        return {"start": started, "final": state}

    # ── one turn ────────────────────────────────────────────────────────

    async def turn(
        self,
        agent_id: str,
        thread_id: str,
        content: str,
        *,
        hitl: HitlPolicy,
        stop_after_s: float | None = None,
        work_intent: str | None = None,
    ) -> TurnResult:
        """Send one message and record its stream until ``run.finished``.

        Human-input requests are answered as they arrive, through the same
        answer route as the Studio card. ``stop_after_s`` presses Stop.
        """
        await self.fresh_session()
        result = TurnResult(thread_id=thread_id, question=content)
        started = time.monotonic()
        result.started_at = time.time()
        body: dict[str, Any] = {"content": content}
        if work_intent:
            body["work_intent"] = work_intent
        pending: list[asyncio.Task[Any]] = []

        def now() -> float:
            return round(time.monotonic() - started, 3)

        async def answer(run_id: str, request_id: str, kind: str, request: dict) -> None:
            action, payload = hitl(kind, request)
            record = {"request_id": request_id, "kind": kind, "action": action, "answer": payload,
                      "t": now()}
            try:
                await self.request(
                    "POST",
                    f"agents/{agent_id}/threads/{thread_id}/runs/{run_id}/inputs/{request_id}",
                    {"action": action, "answer": payload},
                )
            except StudioError as exc:
                record["error"] = str(exc)
            result.hitl_answers.append(record)

        async def stop_later(run_id: str, delay: float) -> None:
            await asyncio.sleep(max(0.0, delay - now()))
            with contextlib.suppress(StudioError):
                await self.cancel(agent_id, thread_id, run_id)
                result.cancelled_by_runner = True

        try:
            async with self.http.stream(
                "POST", f"/api/v1/agents/{agent_id}/threads/{thread_id}/messages", json=body,
            ) as response:
                result.http_status = response.status_code
                if response.is_error:
                    result.transport_error = f"HTTP {response.status_code}"
                    return result
                size = 0
                async for event in _events(response.aiter_lines()):
                    size += len(json.dumps(event["data"]))
                    if size > MAX_STREAM_BYTES:
                        result.transport_error = "stream exceeded the benchmark bound"
                        break
                    event["t"] = now()
                    result.events.append(event)
                    data = event["data"]
                    if event["event"] == "run.started":
                        result.run_id = data.get("run_id")
                        if stop_after_s is not None and result.run_id:
                            pending.append(asyncio.create_task(
                                stop_later(result.run_id, stop_after_s)))
                    elif event["event"] == "input.required" and result.run_id:
                        raw = data.get("request")
                        request = raw if isinstance(raw, dict) else {}
                        pending.append(asyncio.create_task(answer(
                            result.run_id, str(data.get("request_id")),
                            str(data.get("kind") or request.get("kind") or ""), request,
                        )))
                    elif event["event"] in {"run.finished", "done"}:
                        break
        except httpx.HTTPError as exc:
            result.transport_error = type(exc).__name__
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            result.total_s = now()
        return result


async def _events(lines: Any) -> Any:
    name, data = "message", []
    async for line in lines:
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif not line and data:
            try:
                payload = json.loads("\n".join(data))
            except ValueError:
                payload = {}
            yield {"event": name, "data": payload if isinstance(payload, dict) else {}}
            name, data = "message", []


def parse_sse(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for frame in text.split("\n\n"):
        name, data = "message", []
        for line in frame.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if data:
            try:
                payload = json.loads("\n".join(data))
            except ValueError:
                continue
            out.append({"event": name, "data": payload if isinstance(payload, dict) else {}})
    return out


async def with_retry(call: Callable[[], Awaitable[Any]], *, attempts: int = 3,
                     delay_s: float = 5.0) -> Any:
    """Retry a read or setup call on transport errors and 5xx, never a turn."""
    for attempt in range(attempts):
        try:
            return await call()
        except (httpx.HTTPError, StudioError) as exc:
            status = getattr(exc, "status", None)
            if attempt == attempts - 1 or (status is not None and status < 500):
                raise
            await asyncio.sleep(delay_s * (attempt + 1))
    return None
