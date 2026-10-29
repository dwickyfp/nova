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

# Nova Agent Guide

## Nova in 30 seconds

Nova is an Enterprise Intelligence OS that connects governed data, business
meaning, agents, decisions, actions, outcomes, and learning. StarRocks provides
its analytical engine and authentication. Console and Studio share governed
data access, SQL execution, storage, and the bounded assistant engine. The MySQL
proxy exposes the same Nova SQL path.
Read [README.md](README.md) for product context and [HOW_TO_RUN.md](HOW_TO_RUN.md)
for local operation.

## Source of truth

This file defines development invariants, not an inventory of implementation
facts. Read the applicable nested `AGENTS.md` before editing its scope. Nested
instructions refine these contracts; they must not weaken them. The task router
also names guides to read for changes outside their directory scope.

For implementation facts, verify code and tests first, then manifests and
configuration, current architecture docs, operational docs, historical specs,
and finally proposals/research. An explicit migration can describe intended
behavior that is not implemented yet: check its status and tests rather than
treating either old code or a proposal as an automatic replacement mandate.

Not every document in `docs/` describes the current runtime. Smart and the SQL
frontend are current extension points; Auto is legacy recovery. Historical
architecture examples, dated benchmarks, proposals, and research are
non-normative unless current code/tests or a requested migration establish them.
Do not copy stale examples into production. Report unrelated documentation debt.

## Architecture invariants

### New features are active by default

- Implement requested features as normal, active product behavior. Do not add
  an enable/disable flag unless the user's requirements explicitly call for it.
  This includes environment variables, backend configuration, frontend flags,
  UI switches, rollout gates, and flags whose default is `true`.
- Do not invent opt-in modes, kill switches, or parallel old/new behavior for
  hypothetical rollout, rollback, experimentation, or compatibility needs.
  If a requirement explicitly needs a switch, implement and document its
  purpose and default as specified by that requirement.
- Verify new behavior through the normal application path without setting an
  extra enable flag. Preserve authorization, consent, and required provider or
  connection setup; active by default does not bypass those controls.
- Existing flags are not a precedent for adding new ones. Change or remove
  existing flags only when the requested work covers them.

### Security and authorization

- StarRocks authenticates Nova users; do not create another user/password store.
- Preserve the authenticated principal, exactly one named active role, and
  security-context version across SQL, Studio,
  Semantic Views, ML, delegation, and data-derived caches. Ranger policies,
  row filters, and masks govern user data through the patched StarRocks FE.
- Do not substitute root, system, service, or agent-owner credentials for a
  caller's data access. Existing explicitly authorized service-principal work
  is a separate supported path, not a bypass.
- `SHOW GRANTS` can establish role-marker assignments; it cannot replace Ranger
  authorization. Reject ambiguous
  security contexts and preserve fail-closed checks and consent boundaries.
- Preserve `ACCOUNTADMIN` protection in SQL guards, administration services,
  bootstrap, and UI. Do not drop, alter, rename, or revoke its privileges.

### State, storage, and credentials

- Durable relational Nova control-plane metadata belongs in StarRocks
  `NOVA_SYSTEM`. Do not introduce another relational Nova metadata database.
  Ranger's own policy database belongs to Ranger, not Nova's control plane.
- Redis owns runtime sessions, caches, locks, leases, and wakeups. It is not a
  second durable relational source of truth. Object/file payloads belong in
  the existing managed storage abstraction.
- Inspect [docker/init-nova.sql](docker/init-nova.sql) and the owning schema
  initialization before changing tables. Follow the flat names already used
  there, such as `CONFIG_STAGES` and `AUDIT_LOG`; do not invent nested schemas.
- User-facing file access uses `@stage` and configured connection names. Keep
  storage vendors, physical endpoints, and credential-bearing paths out of UI.
- Never commit raw deployment secrets. Configuration may hold metadata,
  environment placeholders, and supported secret references. Session passwords
  use encrypted Redis state; supported provider keys use encrypted persistence.
  Never expose stored secret material or credential-bearing execution state in
  responses, logs, diagnostics, frontend state, or provider context. Keep
  deliberate credential-entry flows separate from stored-secret readback.
- Preserve redacted audit logging for governed actions, including refusals.

### Feature flags

- Core features are always on. Do not add a setting, environment variable, or
  UI toggle that enables or disables a feature (an `*_ENABLED` style flag)
  unless the user explicitly asks for that flag. Ship a new feature active, and
  gate it only on real prerequisites such as authorization or infrastructure.
