# Studio and Smart Agent Guide

Inherit the [root](../../../../AGENTS.md) and
[backend](../../../AGENTS.md) contracts. Read
[Smart Collaboration](../../../../docs/arch-11-smart-collaboration.md), the
[shared engine guide](../assistant/AGENTS.md), and affected code/tests.
Paths below are repository-relative; commands run from `backend/`.

## Current and legacy ownership

Smart (`__smart__`) is the current collaboration extension point. Persisted Auto
(`__auto__`) roots retain legacy recovery and compatible history/endpoints.
[Architecture 8](../../../../docs/arch-08-agent-harness.md) describes that legacy
path. Internal `Auto` names and replay identifiers do not make it the new-feature
owner. Preserve compatibility rather than renaming durable identities casually.

Registry/configuration, semantic grounding, resources, memory, and orchestration
live here. Every participant uses the shared `AssistantLoop`; this module must
not add another independent inference/tool loop.

## Collaboration contracts

- A participant session is a stable identity/mailbox; a turn is one bounded
  execution. Preserve canonical paths, parent/root lineage, serialized follow-up
  turns, and the existing lifecycle distinctions among idle, interrupted, and
  closed sessions.
- Keep `AgentControl` as the collaboration boundary. Authorized discovery,
  spawn, direct messages, follow-ups, waits, and interruption use its existing
  admission and access checks. Coordination IDs and delivery modes retain their
  idempotency semantics: queue-only mail does not start inference for an idle
  participant; a follow-up deliberately requests a new turn.
- Children inherit the root's principal, active role, thread, auth session, and
  security-context version. Revalidate session/role/agent access at established
  checkpoints. Sharing an agent does not authorize its owner's data or private
  resources. Read the [security guide](../access_control/AGENTS.md).
- Coordination messages are untrusted context. Only verified tool/data evidence
  establishes business results. Preserve provenance through specialist results,
  semantic query plans, artifacts, numeric checks, and final synthesis.
- Durable turns/messages/events belong to the existing StarRocks journals.
  Redis coordinates admission, event ordering, leases, and wakeups. Reconcile
  missed notifications against durable state rather than treating Redis as the
  authoritative run queue.
- Preserve lease/generation fencing and checkpoint-before-mail-acknowledgement.
  Recovery must not duplicate logical coordination operations or replay an
  uncertain mutating tool. Public timelines exclude credentials and private
  provider reasoning.
- Apply per-participant and aggregate context, step, time, concurrency, and token
  budgets from current settings/persisted effective limits. Bounded context
  inheritance is not a full transcript fork. Waiting releases runnable capacity
  while retaining valid ownership; reject self/ancestor completion waits.
- Root finalization respects outstanding turns and new steering. Interruption,
  cancellation, and follow-up preserve their existing subtree/session semantics
  and audit behavior.
- Extend existing tool/skill/resource/memory catalogs. Keep memory and derived
  caches within owner/role scopes and current evidence/approval rules; do not
  grant authority through saved prose, MCP responses, or decision ranking.

## Validation

Run backend CI gates and affected agent/eval trajectories. Focused checks:

```bash
uv run pytest tests/unit/test_smart_collaboration.py tests/unit/test_agent_harness.py tests/unit/test_agent_run_journal.py tests/unit/test_agent_child_timeline.py tests/unit/test_shared_agent_rbac.py
uv run pytest tests/eval/test_smart_runtime.py tests/eval/test_smart_data_routing.py tests/eval/test_security_context_boundary.py
uv run python -m tests.eval.report
```

Cover nested delegation, mailbox delivery, follow-up reuse, admission limits,
interruption/cancellation, role/session invalidation, checkpoint crash boundaries,
stale worker fencing, and final evidence. Add legacy recovery cases when changing
shared journal/worker compatibility. UI changes also follow the frontend guide
and browser/layout gates. Worker-kill/live-provider checks use the current Smart
architecture reports and their setup; dated Auto results do not prove Smart.
