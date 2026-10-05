# Nova — Enterprise Intelligence OS

## Vision

Nova is an Enterprise Intelligence Operating System that turns governed
enterprise data into understanding, decisions, actions, outcomes, and learning.
It unifies a governed analytical data foundation, business semantics,
organizational context, AI agents, decision intelligence, and operational
execution.

People use Nova to understand what is happening, investigate evidence, compare
options, review an action, and measure its outcome. The product lifecycle is:

```text
Data → Meaning → Context → Understand → Investigate → Decide → Act → Outcome → Learn
          ↑                                                                   │
          └──────────────── Reviewed improvements ────────────────────────────┘
```

StarRocks provides the analytical query and storage foundation and authenticates
Nova users. Nova owns SQL semantics, execution routing, and the intelligence
workflow; StarRocks owns physical planning and distributed execution. In the
Ranger-enabled profile, the patched StarRocks FE enforces Ranger access policies,
row filters, and masks using the caller's single active role.

## Product principles

1. **Governed by default.** Data access retains the caller's authorization
   context. Supported Actions require policy checks, reviewer approval, consent,
   verification, and audit records.
2. **Business meaning over raw data.** Semantic Views define metrics,
   relationships, and verified queries. Context Graph connects published
   definitions to governed knowledge and decision revisions.
3. **Intelligence is a lifecycle.** Investigations, Decisions, Actions, and
   Outcomes connect analysis to execution and learning, with evidence between
   steps and explicit deployment controls.
4. **Human-governed autonomy.** Agents use a bounded shared engine. Delegation
   cannot broaden access; consequential operations retain their consent gates.
5. **Evidence over confidence.** Conclusions retain evidence and provenance.
   Observed associations, scenario assumptions, and causal effects remain
   distinct; a model's confidence cannot replace verified evidence.
6. **Learn through review.** Outcomes can produce private inferred knowledge.
   Quality findings create proposals; shared semantic definitions and manifested
   agent releases require evaluation and publication.

## Product surfaces

| Surface | Role |
| --- | --- |
| Nova Console | Data and control foundation: SQL Workspace, Database Explorer, catalog, stages, ingestion/export, ML, tasks, monitoring, security, and Intelligence objects. |
| Nova Studio | Intelligence workspace: agents and Smart collaboration, Missions, investigations, evidence, scenarios, Decisions, governed Actions, Outcomes, artifacts, dashboards, skills, and connectors. |

Both surfaces share authentication, governed data access, SQL execution,
storage, and the bounded assistant engine. The MySQL proxy exposes the same
Nova-aware SQL path to existing clients.

## Conceptual platform stack

| Layer | Responsibility |
| --- | --- |
| Studio | People and agents work through Missions, evidence, Decisions, Actions, Outcomes, and artifacts. |
| Intelligence runtime | The shared bounded engine composes agents, Smart, skills, tools, and scoped memory. |
| Business context | Context Graph links semantic definitions, knowledge, and decision revisions; business policies govern supported decisions and actions. |
| Semantic layer | Semantic Views define metrics, relationships, and verified queries. |
| Data foundation | SQL, warehouse, catalog, stages, ingestion/export, ML, and StarRocks provide governed data and computation. |
| Control plane | Security, Ranger, Quality, audit, durable metadata, and runtime coordination span the layers. |

