"""Ask each business's agent the same words and record which meaning it used.

    STARROCKS_FE_MYSQL_PORT=29030 UV_NO_SYNC=1 \\
        uv run python -m tests.benchmark.ontology_isolation.ask --out <file.json>

Needs ``seed``. Every question goes through the production agent API in a new
thread, as a user would ask it in Studio; the stored turn is then read back
for the concepts its tools used (``semantic_query``'s ontology grounding and
``ontology_resolve``'s definitions) and its answer. Gold numbers are computed
straight from the engine. The run also checks the role boundary through
``/ontology/resolve``: without an agent, a role that sees both businesses is
asked which "profit"; a role that cannot see HR never sees HR.Profit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "superset" / "bench"))

from nova_session import principal, sign_in  # noqa: E402

from tests.benchmark.ontology_isolation.seed import STATE  # noqa: E402

#: (agent, question, the concept it must use, words from the other meaning that must not appear)
CASES: list[dict[str, Any]] = [
    {"id": "sales_profit", "agent": "sales",
     "question": "Apa arti profit di bisnis ini dan berapa profit Oktober 2026?",
     "concept": "Penjualan.Profit", "foreign": ["profit sharing", "bagi hasil", "HR.Profit"]},
    {"id": "hr_profit", "agent": "hr",
     "question": "Apa arti profit di bisnis ini dan berapa total profit per departemen?",
     "concept": "HR.Profit", "foreign": ["laba kotor", "harga pokok", "Penjualan.Profit"]},
    {"id": "sales_foreign_term", "agent": "sales",
     "question": "Apa itu bagi hasil karyawan?",
     "concept": None, "foreign": ["HR.Profit", "salary", "gaji dikali"]},
    {"id": "hr_foreign_term", "agent": "hr",
     "question": "Apa arti omzet bersih?",
     "concept": None, "foreign": ["Penjualan.NetRevenue", "setelah diskon dan retur"]},
    {"id": "ops_received", "agent": "ops",
     "question": "Berapa received units Oktober 2026?",
     "concept": "Operasional.ReceivedUnits", "foreign": ["Persediaan.ReceivedUnits"]},
    {"id": "stock_received", "agent": "stock",
     "question": "Berapa received units Oktober 2026?",
     "concept": "Persediaan.ReceivedUnits", "foreign": ["Operasional.ReceivedUnits"]},
    {"id": "serbadata_received", "agent": "serbadata",
     "question": "Berapa received units Oktober 2026?",
     "concept": "ASK", "foreign": []},
]

GOLD_SQL = {
    "sales_profit": (
        "SELECT SUM(net_amount - net_amount * returned_qty / NULLIF(quantity, 0))"
        " - SUM(cost_amount - cost_amount * returned_qty / NULLIF(quantity, 0))"
        " FROM NOVA_RETAIL_FIELD.sales_orders"
        " WHERE order_date >= '2026-10-01' AND order_date < '2026-11-01'"
    ),
    "hr_profit": (
        "SELECT d.DEPARTMENT_NAME, SUM(e.SALARY * COALESCE(e.COMMISSION_PCT, 0)) AS p"
        " FROM NOVA_FK_SP_HR_1.employees e JOIN NOVA_FK_SP_HR_1.departments d"
        " ON d.DEPARTMENT_ID = e.DEPARTMENT_ID GROUP BY d.DEPARTMENT_NAME HAVING p > 0"
    ),
    "ops_received": (
        "SELECT SUM(received_qty) FROM NOVA_RETAIL_FIELD.route_stop_items"
        " WHERE plan_date >= '2026-10-01' AND plan_date < '2026-11-01'"
    ),
    "stock_received": (
        "SELECT SUM(received_qty) FROM NOVA_RETAIL_FIELD.outlet_stock_daily"
        " WHERE stock_date >= '2026-10-01' AND stock_date < '2026-11-01'"
    ),
}


async def gold() -> dict[str, Any]:
    import asyncmy

    from app.core.config import settings

    conn = await asyncmy.connect(
        host=settings.STARROCKS_HOST, port=int(settings.STARROCKS_FE_MYSQL_PORT),
        user=settings.STARROCKS_ROOT_USER, password=settings.STARROCKS_ROOT_PASSWORD,
    )
    out: dict[str, Any] = {}
    try:
        async with conn.cursor() as cur:
            for key, sql in GOLD_SQL.items():
                await cur.execute(sql)
                rows = await cur.fetchall()
                out[key] = [list(map(lambda v: float(v) if isinstance(v, int | float) or
                                     hasattr(v, "as_tuple") else v, row)) for row in rows]
    finally:
        conn.close()
    return out


def _used(steps: list[dict]) -> dict[str, list[str]]:
    """Concepts each ontology-aware tool in the turn named."""
    used: dict[str, list[str]] = {}
    for step in steps:
        if step.get("kind") != "tool":
            continue
        name = str(step.get("name"))
        blob = json.dumps(step.get("semantic_evidence") or step.get("trace_detail") or {})
        found = sorted({
            word.strip('"') for word in blob.replace(",", " ").split()
            if word.strip('"').count(".") == 1
            and word.strip('"').split(".")[0] in {"Penjualan", "HR", "Operasional", "Persediaan"}
        })
        if found:
            used.setdefault(name, []).extend(found)
    return used


async def ask(session, agents: dict[str, str], case: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(
        base_url=session.api, timeout=600, headers={"Authorization": f"Bearer {session.token}"}
    ) as nova:
        agent_id = agents[case["agent"]]
        thread = (await nova.post(f"/agents/{agent_id}/threads", json={"title": case["id"]})).json()
        started = time.perf_counter()
        async with nova.stream(
            "POST", f"/agents/{agent_id}/threads/{thread['thread_id']}/messages",
            json={"content": case["question"]},
        ) as response:
            response.raise_for_status()
            async for _ in response.aiter_lines():
                pass
        seconds = round(time.perf_counter() - started, 1)
        stored = (await nova.get(f"/agents/{agent_id}/threads/{thread['thread_id']}")).json()
    answer = next((m for m in reversed(stored["messages"]) if m["role"] == "assistant"), {})
    steps = answer.get("steps") or []
    text = answer.get("content") or ""
    return {
        **case,
        "seconds": seconds,
        "answer": text,
        "tools": [s.get("name") for s in steps if s.get("kind") == "tool"],
        "concepts_used": _used(steps),
        "finish": next(
            (s.get("finish_reason") for s in steps if s.get("kind") == "turn_outcome"), None
        ),
        "foreign_found": [word for word in case["foreign"] if word.lower() in
                          (text + json.dumps(_used(steps))).lower()],
    }


async def role_boundary() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for who in ("admin", "steward"):
        session = await sign_in(principal(who))
        async with session.nova() as nova:
            body = (await nova.get("/ontology/resolve", params={"q": "profit per bulan"})).json()
        out[who] = {
            "status": body.get("status"),
            "concepts": [c.get("qualified_name") for c in body.get("concepts") or []],
            "candidates": [
                c.get("qualified_name")
                for item in body.get("ambiguities") or [] for c in item.get("candidates") or []
            ],
        }
    return out


async def main(out: Path, only: set[str] | None) -> None:
    state = json.loads(STATE.read_text())
    agents = dict(state["agents"])
    session = await sign_in(principal("admin"))
    async with session.nova() as nova:
        listed = (await nova.get("/agents")).json()
    for item in listed if isinstance(listed, list) else listed.get("agents", []):
        if item.get("name") == "SerbaData":
            agents["serbadata"] = item["agent_id"]
    results = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "release": state.get("release_id"),
               "gold": await gold(), "roles": await role_boundary(), "results": []}
    print(json.dumps({"roles": results["roles"]}), flush=True)
    for case in CASES:
        if only and case["id"] not in only:
            continue
        try:
            result = await ask(session, agents, case)
        except Exception as exc:  # noqa: BLE001 - recorded, the run goes on
            result = {**case, "error": f"{type(exc).__name__}: {exc}"[:300]}
        results["results"].append(result)
        print(json.dumps({k: result.get(k) for k in
                          ("id", "finish", "tools", "concepts_used", "foreign_found", "error")}),
              flush=True)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2, default=str, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    asyncio.run(main(Path(args.out), set(filter(None, args.only.split(","))) or None))
