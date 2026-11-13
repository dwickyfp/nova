<!-- DEVMESH:START version="1" -->

## DevMesh Collaboration

This repository is attached to DevMesh. For substantial work in a DevMesh session:
- Retrieve the current handoff and preserve its goal, constraints, non-goals, and acceptance criteria.
- Answer repository-local questions using source, tests, documentation, and runtime evidence.
- Request ChatGPT research only for consequential external uncertainty or architecture reasoning.
- Report conflicts with the handoff; provide concrete evidence for code investigations.
- Preserve the session and Codex thread across collaboration round-trips.
- Checkpoint before blocking collaboration and satisfy the Definition of Done before completion.

Skills provide the workflow; DevMesh MCP provides dynamic state.
Project configuration: `.devmesh/project.json`.

<!-- DEVMESH:END -->

# Nova engineering contract

Nova shares governed data access across Console, Studio, notebooks, HTTP and
MySQL clients. Read [README.md](README.md) for the product and
[HOW_TO_RUN.md](HOW_TO_RUN.md) for local operation.

## How to use these instructions

- This root file owns repository-wide rules and the development workflow.
  Scoped `AGENTS.md` files own domain-specific contracts and checks. Read the
  applicable guides through the task router below before editing. A scoped
  guide may add requirements; it must not weaken root security or delivery rules.
- Follow the user's explicit scope and recorded decisions. Preserve existing
  authorizations and waivers within that scope; do not repeatedly ask for them
  or extend a waiver to unrelated work. Skills support this workflow.
- Distinguish observed runtime from the approved target. Verify implementation
  facts in source and tests, then manifests/configuration, current architecture
  and operations docs. Historical specs, benchmarks and proposals are context.
  Existing code does not cancel an approved migration, and a target diagram
  does not prove implementation.
- Keep changing inventories, test counts, rollout state and defects out of these
  guides. Read the [acceptance contract](docs/architecture/production-refactor/ACCEPTANCE.md),
  [traceability](docs/architecture/production-refactor/TRACEABILITY.md) and
  [delivery/QA backlog](docs/architecture/production-refactor/DELIVERY_STATUS_AND_QA_BACKLOG.md).
  A prepared store, passing fixture or separate process is not completed cutover
  or proven horizontal scaling.

## Development workflow

1. Inspect the active checkout, branch, staged/unstaged changes and relevant
   services. Identify concurrent work and preserve it. Retrieve the current
   DevMesh handoff when a development session exists; keep its goal, constraints
   and acceptance criteria across collaboration round-trips.
2. Identify the owning domain, actual callers/writers and applicable guides.
   For a new app, domain, workload or architectural replacement, record its
   entrypoints, caller identity, metadata/payload owners, external effects,
   execution role, failure/recovery behavior and acceptance checks. Reuse the
   existing architecture document or PR; a small fix needs no separate proposal.
3. Implement through the existing owners. Update affected callers, schemas,
   runtime wiring, permissions and documentation together. Keep APIs, SQL,
   opaque IDs, owners, notebooks and artifacts compatible unless the approved
   change explicitly alters them. New visible behavior is active through the
   normal product path.
4. Run focused checks to diagnose problems, then all applicable validation
   gates on the delivered source. Check invalid input, authorization, dependency
   failure and concurrency as relevant. Fix defects and rerun affected checks;
   retain earlier failed attempts in acceptance evidence.
5. Review the complete owned diff, secret boundaries and documentation. Update
   migration/QA records with what was implemented, what is active, what was
   tested and what remains open. A checkpoint records incomplete work without
   marking the feature or release complete.
6. Synchronize, commit, integrate and push using the delivery rules below.
   Apply changed processes to the user's active checkout using existing
   infrastructure. Report the branch, validation and any remaining limits.

When a decision or prerequisite is genuinely missing, finish independent work
and ask a concrete question. Do not interrupt authorized, reversible work for
routine implementation choices. Do not leave required TODOs or only a happy
path and describe the request as complete.

## Architecture contracts

### Identity and governed data

- StarRocks authenticates Nova users. Do not create another user/password store.
  Preserve principal, exactly one named active role, login/session identity and
  security-context version through SQL, tools, Studio, ML, delegation and caches.
- Ranger and the patched StarRocks FE govern user data, including filters and
  masks. `SHOW GRANTS` establishes role assignments, not Ranger authorization.
  Start from the explicit default role; fail closed on ambiguous or stale
  identity. Do not add a Ranger-disabled/native-RBAC bypass.
