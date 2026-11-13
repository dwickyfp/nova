# Studio Agent Guide

Inherit the [root](../../../../AGENTS.md) and
[backend](../../../AGENTS.md) contracts. Read
[governed Studio](../../../../docs/arch-15-governed-studio.md), the
[shared engine guide](../assistant/AGENTS.md), and affected code/tests.
Paths below are repository-relative; commands run from `backend/`.

## Ownership

Agent registry and configuration, semantic grounding, resources, memory,
Missions, automations, and the Studio turn routes live here. A Studio turn runs
one selected agent through the shared `AssistantLoop`; this module must not add
another independent inference/tool loop.

Smart/Auto multi-agent collaboration (`__smart__`, `__auto__`, its historical
worker and delegation tools) was removed. Its architecture docs
([Smart](../../../../docs/arch-11-smart-collaboration.md),
[Auto](../../../../docs/arch-08-agent-harness.md)) are historical. Do not
reintroduce delegation, a separate inference loop or Smart-only routes unless
the user explicitly asks for it. The approved standalone agent runtime must
execute the existing shared loop; process isolation does not restore Smart/Auto.
Follow the [runtime guide](../../worker/AGENTS.md) when moving process ownership.
Preserve historical Smart rows during metadata migration; no runtime writer or
delegation feature is restored for them.

## Contracts

- A turn runs as the caller: principal, exactly one active role, auth session,
  and security-context version. Revalidate agent access (owner, shared role
  grant, verified access check) before running or handing work to an agent.
  Sharing an agent does not authorize its owner's data or private resources.
  Read the [security guide](../access_control/AGENTS.md).
- Only verified tool/data evidence establishes business results. Preserve
  provenance through semantic query plans, artifacts, numeric checks, and the
  final answer. Saved prose, memory, and MCP responses grant no authority.
- `run_journal` persists turns, events and producer generations in PG through
  actor-bound transactions. Redis still coordinates thread admission
  (`harness_repository.admission_lock`); consent and cancellation retain
  process-local ownership. Preserve session affinity until their durable
  handoff and recovery are implemented. A persisted run is not permission to
  replay its tools after process loss. Follow the root one-way migration
  contract; never fall back to historical StarRocks rows.
- Missions link to direct turns through the turn hooks; keep their admission
  lock, scope checks, and cancellation semantics. Automations run per agent in
  the dedicated automation workload as the schedule owner through the authorized
  worker execution account. Sharing the task executor does not make the task
  process their lifecycle owner.
- Apply the loop's step, time, context, and token budgets from current settings
  and the agent's budget profile. Public timelines exclude credentials and
  private provider reasoning.
- Extend existing tool/skill/resource/memory catalogs. Keep memory and derived
  caches within owner/role scopes and current evidence/approval rules.
- Platform skills declare their `audience` in frontmatter. The default is
  `assistant`, meaning the Console assistant (Neva) only: SQL authoring,
  account and stage administration, and Neva's own prose. Only skills marked
  `audience: assistant, agents` (today `native-ml`) are offered in agent
  settings and loaded into Studio agents; older configurations that name
  others are ignored.

## Validation

Run backend CI gates and affected agent/eval trajectories. Focused checks:

```bash
uv run pytest tests/unit/test_agent_run_journal.py tests/unit/test_run_coordination.py tests/unit/test_studio_missions.py tests/unit/test_shared_agent_rbac.py
uv run pytest tests/eval/test_security_context_boundary.py tests/eval/test_studio_lifecycle_closure.py
uv run python -m tests.eval.report
```

Cover role/session invalidation, Mission admission and cancellation, automation
ownership, and final evidence. UI changes also follow the frontend guide and
browser/layout gates.