These are product layers, not separate deployable services. The
[README technical architecture](../README.md#architecture) shows the actual Web,
API, MySQL proxy, SQL frontend, assistant engine, ML worker, scheduler/workers,
StarRocks, Ranger, Redis, and storage relationships.

## Engineering foundations

- Nova SQL uses the central frontend for syntax, semantic analysis, policy, and
  execution routing. Supported `@stage` references lower to StarRocks
  `FILES(...)` after authorization. Engine compatibility follows the pinned
  version and tests.
- User-facing file access uses stages and configured connection names. Stored
  credentials stay out of responses, logs, provider context, and frontend state;
  deliberate credential-entry flows are separate from stored-secret readback.
- Durable relational Nova metadata belongs in flat `NOVA_SYSTEM` tables in
  StarRocks. Redis owns sessions, caches, locks, leases, and wakeups. File/object
  payloads use managed storage. Ranger's own policy database is separate.
- Console and Studio use React, Vite, and TanStack Router. Backend domains share
  the existing SQL, authorization, storage, audit, and assistant owners.
- Ranger-enabled execution retains principal, one active role, and
  security-context version. Native-RBAC compatibility is a separate supported
  path; it cannot substitute for Ranger checks in a Ranger-enabled deployment.

See the [agent guide](../AGENTS.md) for development invariants and the
[Ranger architecture](arch-08-ranger-authorization.md) for exact trust boundaries.

## Current scope and rollout

Workflow, Action execution, production quality scoring, and analytical workspace
flags default to disabled. Missions require the workflow control. Offline
Quality APIs and manifested-release promotion gates are separate from the
production scoring flag. The analytical workspace remains unavailable even when
its flag is enabled because the shipped executor provides no isolated runtime.

Current Actions configure governed monitors/schedules or internal Studio
automations, with approval, consent, and verification. External delivery and
inventory transfers/rollbacks are outside these adapters. Verification proves
configuration, not business improvement. Outcomes retain observation completeness
and causal-attribution limits; learning cannot silently publish business truth.
External models and MCP services remain mutable despite pinned release contracts.

Use the [governed Studio operator guide](governed-studio-operations.md) for
migrations, rollout controls, recovery, and acceptance gates. Deterministic tests
and ordinary engine integration do not establish patched-FE Ranger enforcement,
live-provider improvement, or complete production readiness.

## Product terminology

| Term | Meaning |
| --- | --- |
| Enterprise Intelligence OS | Nova's product category: governed data, meaning, agents, decisions, actions, outcomes, and learning in one platform. |
| Nova Console | The data and control foundation. |
| Nova Studio | The intelligence workspace. |
| Semantic Views | Versioned, governed business meaning. |
| Context Graph | Governed business context linking semantic, knowledge, and decision revisions. |
| Mission | Durable business work with run, evidence, and object references. |
| Investigation | A canonical analytical investigation with evidence and labelled hypotheses. |
| Decision | A governed decision object with options, assumptions, policy, and approval. |
| Action | Supervised execution through a supported adapter, with consent and verification. |
| Outcome | An observed result with completeness and attribution limits. |
| Quality | Agent evaluation, regression checks, and reviewable improvement proposals. |

Studio **Decision Mode** is the optional model/tool/skill routing mechanism. It
is distinct from a business **Decision** object.

The compact product description for About/help surfaces is:

> Enterprise Intelligence OS that turns governed data into understanding, decisions, actions, and learning.

## Documentation map

| Area | Current entry points |
| --- | --- |
| Local operation | [Run guide](../HOW_TO_RUN.md) |
| Data and SQL | [SQL frontend](arch-13-sql-frontend.md), [stages](04-stage-manager.md), [task orchestration](08-task-manager.md) |
| Semantics and context | [Intelligence foundation](28-intelligence-foundation.md) |
| Agents | [Smart collaboration](arch-11-smart-collaboration.md), [runtime Decision Mode](arch-12-studio-decision-mode.md) |
| Decisions, Actions, Outcomes, and Quality | [Governed Studio](arch-15-governed-studio.md), [operating guide](governed-studio-operations.md) |
| Security | [Ranger architecture](arch-08-ranger-authorization.md), [Ranger runbook](29-ranger-access-control.md) |
| ML | [Native ML runtime](28-native-ml-runtime.md) |
| State | [NOVA_SYSTEM](arch-06-nova-system-database.md) |

The numbered module documents also contain earlier feature specifications.
Historical plans, the [warehouse compatibility roadmap](roadmap-snowflake-parity.md),
and [gap analysis](gap-analysis.md) preserve their original scope; they are not
an inventory of delivered capabilities. Verify implementation and acceptance
status before treating a planned feature as available.
