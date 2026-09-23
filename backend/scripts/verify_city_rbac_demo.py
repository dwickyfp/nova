"""Exercise one shared Agent Studio agent as two city-scoped users."""

from __future__ import annotations

import json
import os
import time
from decimal import Decimal
from pathlib import Path

import httpx
from verify_ranger_e2e import MySQLClient, MySQLError

DATABASE = "rbac_city_demo"
TABLE = f"{DATABASE}.city_sales"
ROLE = "rbac_city_reader"
AGENT_NAME = "City RBAC Agent"
USERS = {"rbac_jakarta": "Jakarta", "rbac_bandung": "Bandung"}
BASE_URL = os.environ.get("NOVA_API_URL", "http://127.0.0.1:8000")
PASSWORD_FILE = Path(os.environ.get("RBAC_DEMO_CREDENTIAL_FILE", "/tmp/nova-rbac-city-demo.env"))


def _passwords() -> dict[str, str]:
    values = dict(
        line.split("=", 1) for line in PASSWORD_FILE.read_text().splitlines() if "=" in line
    )
    if any(not values.get(name) for name in USERS):
        raise RuntimeError("Both demo credentials are required")
    return values


def _query(client: httpx.Client, token: str, sql: str, *, forged_role: bool = False) -> dict:
    response = client.post(
        "/api/v1/query/execute",
        headers={"Authorization": f"Bearer {token}"},
        json={"sql": sql, "database": DATABASE, "role": "ACCOUNTADMIN" if forged_role else None},
    )
    response.raise_for_status()
    results = response.json()
    if len(results) != 1 or not results[0]["success"]:
        raise AssertionError(f"Workspace query failed: {results}")
    return results[0]


def _studio_turn(
    client: httpx.Client, token: str, agent_id: str, question: str,
) -> tuple[list[tuple[str, dict]], float]:
    headers = {"Authorization": f"Bearer {token}"}
    thread = client.post(
        f"/api/v1/agents/{agent_id}/threads", headers=headers, json={"title": "RBAC city test"},
    )
    thread.raise_for_status()
    thread_id = thread.json()["thread_id"]
    start = time.perf_counter()
    events: list[tuple[str, dict]] = []
    with client.stream(
        "POST", f"/api/v1/agents/{agent_id}/threads/{thread_id}/messages",
        headers=headers, json={"content": question, "role": "ACCOUNTADMIN"},
    ) as response:
        response.raise_for_status()
        event = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                events.append((event, json.loads(line[6:])))
    elapsed = time.perf_counter() - start
    done = [payload for event, payload in events if event == "done"]
    errors = [payload for event, payload in events if event == "error"]
    if errors or not done or done[-1].get("finish_reason") != "stop":
        raise AssertionError(f"Studio turn failed: done={done}, errors={errors}")
    calls = [payload for event, payload in events if event == "tool_call"]
    if not calls:
        raise AssertionError("Studio agent answered without querying governed data")
    return events, elapsed


def _table_rows(events: list[tuple[str, dict]]) -> list[list]:
    return [row for event, payload in events if event == "table" for row in payload["rows"]]


def _answer(events: list[tuple[str, dict]]) -> str:
    return "".join(payload.get("text", "") for event, payload in events if event == "text_delta")


