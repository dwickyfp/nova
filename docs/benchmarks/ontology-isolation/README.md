# One ontology, several businesses: isolation per agent

**Question:** can the Supernova Business Ontology hold two or more businesses
whose words overlap ("profit" means one thing in Sales and another in HR)? And
does each agent keep its own business's meaning, without mixing?

**Answer:**

- The ontology always could hold them. Concept names are unique per domain,
  so `Penjualan.Profit` and `HR.Profit` can both exist.
- Before this change it did **not** keep them apart per agent. A term was
  resolved over every domain the active role can see, and the agent's
  Semantic Views were applied only afterwards. A Sales agent used by a role
  that also sees HR was asked "Penjualan.Profit or HR.Profit?", and
  `ontology_resolve` returned both definitions.
- An agent's business is now its Semantic Views. Grounding,
  `ontology_resolve` and the Dataviz assistant read a term only in the
  domains bound to those views ([arch-18](../../arch-18-business-ontology.md#studio-grounding)).

## How agent, role and data relate

| Layer | Decides | Where |
| --- | --- | --- |
| Role | Which domains (and so which definitions) a person may see at all; which data rows they may read (Ranger) | domain `visibility_roles`, `steward_roles`; Ranger policies |
| Agent | Which business a turn speaks for: the domains of the concepts bound to its Semantic Views | `semantic_view_ids` → bindings → domains |
| Data | Which metric computes a concept, and whether this user may compute it | representation (concept → view metric), view visibility, freshness |

The agent scope only narrows what the role can see. It never grants a domain
or data the role lacks.

## Lab

`backend/tests/benchmark/ontology_isolation/` runs on the live local stack:
the real model, StarRocks with Ranger, and the host backend.

- **`seed`** publishes the `hr_people` Semantic View (`NOVA_FK_SP_HR_1`) and
  creates domain `HR`: stewarded by `hr_steward`, visible to `hr_analyst`
  only. It adds two concepts, each approved by its own domain's steward:
  - `HR.Profit`: the profit-sharing pool, `SUM(salary × commission_pct)`;
  - `Penjualan.Profit`: gross profit, `retail_sales.gross_margin`.

  It then publishes release `ont_rel_0009`. The release's own meaning check
  warns: "'profit' names HR.Profit and Penjualan.Profit", and the same for
  `Operasional.ReceivedUnits` and `Persediaan.ReceivedUnits`, which already
  existed. It creates four agents, each with only its own view: Sales
  (`retail_sales`), HR (`hr_people`), Ops (`delivery_routes`) and Stock
  (`outlet_stock`). The existing SerbaData agent spans retail_sales,
  outlet_stock and delivery_routes.
- **`ask`** sends each question in a new thread through the production agent
  API, as Studio does. It reads back the concepts each turn's tools used, and
  any words from the other meaning, and computes gold numbers from the engine.

Gold numbers:

- Sales profit, Oct 2026: Rp620.439.277,50.
- HR profit sharing: Sales department 72.640 (one employee without a
  department, 1.050).
- Received units, Oct 2026: 307.727. The lab's delivery and stock data
  agree, so for those two only the concept used tells them apart.

## Results

Raw runs are in [raw/](raw/). Run 1 was before the "ask which one" note; runs
2 and 3 are on the final build.

| Check | Expected | Run 1 | Run 2 | Run 3 |
| --- | --- | --- | --- | --- |
| Role that sees both businesses, no agent: "profit per bulan" | asks: Penjualan.Profit or HR.Profit | ✓ | ✓ | ✓ |
| Role that cannot see HR (retail_steward): "profit per bulan" | Penjualan.Profit only; HR never shown | ✓ | ✓ | ✓ |
| Sales agent: "apa arti profit … berapa profit Oktober 2026" | Penjualan.Profit, gross profit, 620.439.277,50 | ✓ | ✓ | ✓ |
| HR agent: "apa arti profit … per departemen" | HR.Profit, profit sharing, Sales 72.640 | ✓ | ✓ | ✓ |
| Sales agent: "apa itu bagi hasil karyawan" (HR's word) | not a Sales concept; no HR definition | ✓ | ✓ | ✓ |
| HR agent: "apa arti omzet bersih" (Sales' word) | not an HR concept; no Sales definition | ✓ | ✓ | ✓ |
| Ops agent: "received units Oktober 2026" | Operasional.ReceivedUnits | ✓ | ✓ | ✓ |
| Stock agent: same words | Persediaan.ReceivedUnits | ✓ | ✓ | ✓ |
| SerbaData (spans both): same words | asks which received units | ✗ said "Saya akan cek…" | ✓ | ✓ |

On the final build: **14 of 14 agent questions and 4 of 4 role checks
correct**, with no foreign definition or concept in any answer or tool step.

Unit tests (`test_ontology_grounding.py`, `test_ontology_studio.py`,
`test_studio_dataviz.py`) cover:

- the leak without a scope;
- each agent's own meaning;
- another business's word staying out unless its domain is named;
- grounding;
- the resolve tool;
- the prompt note for an agent that spans two meanings;
- a Dataviz turn with a dashboard open not mixing in other views.

## Limitations

- **Duplicate definitions.** `Operasional.ReceivedUnits` and
  `Persediaan.ReceivedUnits` have the same definition text ("unit yang
  benar-benar diterima outlet"). An agent that spans both asks which one, but
  the reader cannot tell them apart from the definitions. Their stewards
  should make the two meanings distinct.
- **Sample size.** Three runs of 7 questions each, over four businesses, in
  Indonesian.
- **Explicit lookups.** `ontology_explain` by qualified name (`HR.Profit`)
  still answers inside any agent, because naming the domain is an explicit
  request. It never grants data.
- **Viewer roles.** These agents were exercised as ACCOUNTADMIN, which sees
  every domain: the hardest case for mixing. A role that cannot see HR was
  checked through `/ontology/resolve`, not through an agent turn.
