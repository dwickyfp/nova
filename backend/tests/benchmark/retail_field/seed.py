"""Publish the retail lab's Semantic Views, business ontology and specialist agents.

    NOVA_RETAIL_LAB_PASSWORD=... STARROCKS_FE_MYSQL_PORT=29030 \\
        uv run python -m tests.benchmark.retail_field.seed [--api http://localhost:8000/api/v1]

Everything is created through the production API as ``nova_admin`` except the
ontology steward approval, which a synthetic steward gives as a test fixture
(the submitter can never approve its own proposal). Ids land in
``~/.cache/nova-retail-lab/state.json``. The password is read only from the
environment and never written.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx

from tests.benchmark.retail_field.generate import DATABASE
from tests.benchmark.retail_field.views import VIEWS

STATE = Path.home() / ".cache" / "nova-retail-lab" / "state.json"
ROLE = "ACCOUNTADMIN"

AGENTS = [
    {
        "key": "serbadata",
        "views": ["retail_sales", "outlet_stock", "delivery_routes"],
        "name": "SerbaData",
        "description": (
            "Semua data operasional retail dalam satu agent: penjualan (omzet, unit, diskon, "
            "retur, margin per produk, outlet, kota, kecamatan dan salesman), stok outlet "
            "(sell-out, stockout, penjualan hilang), dan pengantaran (rute harian, urutan "
            "stop, jarak rencana vs aktual per driver, check-in GPS, barang terkirim vs "
            "diterima). Bisa memprediksi penjualan dan stok, mencari kejanggalan dan fraud, "
            "serta menyusun rute terbaik: ambil outlet (nama, koordinat, volume pembelian) "
            "lalu kelompokkan per area dan optimalkan urutan kunjungan dengan "
            "route_optimization, ditampilkan sebagai peta rute."
        ),
        "default_tools": [
            "semantic_query",
            "data_to_chart",
            "diagnose_change",
            "ml_execute",
            "dashboard_build",
        ],
        "sample_questions": [
            "Toko mana yang paling ramai bulan ini?",
            "Prediksi kebutuhan stok Teh Melati di Surabaya 14 hari ke depan",
            "Bagaimana rute pengantaran terbaik di Medan?",
            "Apakah ada indikasi fraud di salesman atau driver?",
        ],
    },
]
#: Agents an earlier version of this lab created; the merged agent replaces them.
RETIRED_AGENTS = ("sales", "stock", "routes", "risk")

#: Domain -> concepts (slug, label, aliases, definition, unit, view, metric).
CONCEPTS = {
    "Penjualan": [
        (
            "NetRevenue",
            "Omzet",
            ["omzet", "penjualan bersih", "net revenue"],
            "Nilai penjualan ke outlet setelah diskon, dalam IDR, pada tanggal order.",
            "IDR",
            "retail_sales",
            "net_revenue",
        ),
        (
            "UnitsSold",
            "Unit terjual",
            ["unit terjual", "volume penjualan"],
            "Jumlah unit yang dipesan outlet (sell-in).",
            "unit",
            "retail_sales",
            "units_sold",
        ),
        (
            "ReturnRate",
            "Tingkat retur",
            ["tingkat retur", "rasio retur"],
            "Unit yang diretur dibagi unit yang dipesan.",
            None,
            "retail_sales",
            "return_rate",
        ),
        (
            "AverageDiscount",
            "Rata-rata diskon",
            ["rata-rata diskon"],
            "Rata-rata persen diskon per baris order.",
            "percent",
            "retail_sales",
            "avg_discount_pct",
        ),
        (
            "GrossMargin",
            "Margin kotor",
            ["margin", "laba kotor"],
            "Omzet dikurangi harga pokok penjualan.",
            "IDR",
            "retail_sales",
            "gross_margin",
        ),
    ],
    "Persediaan": [
        (
            "SellOut",
            "Sell-out",
            ["sell-out", "penjualan ke konsumen"],
            "Unit yang dibeli konsumen di outlet.",
            "unit",
            "outlet_stock",
            "sell_out_units",
        ),
        (
            "StockoutDays",
            "Hari stockout",
            ["stockout", "stok habis"],
            "Jumlah hari outlet-produk kehabisan stok.",
            "day",
            "outlet_stock",
            "stockout_days",
        ),
        (
            "LostSales",
            "Penjualan hilang",
            ["penjualan hilang", "lost sales"],
            "Permintaan konsumen yang tidak terlayani karena stok habis.",
            "unit",
            "outlet_stock",
            "lost_sales_units",
        ),
    ],
    "Operasional": [
        (
            "DeliveryGap",
            "Selisih pengiriman",
            ["selisih kirim", "barang tidak sampai"],
            "Unit yang dicatat terkirim driver tetapi tidak diterima outlet.",
            "unit",
            "delivery_routes",
            "delivery_gap_units",
        ),
        (
            "FarCheckins",
            "Check-in jauh",
            ["check-in jauh"],
            "Check-in driver lebih dari 500 meter dari lokasi outlet.",
            "count",
            "delivery_routes",
            "far_checkins",
        ),
        (
            "DistanceTravelled",
            "Jarak tempuh",
            ["jarak tempuh", "kilometer"],
            "Kilometer aktual yang ditempuh rute pengantaran.",
            "km",
            "delivery_routes",
            "actual_distance_km",
        ),
    ],
}


def login(client: httpx.Client, api: str) -> None:
    password = os.environ["NOVA_RETAIL_LAB_PASSWORD"]
    res = client.post(f"{api}/auth/login", json={"username": "nova_admin", "password": password})
    res.raise_for_status()
    client.headers["Authorization"] = "Bearer " + res.json()["access_token"]


def check(response: httpx.Response, label: str) -> dict:
    if response.status_code >= 300:
        raise SystemExit(f"{label}: {response.status_code} {response.text[:600]}")
    return response.json() if response.content else {}


def publish_views(client: httpx.Client, api: str, state: dict) -> None:
    views = state.setdefault("views", {})
    for name, definition in VIEWS.items():
        if name in views:
            # Publish the current definition as a new version of the existing view.
            added = check(
                client.post(
                    f"{api}/semantic-views/{views[name]}/versions",
                    json={"definition": json.dumps(definition)},
                ),
                f"version {name}",
            )
            view = {"id": views[name], "versions": added.get("versions") or [added]}
        else:
            view = check(
                client.post(
                    f"{api}/semantic-views",
                    json={
                        "name": name,
                        "database": DATABASE,
                        "definition": json.dumps(definition),
                    },
                ),
                f"create {name}",
            )
        version = max(item["version"] for item in view["versions"])
        validated = check(
            client.post(f"{api}/semantic-views/{view['id']}/versions/{version}/validate"),
            f"validate {name}",
        )
        if not validated.get("valid"):
            raise SystemExit(f"{name} is invalid: {json.dumps(validated)[:800]}")
        check(
            client.post(
                f"{api}/semantic-views/{view['id']}/versions/{version}/publish",
                json={"acknowledge_regressions": True},
            ),
            f"publish {name}",
        )
        views[name] = view["id"]
        print("view", name, view["id"])


async def seed_ontology(state: dict) -> None:
    from app.core.database import db
    from app.core.redis import session_store
    from app.modules.ontology.contracts import ConceptBody, ConceptChange, RepresentationChange
    from app.modules.ontology.releases import ReleasePrepare, release_service
    from app.modules.ontology.service import DomainCreate, ontology_service
    from app.modules.ontology.stewardship import ProposalCreate, ReviewRequest, stewardship_service

    if state.get("release_id"):
        return
    await db.init_system_pool()
    await session_store.init()

    def person(name, role):
        return {
            "username": name,
            "roles": [role],
            "assigned_roles": [role],
            "active_role": role,
            "security_context_version": 1,
            "session_id": "retail-lab-seed",
        }

    admin = person("nova_admin", ROLE)
    author = person("retail_analyst", "retail_analyst")
    steward = person("retail_steward", "retail_steward")
    try:
        changes = []
        for domain_name, concepts in CONCEPTS.items():
            domain = await ontology_service.create_domain(
                DomainCreate(
                    name=domain_name,
                    steward_roles=["retail_steward"],
                    description=f"Konsep bisnis {domain_name.lower()}.",
                ),
                admin,
            )
            for slug, label, aliases, definition, unit, view, metric in concepts:
                index = len(changes)
                changes.append(
                    ConceptChange(
                        op="create",
                        body=ConceptBody(
                            domain_id=domain["id"],
                            kind="METRIC",
                            slug=slug,
                            label=label,
                            definition=definition,
                            aliases=aliases,
                            unit=unit,
                        ),
                    )
                )
                changes.append(
                    RepresentationChange(
                        op="create",
                        concept_id=f"@{index}",
                        view_id=state["views"][view],
                        metric=metric,
                    )
                )
        proposal = await stewardship_service.create(
            ProposalCreate(
                title="Konsep bisnis retail",
                rationale="Lab retail field-sales",
                changes=changes,
                operation_id="retail-" + uuid.uuid4().hex,
            ),
            author,
        )
        applied = await stewardship_service.review(
            proposal.id,
            ReviewRequest(
                decision="APPROVE",
                rationale="Lab retail field-sales",
                operation_id="retail-" + uuid.uuid4().hex,
            ),
            steward,
        )
        release = await release_service.prepare(ReleasePrepare(note="Retail lab"), admin)
        await release_service.publish(release.id, admin)
        state["release_id"] = release.id
        print("ontology", applied.status, release.id, release.issues or "", release.warnings or "")
    finally:
        await session_store.close()
        await db.close_system_pool()


def create_agents(client: httpx.Client, api: str, state: dict) -> None:
    agents = state.setdefault("agents", {})
    for key in RETIRED_AGENTS:
        if key in agents:
            # Only agents this lab created and recorded in its own state.
            response = client.delete(f"{api}/agents/{agents[key]}")
            if response.status_code not in (204, 404):
                check(response, f"delete {key}")
            print("removed", key, agents.pop(key))
    for spec in AGENTS:
        if spec["key"] in agents:
            # Keep the lab's descriptions current: Smart routes work by them.
            check(
                client.put(
                    f"{api}/agents/{agents[spec['key']]}",
                    json={
                        "description": spec["description"],
                        "default_tools": spec["default_tools"],
                    },
                ),
                "update",
            )
            # A republished view changes the access fingerprint: verify again.
            verified = check(
                client.post(
                    f"{api}/agents/{agents[spec['key']]}/access/verify", json={"role_name": ROLE}
                ),
                "verify",
            )
            print("re-verified", spec["name"], verified.get("all_granted"))
            continue
        body = {
            key: spec[key] for key in ("name", "description", "default_tools", "sample_questions")
        }
        agent = check(
            client.post(
                f"{api}/agents",
                json={
                    **body,
                    "semantic_view_ids": [state["views"][name] for name in spec["views"]],
                    "policy": "auto_read_only",
                    "budget_seconds": 420,
                },
            ),
            f"agent {spec['name']}",
        )
        check(
            client.post(f"{api}/agents/{agent['agent_id']}/access", json={"role_name": ROLE}),
            "access",
        )
        verified = check(
            client.post(
                f"{api}/agents/{agent['agent_id']}/access/verify", json={"role_name": ROLE}
            ),
            "verify",
        )
        agents[spec["key"]] = agent["agent_id"]
        print("agent", spec["name"], agent["agent_id"], json.dumps(verified)[:160])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000/api/v1")
    args = parser.parse_args()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    try:
        with httpx.Client(timeout=300) as client:
            login(client, args.api)
            publish_views(client, args.api, state)
        asyncio.run(seed_ontology(state))
        with httpx.Client(timeout=300) as client:
            login(client, args.api)
            create_agents(client, args.api, state)
    finally:
        STATE.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
