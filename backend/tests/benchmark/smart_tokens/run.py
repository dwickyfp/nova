"""Ask Smart the measured requests and keep each run's id and time window.

    cd backend && NOVA_TOKEN_PROBE_PASSWORD=... \\
        uv run python -m tests.benchmark.smart_tokens.run OUT.json [--repeat N]

Each request opens a new Studio thread so earlier turns do not grow the
context. The probe worker records the model calls; ``report`` matches them to
these windows. Signs in as ``NOVA_TOKEN_PROBE_USER`` (default ``nova_admin``)
with its default role; the password is read from the environment only.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from typing import Any

BASE = os.environ.get("NOVA_TOKEN_PROBE_API", "http://localhost:8000/api/v1")
SMART = "__smart__"
TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
#: The measured requests. The dashboard is the case this benchmark was made for.
REQUESTS = {
    "dashboard_two_charts": (
        "Buatkan dashboard Field Sales Overview berisi chart jumlah kunjungan per sales rep "
        "dan chart jumlah outlet per kota"
    ),
}


def _request(method: str, path: str, body: Any = None, token: str | None = None,
             timeout: float = 60) -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        BASE + path, method=method, headers=headers,
        data=None if body is None else json.dumps(body).encode(),
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode() or "null")


def login() -> str:
    password = os.environ.get("NOVA_TOKEN_PROBE_PASSWORD")
    if not password:
        raise SystemExit("Set NOVA_TOKEN_PROBE_PASSWORD for the measuring user.")
    user = os.environ.get("NOVA_TOKEN_PROBE_USER", "nova_admin")
    return _request("POST", "/auth/login", {"username": user, "password": password})[
        "access_token"
    ]


def ask(token: str, question: str, timeout: float = 900) -> dict[str, Any]:
    """Send one Smart message on a new thread and wait for its run to finish."""
    thread = _request("POST", f"/agents/{SMART}/threads", {}, token)["thread_id"]
    request = urllib.request.Request(
        f"{BASE}/agents/{SMART}/threads/{thread}/messages", method="POST",
        data=json.dumps({"content": question}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                 "Accept": "text/event-stream"},
    )
    started = time.time()
    response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
    run_id = response.headers.get("X-Nova-Run-ID")
    # The Smart stream can outlive its run: read it aside and follow the run itself.
    Thread(target=_drain, args=(response,), daemon=True).start()
    status = _wait(token, run_id, started + timeout) if run_id else None
    finished = time.time()
    with suppress(Exception):
        response.close()
    return {"thread_id": thread, "run_id": run_id, "started": round(started, 3),
            "finished": round(finished, 3), "seconds": round(finished - started, 1),
            "status": status}


def _drain(response: Any) -> None:
    with suppress(Exception):
        for _ in response:
            pass


def _tree(token: str, run_id: str) -> dict[str, Any]:
    return _request("GET", f"/agents/smart/runs/{run_id}", None, token)


def _root(tree: dict[str, Any], run_id: str) -> dict[str, Any]:
    return next((item for item in tree.get("runs", []) if item["run_id"] == run_id), {})


def _wait(token: str, run_id: str, deadline: float) -> str | None:
    status = None
    while time.time() < deadline:
        with suppress(urllib.error.HTTPError):
            status = _root(_tree(token, run_id), run_id).get("status")
        if status in TERMINAL:
            return status
        time.sleep(5)
    return status


def stored_tokens(token: str, run_id: str) -> dict[str, int]:
    """Tokens the harness stored per run tree: the numbers its budgets enforce."""
    runs = _tree(token, run_id).get("runs", [])
    root = [item for item in runs if item["depth"] == 0]
    children = [item for item in runs if item["depth"] > 0]

    def total(items: list[dict[str, Any]]) -> int:
        return sum(int(i.get("prompt_tokens") or 0) + int(i.get("completion_tokens") or 0)
                   for i in items)

    return {"root": total(root), "specialists": total(children), "tree": total(runs),
            "specialist_runs": len(children)}


def recorded(token: str, run_id: str) -> dict[str, Any]:
    """The window and outcome of a run that already happened."""
    root = _root(_tree(token, run_id), run_id)
    started = datetime.fromisoformat(str(root["started_at"])).replace(tzinfo=UTC).timestamp()
    finished = datetime.fromisoformat(str(root["updated_at"])).replace(tzinfo=UTC).timestamp()
    return {"thread_id": None, "run_id": run_id, "started": round(started, 3),
            "finished": round(finished, 3), "seconds": round(finished - started, 1),
            "status": root.get("status"), "error_class": root.get("error_class")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--request", choices=sorted(REQUESTS), action="append")
    parser.add_argument("--record", nargs="+", metavar="RUN_ID",
                        help="record runs that already happened instead of asking")
    args = parser.parse_args()
    token = login()
    runs = []
    if args.record:
        for run_id in args.record:
            runs.append({"request": "recorded", "attempt": len(runs) + 1,
                         **recorded(token, run_id)})
    else:
        for name in args.request or sorted(REQUESTS):
            for attempt in range(args.repeat):
                runs.append({"request": name, "attempt": attempt + 1,
                             **ask(token, REQUESTS[name])})
                print(f"{name}#{attempt + 1}: {runs[-1]['status']} in "
                      f"{runs[-1]['seconds']}s", flush=True)
    for run in runs:
        if run["run_id"]:
            run["stored_tokens"] = stored_tokens(token, run["run_id"])
    args.out.write_text(json.dumps({"base": BASE, "runs": runs}, indent=1))


if __name__ == "__main__":
    main()
