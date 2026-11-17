# Nova Native Dataviz — Codex Production Handoff

This package targets `nova-main (4).zip` as uploaded 2026-11-13. It converts Dataviz from a Superset iframe to fully native datasets, charts, dashboard designer and Neva collaboration. Archive SHA-256: `2b3e4edb35743171f2c06c94e150959fc7441a570a12e61b1e4793a98258e966`. No code changes have been applied; this is a **research-grounded implementation contract** and static source audit.

## Package contents

- `CODEX_START_HERE.md` — precise Codex runbook and safe implementation order.
- `NOVA_NATIVE_DATAVIZ_PRODUCTION_SPEC.md` — architecture, typed data/permission/API contracts, migration, security, workflows, 17 work packages and release gates.
- `NOVA_NATIVE_DATAVIZ_DESIGN_SYSTEM.md` — Nova-token-grounded interface design for Dataviz and Agent perspectives.
- `NOVA_NATIVE_DATAVIZ_DATA_CONTRACTS.md` — proposed PostgreSQL DDL, request/response examples, idempotent operation transaction and filter/layout contracts.
- `NOVA_DATAVIZ_INTEGRATION_MAP.md` — end-to-end data flow, exact module ownership and frontend/backend integration map.
- `NOVA_V4_CODE_AUDIT_EVIDENCE.md` — file/line evidence from actual uploaded v4 source.
- `NOVA_NATIVE_DATAVIZ_QA_MATRIX.csv` — 254 acceptance scenarios (65 CRITICAL / 189 HIGH) initially NOT_RUN.
- `REFERENCE_DATAVIZ_BUILDER.png` — concept UI composition reference.
- `REFERENCE_AGENT_COLLAB.png` — concept Agent Artifact Panel reference.

## Boundaries

- **Native**: GridStack.js MIT, Apache ECharts Apache-2.0, TanStack Table MIT, existing Nova SQL/Viz Core, PostgreSQL actor-bound metadata and S3-compatible exports.
- **Security**: active role + Ranger enforced on every data query; dashboard share never grants data privilege; distinct export permission.
- **AI**: same Neva harness and shared document operation service; no pixel-based browser automation for native Chart/Dashboard.
- **Migration**: no production dual-write; inventory and verified safe cutover before deleting Superset.
- **Design**: reuse actual `DESIGN.md` colors/typography. Reference screenshots are for spatial layout and behavior only.

## Status

Static audit completed, specs and test inventory authored; **implementation and QA have not run**. Refer to the current repo's `docs/architecture/production-refactor/DELIVERY_STATUS_AND_QA_BACKLOG.md` for the true status of PostgreSQL metadata/HA migration.