- Ranger authorization is not optional: every session activates exactly one
  named role, starting from the user's default role. Do not reintroduce a
  native-RBAC or "Ranger disabled" mode.
- Deployment topology, transport, tuning, and explicit safety acknowledgements
  for destructive operations are configuration, not feature flags.

### SQL and agents

- Nova owns SQL syntax, semantic analysis, policy, and execution routing;
  StarRocks owns physical planning and distributed execution. New syntax goes
  through `backend/app/sql_frontend/`, not ad-hoc service detection or a new
  parser. Existing guard and stage-lowering helpers retain their responsibilities.
- Plans remain credential-free. Authorize stage access before resolving storage
  secrets at execution. Never forward unsupported managed Nova syntax silently.
- Studio and specialists compose the shared bounded engine in
  `backend/app/modules/assistant/`. Do not create a second inference/tool loop.
  Smart is current; extend Auto only for explicitly requested compatibility or
  recovery. Delegation cannot broaden access or turn coordination text into
  trusted data evidence.
- Find the existing owner before adding infrastructure. Do not introduce a
  parallel parser/planner, authorization engine, storage abstraction, repository
  layer, semantic execution path, scheduler, agent loop, or audit subsystem
  unless the task explicitly replaces that architecture.
- Verify engine-sensitive behavior against the pin, patches, and tests; do not
  infer support from another database or newer upstream documentation.

## Task routing

| Task | Read before changing code |
| --- | --- |
| Backend domains, repositories, APIs, metadata | [backend guide](backend/AGENTS.md) |
| SQL frontend, query/proxy consumers, grammar/generated parser, dialect compatibility | [SQL guide](backend/app/sql_frontend/AGENTS.md), [current SQL architecture](docs/arch-13-sql-frontend.md) |
| Auth, active roles, stages, Ranger adapters, filtering/masking | [access-control guide](backend/app/modules/access_control/AGENTS.md), [Ranger architecture](docs/arch-08-ranger-authorization.md) |
| Shared agent loop, context, providers, consent, tools | [assistant guide](backend/app/modules/assistant/AGENTS.md) |
| Studio, Smart, delegation, resources, memory, recovery | [agents guide](backend/app/modules/agents/AGENTS.md), [Smart architecture](docs/arch-11-smart-collaboration.md) |
| Viz core, charts, dashboards, ShowMe, presentation models | [viz architecture](docs/arch-16-viz-core.md), `backend/app/modules/viz/` |
| Superset BI surface, `/bi`, BI tokens, `nova_superset` | [Superset architecture](docs/arch-17-superset.md), [access-control guide](backend/app/modules/access_control/AGENTS.md), `superset/`, `backend/app/modules/superset/` |
| Frontend routes, components, copy, layout | [frontend guide](frontend/AGENTS.md), [design system](DESIGN.md) |
| Compose, bootstrap, backend test infrastructure | [Docker guide](docker/AGENTS.md) |
| StarRocks patches or FE image | [patch guide](patches/starrocks/AGENTS.md), [patch baseline](patches/starrocks/README.md) |

## Validation routing

[.github/workflows/ci.yml](.github/workflows/ci.yml) owns CI commands and setup.
Read it before selecting checks; scoped guides give commands and additional
behavior gates. Focused tests diagnose changes, but do not replace applicable
CI suites. Changes spanning backend and frontend require both sets of checks.

| Change | Required validation |
| --- | --- |
| Documentation/instructions only | Diff, references, stale claims, hierarchy consistency, task routing |
| Backend code | Unit suite with coverage, agent eval scorecard, blocking Ruff on changed Python |
| DB access, SQL execution, auth, storage, proxy | Backend checks plus seeded real-engine integration |
| SQL grammar/generated parser | Grammar pin/drift, regeneration and artifact diff, relevant SQL tests and consumer regressions |
| Agent behavior | Backend checks plus affected `tests/eval/` trajectories; benchmarks when overhead changes |
| Ranger/security or engine patch | Backend/integration checks as applicable plus patched-FE policy/role acceptance |
| Frontend code | `pnpm lint`, `pnpm build`, `pnpm test:coverage`, CI browser setup and affected layout/state checks |
| Superset image or `nova_superset` | CI `superset` job (image build, package tests) plus `test_superset_governed.py` on the governed stack |

Full-tree Ruff is report-only in CI; changed-file Ruff and full-application mypy
are blocking. Do not disable tests, weaken assertions, or bypass gates. No tests
collected or every test skipped is not a pass. If a required check cannot run,
report the missing prerequisite and stop before commit/push unless the user
explicitly authorizes an exception.

