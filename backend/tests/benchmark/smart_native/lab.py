"""The Finance + HR lab, its user, and a small client for the running backend."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DATABASE = "NOVA_SMART_LAB"
USER = "smart_lab_user"
ROLE = "smart_lab_analyst"
SMART = "__smart__"
BASE = os.environ.get("NOVA_SMART_LAB_API", "http://localhost:8000/api/v1")
#: Outside the repository: the lab user's generated password and object ids live here.
STATE = Path(os.environ.get("NOVA_SMART_LAB_STATE", Path.home() / ".cache" / "nova-smart-lab"))


def read_state(name: str) -> dict[str, Any]:
    path = STATE / name
    return json.loads(path.read_text()) if path.exists() else {}


def write_state(name: str, value: dict[str, Any]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / name
    path.write_text(json.dumps(value, indent=1))
    path.chmod(0o600)


def _request(method: str, path: str, body: Any = None, token: str | None = None,
             timeout: float = 300) -> tuple[int, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        BASE + path, method=method, headers=headers,
        data=None if body is None else json.dumps(body).encode(),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def login() -> str:
    """Sign in as the lab user and activate its one role."""
    secret = read_state("user.json").get("password")
    if not secret:
        raise SystemExit("Seed the lab first: python -m tests.benchmark.smart_native.seed")
    status, text = _request("POST", "/auth/login", {"username": USER, "password": secret})
    token = json.loads(text).get("access_token")
    if not token:
        raise SystemExit(f"Lab sign-in failed with HTTP {status}")
    status, _ = _request("POST", "/auth/switch-role", {"role": ROLE}, token)
    if status != 200:
        raise SystemExit(f"Lab role activation failed with HTTP {status}")
    write_state("session.json", {"token": token})
    return token


def api(method: str, path: str, body: Any = None, timeout: float = 300) -> tuple[int, Any]:
    token = read_state("session.json").get("token") or login()
    status, text = _request(method, path, body, token, timeout)
    if status == 401:
        status, text = _request(method, path, body, login(), timeout)
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def ask(agent_id: str, question: str, thread_id: str | None = None,
        timeout: float = 480) -> dict[str, Any]:
    """Send one message and collect the whole event stream."""
    if thread_id is None:
        status, thread = api("POST", f"/agents/{agent_id}/threads", {})
        if status not in (200, 201):
            raise RuntimeError(f"Could not open a thread: HTTP {status}")
        thread_id = thread["thread_id"]
    token = read_state("session.json").get("token") or login()
    request = urllib.request.Request(
        f"{BASE}/agents/{agent_id}/threads/{thread_id}/messages", method="POST",
        data=json.dumps({"content": question}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                 "Accept": "text/event-stream"},
    )
    started, frames = time.time(), []
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        run_id = response.headers.get("X-Nova-Run-ID")
        event, data = None, []
        for raw in response:
            line = raw.decode().rstrip("\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif not line and (event or data):
                text = "\n".join(data)
                try:
                    payload = json.loads(text)
                except ValueError:
                    payload = text
                frames.append({"event": event, "data": payload})
                event, data = None, []
    return {"agent": agent_id, "question": question, "thread_id": thread_id, "run_id": run_id,
            "seconds": round(time.time() - started, 1), "frames": frames}