def verify() -> None:
    passwords = _passwords()
    timings: dict[str, float] = {}
    with httpx.Client(base_url=BASE_URL, timeout=180) as client:
        for username, city in USERS.items():
            forbidden = "Bandung" if city == "Jakarta" else "Jakarta"
            login = client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": passwords[username]},
            )
            login.raise_for_status()
            session = login.json()
            if session["active_role"] != ROLE or session["assigned_roles"] != [ROLE]:
                raise AssertionError(f"Unexpected login role for {username}")
            token = session["access_token"]

            started = time.perf_counter()
            result = _query(
                client, token, f"SELECT id, city, amount FROM {TABLE}", forged_role=True,
            )
            if len(result["rows"]) != 2 or {row[1] for row in result["rows"]} != {city}:
                raise AssertionError(f"Workspace scope failed for {username}: {result['rows']}")
            denied = _query(
                client, token,
                f"SELECT id, city, amount FROM {TABLE} WHERE city = '{forbidden}'",
            )
            if denied["rows"]:
                raise AssertionError(f"Workspace leaked {forbidden} to {username}")
            timings[f"workspace_{username}"] = time.perf_counter() - started

            try:
                with MySQLClient(
                    username, passwords[username], database=DATABASE, role="ACCOUNTADMIN",
                ):
                    pass
            except MySQLError:
                pass
            else:
                raise AssertionError(f"Proxy accepted a forged ACCOUNTADMIN role for {username}")

            with MySQLClient(
                username, passwords[username], database=DATABASE, role=ROLE,
            ) as proxy:
                if proxy.query("SELECT CURRENT_ROLE()") != [[ROLE]]:
                    raise AssertionError(f"Wrong proxy role for {username}")
                try:
                    proxy.query("USE ROLE ACCOUNTADMIN")
                except MySQLError:
                    pass
                else:
                    raise AssertionError(f"Proxy let {username} assume ACCOUNTADMIN")
                if proxy.query("SELECT CURRENT_ROLE()") != [[ROLE]]:
                    raise AssertionError(
                        f"Proxy role changed after denied escalation for {username}"
                    )
                rows = proxy.query(f"SELECT id, city, amount FROM {TABLE}")
                if len(rows) != 2 or {row[1] for row in rows} != {city}:
                    raise AssertionError(f"Proxy scope failed for {username}: {rows}")
                if proxy.query(
                    f"SELECT id, city, amount FROM {TABLE} WHERE city = '{forbidden}'"
                ):
                    raise AssertionError(f"Proxy leaked {forbidden} to {username}")

            listing = client.get(
                "/api/v1/agents", headers={"Authorization": f"Bearer {token}"},
            )
            listing.raise_for_status()
            agents = [row for row in listing.json()["agents"] if row["name"] == AGENT_NAME]
            if len(agents) != 1 or agents[0]["visibility"] != "shared":
                raise AssertionError(f"Shared agent unavailable for {username}")
            agent_id = agents[0]["agent_id"]
            positive, elapsed = _studio_turn(
                client, token, agent_id,
                "Berapa total_amount per city?",
            )
            timings[f"studio_{username}"] = elapsed
            visible_rows = _table_rows(positive)
            expected_total = Decimal("400") if city == "Jakarta" else Decimal("600")
            if (
                not visible_rows
                or any(city not in row for row in visible_rows)
                or sum(Decimal(str(row[-1])) for row in visible_rows) != expected_total
            ):
                raise AssertionError(f"Studio result scope failed for {username}: {visible_rows}")
            if any(forbidden in row for row in visible_rows):
                raise AssertionError(f"Studio leaked {forbidden} to {username}")
            forbidden_total = "600.00" if city == "Jakarta" else "400.00"
            if forbidden_total in _answer(positive):
                raise AssertionError(f"Studio answer claimed unauthorized total for {username}")

            if city == "Jakarta":
                negative, elapsed = _studio_turn(
                    client, token, agent_id,
                    "Berapa total_amount per city hanya untuk Bandung?",
                )
                timings["studio_jakarta_requests_bandung"] = elapsed
                if _table_rows(negative):
                    raise AssertionError("Jakarta user received Bandung table rows in Studio")
                if not any(
                    event == "tool_status" and payload.get("status") == "done"
                    for event, payload in negative
                ):
                    raise AssertionError("Bandung request did not complete a governed query")
                if _answer(negative) != "The authorized query returned no rows for this request.":
                    raise AssertionError(
                        "Studio did not state that the authorized result was empty"
                    )
            print(f"PASS {username}: Workspace, MySQL proxy, Studio = {city} only")
    print("PASS Jakarta asks Bandung in Studio: zero rows")
    print("timings_seconds=" + json.dumps({key: round(value, 3) for key, value in timings.items()}))


if __name__ == "__main__":
    verify()