## Safe autonomy and completion

Inspect, implement, test, fix, and verify within the user's authorized scope.
Ask only for genuinely missing product decisions or actions requiring approval;
do not interrupt clear reversible work for routine choices. Use safe repository
search/editing tools and shell commands suited to the environment. Preserve
unrelated changes; avoid destructive or broad edits.

Reuse the running local Nova stack, including StarRocks and its supporting
services, for tests and checks. Before running a container-dependent test,
script, fixture, `docker compose up`, or `docker run`:

- Inspect `docker compose ls` and `docker ps`, identify the existing project,
  services, and published ports, and configure the test endpoints to use them.
- Inspect test setup and teardown for automatic container creation or removal.
  A fixture or helper that starts a stack is subject to the same reuse rule as
  a direct Docker command. Do not run it until reuse is verified.
- Do not create another StarRocks FE/BE or supporting stack per test, suite,
  feature, chat, branch, or worktree. A different compose file, project name,
  default port, or unavailable endpoint is not permission to create a duplicate.
- Preserve existing data and volumes. Use an existing disposable test stack
  for destructive seed/reset suites. If the running stack cannot safely satisfy
  a check, report the specific prerequisite and ask before creating a separate
  stack or resetting shared state. Never silently substitute an unpatched
  engine for governed acceptance or count skipped checks as passed.
- Start a local stack automatically only when no Nova stack is running and
  initialization is safe; keep it available for subsequent checks. Rebuild a
  changed service within its existing project when required by the task.
  Ephemeral CI provisioning does not authorize duplicate local stacks.

The [Docker guide](docker/AGENTS.md) gives the operational steps.

For implementation requests, finish the requested behavior and validation rather
than leaving scaffolding, required TODOs, or only a happy path. Review the final
diff, security/secret boundaries, and affected documentation. Remove temporary
debug work. Report what changed, commands/results, and remaining limitations;
distinguish local checks from verified GitHub Actions results.

- New branches use `feat/<goals>` with a short lowercase kebab-case goal. Do not
  use `codex/` or include `codex` in branch names.
- After completing a coding goal, pass applicable checks, commit only that
  goal's changes, and push before reporting completion. Set upstream on the
  first push. Use a commit message describing the result.
- Before every commit and again before pushing, fetch the latest remote `main`
  and check for missing commits against that fetched branch. Integrate new
  commits, preserve uncommitted work, resolve conflicts, and rerun relevant checks.
- Checks must cover the exact delivered changes after the final edit and any
  integration/conflict resolution. Reuse a passing result only when tested code,
  dependencies, and configuration remain unchanged. Documentation-only work
  needs documentation checks; code/configuration edits need application checks.
- A synchronization, required-check, commit, push, required merge, or worktree
  cleanup failure blocks completion.

### Deliver features to the active checkout and clean up worktrees

- Do not create a worktree automatically for every feature. Prefer the user's
  active checkout when work can proceed safely there. Use a worktree only when
  requested or needed to isolate changes from concurrent or unrelated work.
- Before working in a separate worktree, record the user's original checkout
  path and its active branch as the delivery target. Inspect `git worktree list`
  and branch/status information; do not assume the target is `main` or the
  temporary feature branch. If the target cannot be established, clarify it.
- After implementation and required checks pass, commit the feature and merge
  it into that recorded target branch in the original checkout. This merge is
  part of completing the feature and does not need another routine approval.
  Preserve unrelated staged, unstaged, and untracked work. Resolve conflicts
  without discarding others' changes; if the target branch has changed or safe
  integration requires a user decision, report the blocker before proceeding.
- Follow the synchronization rules above, validate the integrated result, and
  push the target branch before reporting completion. A commit or push confined
  to the feature worktree does not complete delivery. Ensure local development
  processes use the original checkout and reload/restart affected processes as
  needed, reusing the existing containers, so the user can test there directly.
- Once delivery succeeds, verify the feature commits are included in the target
  and preserve any needed uncommitted, untracked, or ignored files before
  removing the task's temporary worktree. Use the owning app's cleanup tool for
  managed worktrees; otherwise use `git worktree remove` from outside that
  worktree. Never force removal, delete the original checkout, or remove a
  worktree still used by another task. Verify cleanup with `git worktree list`.
- Report the delivery branch, validation result, and worktree cleanup status.
  Do not leave completed features only in a worktree or require the user to
  switch directories to test them. If integration or cleanup is blocked,
  preserve the work and report what remains instead of claiming completion.

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