- Never substitute root, system, service or agent-owner credentials for a
  caller's data access. Explicitly authorized service-principal work retains
  its own identity and authorization checks.
- Preserve `ACCOUNTADMIN` guards in SQL, services, bootstrap and UI. Do not
  drop, alter, rename or revoke its privileges. Preserve redacted audit events
  for governed actions and refusals.

### State and effects

| State | Owner under the approved target |
| --- | --- |
| Durable Nova control metadata | PostgreSQL 17, `nova_control.nova_meta` |
| User authentication and governed analytical data | StarRocks and Ranger |
| Stream journal that must commit with analytical data | The same StarRocks transaction |
| Durable jobs, definitions, outbox, claims, fencing, consent and receipts | PostgreSQL |
| Sessions, caches and wakeups | Redis; separate critical and cache workloads |
| File, notebook and model payloads | Existing managed object/file storage owner |
| Ranger and Superset application metadata | Their separate databases |

Existing domains remain on `NOVA_SYSTEM` until verified cutover. Inventory every
writer before porting. Do not introduce production dual-write, an opt-in metadata
backend flag, a generic SQL dialect-rewrite proxy or fallback to stale metadata.
PG metadata is never exposed as a user SQL connection.

Use the [metadata migration guide](backend/metadata_migrations/AGENTS.md) for
actor-bound transactions, grants/RLS, revision CAS, import and epoch activation.
Application processes never use the owner/migrator login or migrate PG at startup.
Persist intent before external effects. Lease loss or an unknown commit outcome
requires verification/reconciliation, not automatic replay of a mutation.

### Runtime and deployment

- Keep HTTP request handling separate from long-running workload ownership.
  Use the [runtime guide](backend/app/worker/AGENTS.md) for process admission,
  supervision, cancellation, fencing, readiness and adding roles. A process split
  or larger resource limit alone does not establish safe replicas.
- The accepted production target is one Compose file with inline environment
  and privilege-scoped YAML anchors, without `.env` or an override chain.
  All required services start normally. Follow the [Docker guide](docker/AGENTS.md)
  for the current development topology and production acceptance.
- Git holds a credential-free template. The actual deployment file stays
  outside Git with restricted permissions, stable secrets and adjustable
  resources. Use pinned images, private management ports, TLS, bounded resources,
  log rotation, controlled shutdown and tested off-host recovery.
- Notebook, Python and MCP execution must not receive infrastructure credentials,
  host mounts or the Docker socket. Only the OpenSandbox controller holds the
  required Docker access. Isolate ML execution from the API and verify artifact
  provenance. Do not claim these target boundaries are complete without tests.
- This delivery accepts single-host Docker OpenSandbox. Do not add KVM, Kata,
  Kubernetes or separate hosts without an approved scope change. Single-host
  operation does not establish cross-host HA or enterprise isolation.

### Shared extension points

- Find the existing owner before adding infrastructure. A new app or feature
  uses shared identity, policy, SQL, storage, audit and bounded assistant
  services. Do not create a parallel repository framework, parser, planner,
  authorization engine, scheduler, semantic execution path or inference loop
  unless the task explicitly replaces that architecture.
- Nova owns SQL syntax, binding, policy and routing; StarRocks owns physical
  planning/execution. New syntax goes through `backend/app/sql_frontend/`.
  Keep plans credential-free; authorize stages before resolving secrets.
  Unsupported managed syntax fails explicitly rather than passing through.
- Studio runs one selected agent through `backend/app/modules/assistant/`.
  Historical Smart/Auto delegation is not the new agent process boundary.
  Do not restore multi-agent product behavior unless explicitly requested.
- Verify engine-sensitive behavior against the pinned image, patches and tests.
  Newer upstream behavior or another database is not evidence of support.

### Product behavior and secrets

- Features are active by default. Do not add enable/disable flags, opt-in modes,
  kill switches or parallel old/new behavior unless explicitly required.
  Existing flags are not permission to add more. Authorization, consent,
  provider setup and infrastructure prerequisites still apply. Topology,
  transport, tuning and destructive-operation acknowledgements are configuration.
- Use existing encryption and secret resolution. Session passwords stay encrypted
  in Redis; supported provider credentials use encrypted persistence. Never expose
  stored secrets
  or credential-bearing execution state in responses, logs, diagnostics,
  frontend state, queues, artifacts or provider context. Keep intentional
  credential-entry flows distinct from stored-secret readback.
