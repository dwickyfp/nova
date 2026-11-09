"""Two businesses, one word: set up the business-ontology isolation lab.

    UV_NO_SYNC=1 uv run python -m tests.benchmark.ontology_isolation.seed

Through the production API as ``nova_admin`` (credentials read from
``docker/.env`` by ``superset/bench/nova_session.py``, never printed), this
publishes an HR Semantic View over ``NOVA_FK_SP_HR_1`` and creates one agent
per business: Sales (retail_sales), HR (hr_people), Operasional
(delivery_routes) and Persediaan (outlet_stock).

Through the ontology services (as the lab seed of the retail field lab does)
it adds:

* domain ``HR``, stewarded by ``hr_steward`` and visible only to ``hr_analyst``
  (and ACCOUNTADMIN): ``HR.Profit`` means the profit-sharing pool paid to
  employees, ``SUM(salary * commission_pct)``;
* ``Penjualan.Profit``: gross profit, net revenue after returns minus the cost
  of goods kept (``retail_sales.gross_margin``).

Each domain's change is approved by that domain's steward (a synthetic
principal: a submitter can never approve its own proposal), then one release
is published. Ids land in ``~/.cache/nova-ontology-isolation/state.json``.
Rerunning reuses what exists.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "superset" / "bench"))

from nova_session import principal, sign_in  # noqa: E402

from tests.benchmark.retail_field.views import dataset, field, metric  # noqa: E402

STATE = Path.home() / ".cache" / "nova-ontology-isolation" / "state.json"
ROLE = "ACCOUNTADMIN"
HR_DATABASE = "NOVA_FK_SP_HR_1"


def hr_view() -> dict[str, Any]:
    def source(item: dict[str, Any]) -> dict[str, Any]:
        return {**item, "source": f"{HR_DATABASE}.{item['name']}"}

    employees = source(
        dataset(
            "employees",
            ["EMPLOYEE_ID"],
            [
                field("EMPLOYEE_ID", "Integer", description="Kode karyawan."),
                field("FIRST_NAME", description="Nama depan."),
                field("LAST_NAME", description="Nama belakang karyawan.", synonyms=["karyawan"]),
                field("JOB_ID", description="Kode jabatan."),
                field("DEPARTMENT_ID", "Integer", description="Kode departemen."),
                field("SALARY", "Integer", dimension=False, description="Gaji bulanan."),
                field(
                    "COMMISSION_PCT", "Decimal", dimension=False,
                    description="Porsi bagi hasil (profit sharing) dari gaji.",
                ),
            ],
            "Karyawan dengan gaji, jabatan, departemen dan porsi bagi hasil.",
        )
    )
    departments = source(
        dataset(
            "departments",
            ["DEPARTMENT_ID"],
            [
                field("DEPARTMENT_ID", "Integer", description="Kode departemen."),
                field(
                    "DEPARTMENT_NAME", description="Nama departemen.",
                    synonyms=["departemen", "divisi"],
                ),
            ],
            "Departemen perusahaan.",
        )
    )
    jobs = source(
        dataset(
            "jobs",
            ["JOB_ID"],
            [
                field("JOB_ID", description="Kode jabatan."),
                field("JOB_TITLE", description="Nama jabatan.", synonyms=["jabatan"]),
            ],
            "Jabatan dan rentang gajinya.",
        )
    )
    metrics = [
        metric(
            "headcount", "employees", "COUNT(employees.EMPLOYEE_ID)", time=None,
            description="Jumlah karyawan.", unit="orang", datatype="Integer",
            synonyms=["jumlah karyawan"],
        ),
        metric(
            "payroll", "employees", "SUM(employees.SALARY)", time=None,
            description="Total gaji bulanan.", unit="USD", currency="USD",
            synonyms=["total gaji"],
        ),
        metric(
            "profit_sharing", "employees",
            "SUM(employees.SALARY * COALESCE(employees.COMMISSION_PCT, 0))", time=None,
            description="Kumpulan bagi hasil (profit sharing) yang dibayarkan ke karyawan.",
            unit="USD", currency="USD",
        ),
    ]
    return {
        "version": "0.1.1",
        "name": "hr_people",
        "description": "SDM: karyawan, departemen, jabatan, gaji dan bagi hasil. Mata uang USD.",
        "ai_context": None,
        "entities": {},
        "hierarchies": {},
        "verified_queries": [],
        "named_filters": [],
        "conformed_dimensions": [],
        "question_routing_instructions": (
            "Profit di SDM berarti profit sharing: bagi hasil yang dibayarkan ke karyawan "
            "(profit_sharing). Departemen dengan DEPARTMENT_NAME."
        ),
        "query_generation_instructions": "Salary is monthly USD.",
        "datasets": [employees, departments, jobs],
        "relationships": [
            {
                "name": "employees_departments",
                "from": "employees", "to": "departments",
                "from_columns": ["DEPARTMENT_ID"], "to_columns": ["DEPARTMENT_ID"],
                "cardinality": "many_to_one",
            },
            {
                "name": "employees_jobs",
                "from": "employees", "to": "jobs",
                "from_columns": ["JOB_ID"], "to_columns": ["JOB_ID"],
                "cardinality": "many_to_one",
            },
        ],
        "metrics": metrics,
    }


AGENTS = [
    ("sales", "Sales Analyst", ["retail_sales"],
     "Analis penjualan retail: omzet, margin, laba, diskon, retur per produk, outlet, kota."),
    ("hr", "HR Analyst", ["hr_people"],
     "Analis SDM: jumlah karyawan, gaji dan bagi hasil per departemen dan jabatan."),
    ("ops", "Ops Analyst", ["delivery_routes"],
     "Analis pengantaran: rute, stop, barang terkirim dan diterima outlet."),
    ("stock", "Stock Analyst", ["outlet_stock"],
     "Analis stok outlet: sell-out, stok akhir, stockout, barang masuk."),
]


def person(name: str, role: str) -> dict[str, Any]:
    return {
        "username": name, "roles": [role], "assigned_roles": [role], "active_role": role,
        "security_context_version": 1, "session_id": "ontology-isolation-seed",
    }


def check(response: httpx.Response, label: str) -> dict:
    if response.status_code >= 300:
        raise SystemExit(f"{label}: {response.status_code} {response.text[:600]}")
    return response.json() if response.content else {}


async def publish_hr_view(nova: httpx.AsyncClient, state: dict) -> None:
    views = {v["name"]: v["id"] for v in check(await nova.get("/semantic-views"), "views")}
    state["views"] = {name: views[name] for name in
                      ("retail_sales", "outlet_stock", "delivery_routes") if name in views}
    definition = json.dumps(hr_view())
    if "hr_people" in views:
        state["views"]["hr_people"] = views["hr_people"]
        return
    view = check(
        await nova.post(
            "/semantic-views",
            json={"name": "hr_people", "database": HR_DATABASE, "definition": definition},
        ),
        "create hr_people",
    )
    version = max(item["version"] for item in view["versions"])
    valid = check(
        await nova.post(f"/semantic-views/{view['id']}/versions/{version}/validate"), "validate"
    )
    if not valid.get("valid"):
        raise SystemExit(f"hr_people is invalid: {json.dumps(valid)[:800]}")
    check(
        await nova.post(
            f"/semantic-views/{view['id']}/versions/{version}/publish",
            json={"acknowledge_regressions": True},
        ),
        "publish hr_people",
    )
    state["views"]["hr_people"] = view["id"]
    print("view hr_people", view["id"])


async def seed_ontology(state: dict) -> None:
    from app.core.database import db
    from app.core.redis import session_store
    from app.modules.ontology.contracts import ConceptBody, ConceptChange, RepresentationChange
    from app.modules.ontology.releases import ReleasePrepare, release_service
    from app.modules.ontology.service import DomainCreate, ontology_service
    from app.modules.ontology.stewardship import (
        ProposalCreate,
        ReviewRequest,
        stewardship_service,
    )

    if state.get("release_id"):
        return
    await db.init_system_pool()
    await session_store.init()
    admin = person("nova_admin", ROLE)
    try:
        domains = {d["name"]: d["id"] for d in await ontology_service.list_domains(admin)}
        if "HR" not in domains:
            created = await ontology_service.create_domain(
                DomainCreate(
                    name="HR", steward_roles=["hr_steward"], visibility_roles=["hr_analyst"],
                    description="Konsep bisnis SDM.",
                ),
                admin,
            )
            domains["HR"] = created["id"]
        plans = [
            (
                "Penjualan", "retail_steward", "Penjualan.Profit",
                ConceptBody(
                    domain_id=domains["Penjualan"], kind="METRIC", slug="Profit", label="Profit",
                    aliases=["laba"],
                    definition=(
                        "Laba kotor penjualan: omzet bersih setelah retur dikurangi harga pokok "
                        "unit yang tidak diretur."
                    ),
                    unit="IDR",
                ),
                state["views"]["retail_sales"], "gross_margin",
            ),
            (
                "HR", "hr_steward", "HR.Profit",
                ConceptBody(
                    domain_id=domains["HR"], kind="METRIC", slug="Profit", label="Profit",
                    aliases=["profit sharing", "bagi hasil"],
                    definition=(
                        "Profit sharing: kumpulan bagi hasil yang dibayarkan ke karyawan, "
                        "gaji dikali porsi bagi hasil."
                    ),
                    unit="USD",
                ),
                state["views"]["hr_people"], "profit_sharing",
            ),
        ]
        for domain, steward_role, label, body, view_id, metric_name in plans:
            proposal = await stewardship_service.create(
                ProposalCreate(
                    title=f"Tambah {label}",
                    rationale="Lab isolasi ontologi lintas bisnis",
                    changes=[
                        ConceptChange(op="create", body=body),
                        RepresentationChange(
                            op="create", concept_id="@0", view_id=view_id, metric=metric_name
                        ),
                    ],
                    operation_id="iso-" + uuid.uuid4().hex,
                ),
                person("ontology_lab_author", ROLE),
            )
            applied = await stewardship_service.review(
                proposal.id,
                ReviewRequest(
                    decision="APPROVE", rationale="Lab isolasi ontologi",
                    operation_id="iso-" + uuid.uuid4().hex,
                ),
                person(f"{domain.lower()}_lab_steward", steward_role),
            )
            print("proposal", label, applied.status)
        release = await release_service.prepare(
            ReleasePrepare(note="Ontology isolation lab"), admin
        )
        if release.status != "PREPARED":
            raise SystemExit(f"release {release.status}: {release.issues}")
        await release_service.publish(release.id, admin)
        state["release_id"] = release.id
        print("release", release.id, release.warnings or "")
    finally:
        await session_store.close()
        await db.close_system_pool()


async def create_agents(nova: httpx.AsyncClient, state: dict) -> None:
    agents = state.setdefault("agents", {})
    for key, name, views, description in AGENTS:
        if key not in agents:
            agent = check(
                await nova.post(
                    "/agents",
                    json={
                        "name": name,
                        "description": description,
                        "default_tools": ["semantic_query", "ontology_resolve", "ontology_explain"],
                        "semantic_view_ids": [state["views"][view] for view in views],
                        "policy": "auto_read_only",
                        "budget_seconds": 300,
                    },
                ),
                f"agent {name}",
            )
            agents[key] = agent["agent_id"]
            check(
                await nova.post(f"/agents/{agents[key]}/access", json={"role_name": ROLE}),
                f"access {name}",
            )
        verified = check(
            await nova.post(f"/agents/{agents[key]}/access/verify", json={"role_name": ROLE}),
            f"verify {name}",
        )
        print("agent", name, agents[key], verified.get("all_granted"))


async def main() -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    try:
        session = await sign_in(principal("admin"))
        async with session.nova() as nova:
            await publish_hr_view(nova, state)
        await seed_ontology(state)
        session = await sign_in(principal("admin"))
        async with session.nova() as nova:
            await create_agents(nova, state)
    finally:
        STATE.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
