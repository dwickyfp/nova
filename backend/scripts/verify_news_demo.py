"""Run the News demonstration end to end through the API and check the access matrix.

Signs in as an administrator, publishes the four demo Semantic Views, switches News
on for each view and for the demo readers, waits for the scheduled execution account
to press an edition, then reads the newspaper as every demo user.

    NOVA_ADMIN_PASSWORD=... uv run python scripts/verify_news_demo.py

Requires ``seed_news_demo.py`` and a running API, scheduler and worker. This is
not a read-only probe: it creates a Semantic View and changes News settings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE_URL = os.environ.get("NOVA_API_URL", "http://127.0.0.1:8000")
ADMIN = os.environ.get("NOVA_ADMIN_USER", "nova_admin")
CREDENTIAL_PATH = Path(os.environ.get("NEWS_DEMO_CREDENTIAL_FILE", "/tmp/nova-news-demo.env"))
MODELS = Path(__file__).resolve().parents[2] / "workspace/news_demo"
#: view name -> (model file, database, News configuration)
VIEWS = {
    "news_retail_sales": ("retail_sales.ossie.yaml", "news_demo", {
        "metrics": ["revenue"], "count_metric": "order_count",
        "slice_dimensions": ["city", "channel", "category"], "time_dimension": "sale_date",
    }),
    "news_workforce": ("workforce.ossie.yaml", "news_demo", {
        "metrics": ["overtime_hours", "absence_hours"], "count_metric": "headcount_days",
        "slice_dimensions": ["city", "department"], "time_dimension": "work_date",
    }),
    "news_operating_expenses": ("operating_expenses.ossie.yaml", "news_demo", {
        "metrics": ["operating_expense"], "count_metric": "transaction_count",
        "slice_dimensions": ["city", "cost_center"], "time_dimension": "posting_date",
    }),
    "news_service_reliability": ("service_reliability.ossie.yaml", "news_engineering", {
        "metrics": ["incident_count", "downtime_minutes"], "count_metric": "monitored_checks",
        "slice_dimensions": ["service", "team"], "time_dimension": "event_date",
    }),
}
ENGINEERING = "news_service_reliability"
ENTITLED = ("news_manager", "news_bandung", "news_jakarta", "news_outsider")
#: Who must see the Bandung story on the newest edition.
EXPECT_BANDUNG = {
    "news_manager": True,
    "news_bandung": True,
    "news_jakarta": False,
    "news_outsider": False,
}
CONFIG = {
    "execution_role": "news_editor",
    "cadence_minutes": 15,
    "max_stories": 24,
    "narrative": "model",
}


async def login(client: httpx.AsyncClient, username: str, password: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    response.raise_for_status()
    body = response.json()
    if body.get("status") != "AUTHENTICATED":
        raise RuntimeError(f"{username}: sign-in returned {body.get('status')}")
    return {"Authorization": f"Bearer {body['access_token']}"}


async def publish_view(client: httpx.AsyncClient, admin: dict[str, str], name: str) -> str:
    model_file, database, _config = VIEWS[name]
    views = (await client.get("/api/v1/semantic-views", headers=admin)).json()
    view = next((row for row in views if row["name"] == name), None)
    if view is None:
        created = await client.post(
            "/api/v1/semantic-views",
            headers=admin,
            json={"name": name, "database": database,
                  "definition": (MODELS / model_file).read_text()},
        )
        created.raise_for_status()
        view = created.json()
    if not view.get("active_version"):
        detail = (await client.get(f"/api/v1/semantic-views/{view['id']}", headers=admin)).json()
        version = max(row["version"] for row in detail["versions"])
        base = f"/api/v1/semantic-views/{view['id']}/versions/{version}"
        (await client.post(f"{base}/validate", headers=admin)).raise_for_status()
        (await client.post(f"{base}/publish", headers=admin, json={})).raise_for_status()
    return view["id"]


def subjects(paper: dict) -> list[dict]:
    return [story for section in paper["sections"] for story in section["stories"]]


async def run(wait_minutes: int) -> int:
    password = os.environ.get("NOVA_ADMIN_PASSWORD")
    if not password:
        print("Set NOVA_ADMIN_PASSWORD for the administrator sign-in.", file=sys.stderr)
        return 2
    users = dict(
        line.split("=", 1) for line in CREDENTIAL_PATH.read_text().splitlines() if "=" in line
    )
    report: dict = {"checks": []}

    def check(name: str, passed: bool, detail: str = "") -> None:
        report["checks"].append({"check": name, "passed": bool(passed), "detail": detail})

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=180) as client:
        admin = await login(client, ADMIN, password)
        view_ids = {name: await publish_view(client, admin, name) for name in VIEWS}
        for name in users:
            response = await client.put(
                f"/api/v1/users/{name}", headers=admin, json={"news_enabled": name in ENTITLED}
            )
            response.raise_for_status()
        for name, view_id in view_ids.items():
            enabled = await client.put(
                f"/api/v1/semantic-views/{view_id}/news",
                headers=admin,
                json={"enabled": True, "config": CONFIG | VIEWS[name][2]},
            )
            check(f"news switched on for {name}", enabled.status_code == 200, enabled.text[:160])
            if enabled.status_code != 200:
                print(json.dumps(report, indent=2))
                return 1

        sessions = {name: await login(client, name, secret) for name, secret in users.items()}
        deadline = time.monotonic() + wait_minutes * 60
        paper: dict = {"sections": []}
        while time.monotonic() < deadline:
            response = await client.get(
                "/api/v1/intelligence/newspaper", headers=sessions["news_manager"]
            )
            response.raise_for_status()
            paper = response.json()
            if sum(1 for section in paper["sections"] if section["stories"]) == len(VIEWS):
                break
            await asyncio.sleep(30)
        stories = subjects(paper)
        desks = {section["name"]: len(section["stories"]) for section in paper["sections"]}
        check("the edition carries all four desks for the manager",
              set(desks) == set(VIEWS) and all(desks.values()), json.dumps(desks))
        retail = next(
            (section["stories"] for section in paper["sections"]
             if section["name"] == "news_retail_sales"), [],
        )
        bandung = next(
            (row for row in retail
             if row["slice"] and row["slice"]["value"] == "Bandung" and row["change"] < 0),
            None,
        )
        check("the Bandung drop is in the edition", bandung is not None)
        report["edition_date"] = paper.get("edition_date")
        report["manager_stories"] = [
            {"desk": row.get("view_name"), "headline": row["narrative"]["headline"],
             "severity": row["severity"], "impact": row.get("impact"),
             "written_by": row["narrative_source"]}
            for row in stories
        ]
        for name, expected in EXPECT_BANDUNG.items():
            started = time.monotonic()
            response = await client.get("/api/v1/intelligence/newspaper", headers=sessions[name])
            elapsed = round((time.monotonic() - started) * 1000)
            body = response.json() if response.status_code == 200 else {"sections": []}
            shown = subjects(body)
            sees = any(
                row["slice"] and row["slice"]["value"] == "Bandung"
                for section in body["sections"]
                if section["name"] == "news_retail_sales"
                for row in section["stories"]
            )
            names = {section["name"] for section in body["sections"]}
            if name != "news_manager":
                check(f"{name} gets no engineering desk", ENGINEERING not in names)
            if name in ("news_bandung", "news_jakarta"):
                city = "Bandung" if name == "news_bandung" else "Jakarta"
                others = [
                    row["slice"]["value"] for row in shown
                    if row["slice"] and row["slice"]["dimension"] == "city"
                    and row["slice"]["value"] != city
                ]
                check(f"{name} sees no other city on any desk", not others, ",".join(others))
            check(f"{name} {'sees' if expected else 'does not see'} the Bandung story",
                  response.status_code == 200 and sees == expected,
                  f"{len(shown)} stories, {elapsed} ms")
            if bandung is not None:
                opened = await client.get(
                    f"/api/v1/intelligence/stories/{bandung['id']}", headers=sessions[name]
                )
                check(f"{name} opening the story by id returns {200 if expected else 404}",
                      opened.status_code == (200 if expected else 404))
        refused = await client.get("/api/v1/intelligence/newspaper", headers=sessions["news_off"])
        check("news_off is refused without the entitlement", refused.status_code == 403)
        me = await client.get("/api/v1/auth/me", headers=sessions["news_off"])
        check("news_off is told News is off", me.json().get("news_enabled") is False)
    report["passed"] = all(row["passed"] for row in report["checks"])
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-minutes", type=int, default=20)
    raise SystemExit(asyncio.run(run(parser.parse_args().wait_minutes)))