- Every user-visible/API/SQL/novaduck change updates the corresponding `/docs`
  page in the same change. Follow the [authoring guide](frontend/src/features/docs/content/AUTHORING.md).
  Run every applicable SQL/Python example in Nova before documenting it as working.
  Use plain English and exact UI labels; keep physical endpoints, internal
  paths, storage vendors, environment variables and credentials out of user flows.
  User-facing file access uses `@stage` and configured connection names.

## Task router

Read cross-scope guides even when the edited file is outside their directory.

| Change | Read before editing |
| --- | --- |
| Backend app/domain, repository, API or new capability | [Backend](backend/AGENTS.md) |
| PG schema, metadata UoW, grants/RLS, imports or cutover | [Metadata](backend/metadata_migrations/AGENTS.md), [ownership inventory](docs/architecture/production-refactor/METADATA_OWNERSHIP.csv) |
| HTTP lifecycle, workers, scheduler, proxy process, admission or scaling | [Runtime](backend/app/worker/AGENTS.md), [Docker](docker/AGENTS.md) |
| SQL frontend, grammar, query/proxy consumers, dialects | [SQL](backend/app/sql_frontend/AGENTS.md), [architecture](docs/arch-13-sql-frontend.md) |
| Auth, roles, Ranger, stages, filters/masks, user-data adapters | [Access control](backend/app/modules/access_control/AGENTS.md), [Ranger](docs/arch-08-ranger-authorization.md) |
| Assistant loop, providers, consent, tools | [Assistant](backend/app/modules/assistant/AGENTS.md) |
| Studio agents, Missions, automations, resources, memory | [Agents](backend/app/modules/agents/AGENTS.md), [Studio](docs/arch-15-governed-studio.md) |
| Workspace files, versions, shares, object publication | [Workspaces](backend/app/modules/workspaces/AGENTS.md) |
| Notebook documents/runtime, OpenSandbox, `nova_runtime`, data tokens | [Notebooks](backend/app/modules/notebooks/AGENTS.md), [architecture](docs/arch-20-nova-notebook.md), [Access control](backend/app/modules/access_control/AGENTS.md) |
| Query Autopilot and recommendation/apply lifecycle | [Autopilot](backend/app/modules/query_autopilot/AGENTS.md) |
| Superset, saved charts/dashboards, `/bi`, BI tokens, legacy artifacts | [Superset](docs/arch-17-superset.md), [Access control](backend/app/modules/access_control/AGENTS.md), `superset/`, `backend/app/modules/superset/` |
| Studio Dataviz, publication/repair, assistant and role-scoped dashboards | [Dataviz](docs/arch-19-studio-dataviz.md), [Superset](docs/arch-17-superset.md), `frontend/src/features/studio/dataviz/` |
| Inline charts, ShowMe, presentation models, legacy views | [Viz](docs/arch-16-viz-core.md), `backend/app/modules/viz/` |
| Ontology domains, concepts, relations, metrics, releases | [Ontology](docs/arch-18-business-ontology.md), [Access control](backend/app/modules/access_control/AGENTS.md), `backend/app/modules/ontology/` |
| Frontend routes, components, labels or layout | [Frontend](frontend/AGENTS.md), [Design](DESIGN.md) |
| User docs and `/docs` | [Authoring](frontend/src/features/docs/content/AUTHORING.md), [Frontend](frontend/AGENTS.md) |
| Compose, bootstrap, images or container-dependent test fixtures | [Docker](docker/AGENTS.md) |
| StarRocks patches or FE image | [Patch guide](patches/starrocks/AGENTS.md), [baseline](patches/starrocks/README.md) |

## Validation routing

[CI](.github/workflows/ci.yml) owns commands, pins and setup. Scoped guides add
behavior gates; focused tests do not replace applicable suites.

| Change | Required checks |
| --- | --- |
| Instructions or engineering docs only | Diff, local links, source-backed claims, hierarchy and task routing; application suites are not required for prose alone |
| User docs | Execute applicable examples live; docs browser tests; view `/docs` |
| Backend code | Unit/coverage, agent scorecard, changed-file Ruff and full-application mypy |
| PG metadata/schema/import | Backend gates plus real PG17 transaction, grant/RLS, migration and concurrency checks; engine compatibility when affected |
| DB access, SQL, auth, storage or proxy | Backend gates plus applicable seeded real-engine integration |
| SQL grammar/generated parser | Pin/drift check, regeneration/artifact diff, SQL and consumer regressions |
| Agent behavior | Backend gates plus affected `tests/eval/` trajectories; benchmarks when overhead changes |
| Ranger/security/engine patch | Applicable backend/integration checks plus patched-FE policy and role acceptance |
| Frontend code | `pnpm lint`, `pnpm build`, `pnpm test:coverage`, browser setup and affected layout/state checks |
| Notebook runtime image | Image build/kernel tests and affected governed notebook flows |
| Superset image or `nova_superset` | CI Superset image/package checks and governed Superset acceptance |
| Runtime or deployment | Applicable code checks, Compose validation, affected startup/readiness/shutdown and recovery/replica checks |

