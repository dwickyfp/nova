"""Neva on a native dashboard, live: the real model, the real engine, two people.

``alice`` (as ``regional_manager``) gets a fresh dataset over
``analytics.sales``, one bar chart and three dashboards in her personal
workspace: "Neva QA <run> Sales North", "... Sales South" and one she opens.
Each case asks the Dataviz assistant one question with the page context the
editor sends and checks what Neva does, not only what it says:

* NDV-AGENT-005: with the City filter at Bandung on screen, the total quoted
  is Bandung's (600), not every city's (1,000).
* NDV-AGENT-018: asked to change "my Neva QA <run> Sales dashboard" with none
  open, Neva lists the two that match and changes nothing.
* NDV-AGENT-019 / NDV-REVIS-014 / NDV-DATA-017: a new chat on the dashboard at
  an old revision reads the latest one and creates no dashboard, chart or
  dataset; ``bob`` (who cannot open it) learns nothing about it.
* NDV-REVIS-011: the page on screen was removed; Neva says so and changes
  nothing.
* NDV-AGENT-006 / NDV-AGENT-013: asked for a pie chart, the approval says what
  the chart will show and the page gets a pointer event for that widget.

Every approval is denied, so no turn changes a document. Credentials come
from NOVA_QA_OWNER_PASSWORD (alice) and NOVA_QA_OUTSIDER_PASSWORD (bob) and
are never printed. Everything created is removed afterwards.

Usage (from backend/, with nova-dev running on 5173):
    UV_NO_SYNC=1 uv run python scripts/verify_native_neva_live.py \\
        --output ../docs/reports/native-dataviz/neva-live-attempt-1.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

AGENT = "nova-dataviz-assistant"
SQL = "SELECT city, amount FROM analytics.sales"
RUN = uuid4().hex[:4]


class Probe:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def check(self, name: str, passed: bool, **detail: Any) -> bool:
        self.steps.append({"check": name, "passed": bool(passed), **detail})
        print(f"[{'PASS' if passed else 'FAIL'}] {name}", flush=True)
        return passed


class Session:
    def __init__(self, client: httpx.AsyncClient, token: str) -> None:
        self.client, self.token = client, token

    def headers(self, **extra: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", **extra}

    async def call(self, method: str, path: str, *, body: Any = None, key: str | None = None,
                   revision: int | None = None) -> httpx.Response:
        headers = self.headers()
        if key:
            headers["Idempotency-Key"] = key
        if revision is not None:
            headers["If-Match"] = f'"{revision}"'
        return await self.client.request(method, path, headers=headers, json=body)

    async def turn(self, question: str, native: dict[str, Any] | None) -> dict[str, Any]:
        """One Dataviz assistant turn in a new chat; every approval is denied."""
        created = await self.call("POST", f"/api/v1/agents/{AGENT}/threads",
                                  body={"title": f"Neva QA {RUN}", "learning_enabled": False})
        created.raise_for_status()
        thread = created.json()["thread_id"]
        body: dict[str, Any] = {"content": question}
        if native is not None:
            body["dataviz"] = {"native_dashboard": native}
        frames: list[tuple[str, dict[str, Any]]] = []
        denied: list[str] = []
        started = time.monotonic()
        async with self.client.stream(
            "POST", f"/api/v1/agents/{AGENT}/threads/{thread}/messages", json=body,
            headers=self.headers(Accept="text/event-stream"), timeout=600,
        ) as response:
            if response.status_code != 200:
                await response.aread()
                return {"status": response.status_code, "frames": [], "text": "",
                        "thread": thread}
            event, data = "", []
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif not line and data:
                    try:
                        payload = json.loads("\n".join(data))
                    except ValueError:
                        payload = {}
                    frames.append((event, payload))
                    if event == "tool_call" and payload.get("status") == "pending":
                        denied.append(payload["tool_call_id"])
                        asyncio.create_task(self.deny(payload["tool_call_id"]))
                    event, data = "", []
        text = "".join(p.get("text", "") for e, p in frames if e == "text_delta")
        return {"status": 200, "frames": frames, "text": text, "denied": denied,
                "thread": thread, "seconds": round(time.monotonic() - started, 1)}

    async def deny(self, call_id: str) -> None:
        for _ in range(20):  # the broker registers the call just after the event
            response = await self.call(
                "POST", f"/api/v1/agents/{AGENT}/tool-calls/{call_id}/decision",
                body={"decision": "deny"})
            if response.status_code != 404:
                return
            await asyncio.sleep(0.25)


def tools(result: dict[str, Any]) -> list[str]:
    return [p.get("tool_name", "") for e, p in result["frames"] if e == "tool_call"]


def summary(result: dict[str, Any]) -> dict[str, Any]:
    done = [p for e, p in result["frames"] if e == "done"]
    return {"tools": tools(result),
            "calls": [str(p.get("arguments") or p.get("sql_preview") or "")[:300]
                      for e, p in result["frames"] if e == "tool_call"],
            "finish": done[-1].get("finish_reason") if done else None,
            "answer": result["text"][:600], "seconds": result.get("seconds"),
            "errors": [{"code": p.get("code"), "message": str(p.get("message", ""))[:300]}
                       for e, p in result["frames"] if e == "error"]}


def mentions(text: str, number: str) -> bool:
    plain = text.replace(",", "").replace(".00", "")
    return re.search(rf"(?<![\d.]){re.escape(number)}(?![\d])", plain) is not None


async def login(client: httpx.AsyncClient, user: str, variable: str) -> Session:
    response = await client.post("/api/v1/auth/login",
                                 json={"username": user, "password": os.environ[variable]})
    response.raise_for_status()
    return Session(client, response.json()["access_token"])


async def run(args: argparse.Namespace) -> dict[str, Any]:
    probe = Probe()
    report: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "run": RUN,
                              "scope": "Neva on a native dashboard with the live model",
                              "result": "FAIL", "cleanup": "NOT_RUN", "turns": {}}
    created: list[tuple[str, str]] = []  # (collection, id)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=120) as client:
        alice = await login(client, "alice", "NOVA_QA_OWNER_PASSWORD")
        bob = await login(client, "bob", "NOVA_QA_OUTSIDER_PASSWORD")
        try:
            switched = await alice.call("POST", "/api/v1/auth/switch-role",
                                        body={"role": "regional_manager"})
            probe.check("alice uses regional_manager", switched.status_code == 200)

            async def make(collection: str, definition: dict[str, Any], identity: str) -> None:
                key = str(uuid4())
                response = await alice.call("POST", f"/api/v1/viz/{collection}", key=key,
                                            body={"workspace_id": "personal",
                                                  "definition": definition})
                if response.status_code == 201:
                    created.append((collection, identity))
                probe.check(f"create {collection[:-1]}", response.status_code == 201,
                            status=response.status_code)

            dataset = str(uuid4())
            source = {"kind": "sql", "database": "analytics", "sql": SQL}
            inspected = await alice.call("POST", "/api/v1/viz/datasets/inspect",
                                         body={"dataset_id": dataset, "source": source})
            schema = inspected.json()
            fields = {f["source_column"]: f["field_id"] for f in schema["fields"]}
            made = await alice.call("POST", "/api/v1/viz/datasets", key=str(uuid4()), body={
                "dataset_id": dataset, "source": source, "workspace_id": "personal",
                "title": f"Neva QA {RUN} sales",
                "expected_schema_fingerprint": schema["schema_fingerprint"]})
            if made.status_code == 201:
                created.append(("datasets", dataset))
            probe.check("create dataset", made.status_code == 201)
            chart = str(uuid4())
            await make("charts", {
                "chart_id": chart, "title": "Amount by city", "dataset_id": dataset,
                "dataset_revision": 1, "visual": "bar",
                "encoding": {"kind": "cartesian", "dimensions": [{"field_id": fields["city"]}],
                             "measures": [{"field_id": fields["amount"],
                                           "aggregation": "sum"}]}}, chart)
            widget, page = str(uuid4()), str(uuid4())

            def dashboard(identity: str, title: str) -> dict[str, Any]:
                return {"dashboard_id": identity, "title": title,
                        "datasets": [{"dataset_id": dataset, "revision": 1}],
                        "pages": [{"page_id": page, "title": "Overview", "widgets": [{
                            "kind": "chart", "widget_id": widget,
                            "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
                            "chart_ref": {"chart_id": chart, "revision": 1}}]}]}

            opened = str(uuid4())
            await make("dashboards", dashboard(opened, f"Neva QA {RUN} Overview"), opened)
            for side in ("North", "South"):
                identity = str(uuid4())
                await make("dashboards", dashboard(identity, f"Neva QA {RUN} Sales {side}"),
                           identity)
            # A later revision, so a chat that loaded revision 1 is behind.
            draft = (await alice.call("GET", f"/api/v1/viz/dashboards/{opened}?draft=true")).json()
            key = str(uuid4())
            renamed = await alice.call(
                "POST", f"/api/v1/viz/dashboards/{opened}/operations", key=key,
                revision=draft["revision"],
                body={"operation_id": key, "expected_revision": draft["revision"],
                      "changes": [{"op": "dashboard.rename",
                                   "title": f"Neva QA {RUN} Overview v2"}]})
            probe.check("dashboard moved to revision 2", renamed.status_code == 200,
                        status=renamed.status_code)

            async def count(collection: str) -> int:
                listed = await alice.call(
                    "GET", f"/api/v1/viz/{collection}?workspace_id=personal&draft=true&limit=100")
                return len(listed.json()) if listed.status_code == 200 else -1

            before = {name: await count(name) for name in ("dashboards", "charts", "datasets")}
            context = {"dashboard_id": opened, "revision": 2, "page_id": page,
                       "selected_widget_id": None}

            # NDV-AGENT-005
            bandung = {**context, "view_filters": [{
                "source": "filter", "label": "City", "text": "Bandung", "dataset_id": dataset,
                "field_id": fields["city"], "values": ["Bandung"]}]}
            turn = await alice.turn("What is the total amount I'm looking at right now?",
                                    bandung)
            report["turns"]["AGENT-005"] = summary(turn)
            probe.check("AGENT-005: Neva queried the dashboard data",
                        "native_dataset_query" in tools(turn))
            probe.check("AGENT-005: the total is Bandung's 600, not every city's 1,000",
                        mentions(turn["text"], "600") and not mentions(turn["text"], "1000"),
                        answer=turn["text"][:400])

            # NDV-AGENT-018
            turn = await alice.turn(
                f"Add a KPI of the total amount to my Neva QA {RUN} Sales dashboard.", None)
            report["turns"]["AGENT-018"] = summary(turn)
            probe.check("AGENT-018: Neva looked the name up",
                        "native_dashboard_list" in tools(turn))
            probe.check("AGENT-018: both matching dashboards are named",
                        "North" in turn["text"] and "South" in turn["text"],
                        answer=turn["text"][:400])
            probe.check("AGENT-018: no change was proposed or made", not turn["denied"]
                        and not any(e == "dashboard_changed" for e, _ in turn["frames"]))

            # NDV-AGENT-019 / NDV-REVIS-014 / NDV-DATA-017
            turn = await alice.turn("What is on this dashboard?", {**context, "revision": 1})
            report["turns"]["AGENT-019"] = summary(turn)
            probe.check("AGENT-019: the latest version is described",
                        "amount by city" in turn["text"].lower(),
                        answer=turn["text"][:400])
            after = {name: await count(name) for name in ("dashboards", "charts", "datasets")}
            probe.check("REVIS-014 / DATA-017: no dashboard, chart or dataset was copied",
                        after == before, before=before, after=after)
            turn = await bob.turn("What is on this dashboard and what are its totals?",
                                  {**context, "revision": 2})
            report["turns"]["AGENT-019-bob"] = summary(turn)
            probe.check("AGENT-019: a person who cannot open it learns nothing about it",
                        "Overview" not in turn["text"] and "Amount by city" not in turn["text"]
                        and not mentions(turn["text"], "600")
                        and not mentions(turn["text"], "400"), answer=turn["text"][:400])

            # NDV-REVIS-011
            turn = await alice.turn("Add a KPI of the total amount to this page.",
                                    {**context, "page_id": str(uuid4())})
            report["turns"]["REVIS-011"] = summary(turn)
            probe.check("REVIS-011: no change was saved",
                        not any(e == "dashboard_changed" for e, _ in turn["frames"]))
            probe.check("REVIS-011: Neva says the page is gone or asks which page",
                        any(word in turn["text"].lower()
                            for word in ("no longer", "removed", "which page", "not on")),
                        answer=turn["text"][:400])

            # NDV-AGENT-006 / NDV-AGENT-013
            turn = await alice.turn("Change the Amount by city chart into a pie chart.", context)
            report["turns"]["AGENT-006"] = summary(turn)
            approvals = [p for e, p in turn["frames"]
                         if e == "tool_call" and p.get("tool_name") == "native_chart_update"]
            text = approvals[0].get("sql_preview", "") if approvals else ""
            report["turns"]["AGENT-006"]["approval"] = text
            probe.check("AGENT-006: the approval says what the chart will show",
                        "Amount" in text and ("will show" in text or "still shows" in text),
                        approval=text)
            pointers = [p for e, p in turn["frames"] if e == "dashboard_activity"]
            probe.check("AGENT-013: the pointer targets that chart's widget",
                        bool(pointers) and pointers[0].get("widget_ids") == [widget]
                        and pointers[0].get("action") == "change", pointers=pointers[:2])
            probe.check("AGENT-006: denied, so nothing was saved",
                        not any(e == "dashboard_changed" for e, _ in turn["frames"]))
            final = (await alice.call("GET", f"/api/v1/viz/dashboards/{opened}?draft=true")).json()
            probe.check("the dashboard is still at revision 2", final["revision"] == 2)
            report["result"] = "PASS" if all(s["passed"] for s in probe.steps) else "FAIL"
        except Exception as error:  # noqa: BLE001 - recorded without payloads
            report["error_type"] = type(error).__name__
            report["error"] = str(error)[:200]
        finally:
            removed = []
            for collection, identity in reversed(created):
                path = f"/api/v1/viz/{collection}/{identity}"
                draft = await alice.call("GET", path + "?draft=true")
                if draft.status_code != 200:
                    removed.append(False)
                    continue
                key, revision = str(uuid4()), draft.json()["revision"]
                gone = await alice.call("DELETE", path, key=key, revision=revision,
                                        body={"operation_id": key,
                                              "expected_revision": revision})
                removed.append(gone.status_code == 200)
            report["cleanup"] = "REMOVED" if all(removed) else "PARTIAL"
            if report["cleanup"] != "REMOVED":
                report["result"] = "FAIL"
            for session in (alice, bob):
                await session.call("POST", "/api/v1/auth/logout")
    report["steps"] = probe.steps
    report["finished_at"] = datetime.now(UTC).isoformat()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({"result": report["result"], "cleanup": report["cleanup"],
                      "error": report.get("error")}))
    raise SystemExit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