Full-tree Ruff is report-only; changed-file Ruff and full-application mypy are
blocking. Never disable checks or weaken assertions to obtain a pass. Keep
local, CI, governed-engine, live-AI and fault evidence distinct. Record commit,
image/schema versions, topology, commands, results, skips and sanitized artifacts.
Mocked providers do not replace live AI evidence. Retain every live attempt;
use known data, output schemas and expected tool effects rather than prose matches.

No collected tests, skipped cases, missing prerequisites and owner waivers are
not passes. Apply the release-specific load/embedding decisions in the acceptance
contract without weakening other gates. Required unavailable checks block release
and normally block commit/push; an explicit scoped user authorization may move
those checks to CI. Record that exception and follow the resulting CI to its
outcome. Cancelled/superseded runs are not passes.

## Infrastructure and delivery safety

Before any container-dependent command or fixture, inspect `docker compose ls`
and `docker ps`, identify the existing project/ports and inspect setup/teardown.
Reuse the compatible stack. Never create a duplicate FE/BE or supporting stack
because a default port is unavailable. Preserve development data and volumes;
reseed/fault tests require existing disposable resources or authorized CI.
Ask before creating a separate stack or resetting shared state unless that exact
work is already authorized. Start a stack automatically only when none exists
and initialization is safe; rebuild changed services within the existing project.
See [Docker operations](docker/AGENTS.md) for the procedure.

- Work in the active checkout when safe. Preserve unrelated staged, unstaged
  and untracked changes; commit only the owned goal. Do not create a worktree
  automatically. New branches use `feat/<goals>` with short lowercase kebab-case
  names, without `codex`.
- Before every commit and again before push, fetch remote `main` and check for
  missing commits. Integrate them without discarding concurrent work, resolve
  conflicts and rerun affected checks. Tests must cover the final delivered
  source, dependencies and configuration; reuse results only while those remain
  unchanged. Documentation-only edits need documentation checks.
- After applicable checks pass, commit and push, setting upstream on first push.
  Synchronization, required-check, commit, push or required integration failure
  blocks a completion claim. Report incomplete work honestly at checkpoints.
- If a worktree is needed, first record the original checkout and active branch
  as the delivery target and inspect `git worktree list`. Do not assume `main`.
  Once validated, merge the feature into that target, validate integration and
  push it. This merge is part of authorized delivery. If the target changed or
  safe conflict resolution needs a product decision, preserve work and ask.
- Ensure local processes run the delivered source in the original checkout.
  After successful delivery, preserve needed uncommitted/untracked/ignored files,
  verify the feature is included, then remove only this task's temporary worktree
  through its owning app or `git worktree remove`. Never force removal, delete
  the original checkout or remove another task's worktree. Verify cleanup and
  report its status with the delivery branch and checks.

## Maintaining the guides

Keep root rules stable and shared; place domain details beside their owner.
Add a scoped guide only when it has distinct contracts, entrypoints or validation
requirements, and add its cross-scope route above. Link to existing owners instead
of copying their instructions. Remove superseded guidance in the same change;
label target behavior and historical material explicitly. Check every affected
parent/child guide when architecture changes. Never describe a prepared port,
new service definition or unrun acceptance case as completed behavior.

<!-- antislop:start -->
## Task-specific skills

Apply only relevant project skills: [antislop core](.agents/skills/antislop/SKILL.md)
plus `antislop-copywriting` for prose, `antislop-ui` for visuals, `antislop-human`
for accessibility, `antislop-layoutmobile` for responsive layout, or
`antislop-code` for comment hygiene, from `.agents/skills/`. Backend work does not require
unrelated design skills. Clear implementation tasks apply guidance during work;
explicit audits use audit mode without a mandatory mode-selection question.
Frontend-specific precedence and layout activation live in its scoped guide.
Within these packaged skills, `antislop.md` means the core file linked above;
`skills/antislop-*/` means the corresponding folder under `.agents/skills/`.
<!-- antislop:end -->
