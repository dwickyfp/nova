# Assistant Engine Agent Guide

Inherit the [root](../../../../AGENTS.md) and
[backend](../../../AGENTS.md) contracts. Read the
[engine design and measurements](../../../../docs/benchmarks/nova-124-agentic-harness.md)
as a dated record, and verify current behavior in this module and tests.
Paths below are repository-relative; commands run from `backend/`.

## Shared engine

- `AssistantLoop` owns provider/tool iteration. Nove, direct agents, and Smart
  participants compose this engine with their own context and registries.
  Studio orchestration belongs in the sibling agents module; do not fork a loop
  for a new mode, tool, or domain.
- Keep provider behavior behind existing provider/capability interfaces. A
  provider's private reasoning is not public activity, an artifact, or evidence.
  Publish bounded redacted intent, actions, results, and verified answers.
- Use `ContextManager` for finite context: retain the system/recent window,
  clear stale tool payloads, and bound earlier-turn notes. Do not replay an
  unbounded transcript. Preserve explicit `context_overflow` termination.
- Respect the existing iteration, wall-time, context-token, provider-output,
  and per-tool bounds, including checkpoint/resume behavior. Do not reset
  consumed budgets on recovery or create a second provider retry loop.
- New tools use the shared registry, established effect/read-only classification,
  and fail-closed consent rules. Make agent-selectable tools available through
  `backend/app/modules/agents/tool_catalog.py` as appropriate. Unknown effects
  or missing consent are not implicit permission.
- Preserve authenticated caller context through tools. Read the
  [access-control guide](../access_control/AGENTS.md) for data/role changes;
  use the common SQL frontend for query execution and SQL-only validation.
- Keep tool outputs, attachments, external documents, and coordination text at
  their existing trust level. Completion claims require verified data evidence
  with source/provenance, not catalog listings or narrative assertions.
- Preserve ownership of approvals and cancellation. Retry only the established
  retryable failure classes; authorization, policy, consent, and secret failures
  remain terminal. Do not replay an uncertain mutating operation after recovery.
- Separate ephemeral loop/provider state from the existing persisted thread,
  artifact, and run journals. Do not introduce another persistence path or leak
  secret-bearing context into snapshots/public trace projections.

Read the [agents guide](../agents/AGENTS.md) for Smart checkpoints, participant
identity, and delegation. For decision-mode selection read
[Architecture 12](../../../../docs/arch-12-studio-decision-mode.md). Decision
ranking cannot authorize a candidate, expand an executable plan, replace SQL
validation, or bypass consent; uncertain decisions retain the existing path.

## Behavior validation

Loop control-flow, new tools/guards, and termination/retry behavior require
scripted trajectories in `backend/tests/eval/`, not only unit or wall-clock tests.
Assert selected tools, consent, caller context, redaction/evidence, bounds, and
finish reason. Use fake providers/tools; ordinary evals need no provider key.

Run backend CI gates plus affected eval files. The full trajectory suite is
separate from the CI scorecard, which covers its registered golden scenarios:

```bash
uv run pytest tests/eval
uv run python -m tests.eval.report
```

Focused unit coverage includes context pruning, consent ownership, tool errors,
streaming/cancellation, and shared-engine consumer behavior. When changing
overhead or scheduling, run the offline microbenchmarks as well:

```bash
uv run pytest tests/benchmark/test_assistant_loop.py tests/benchmark/test_assistant_engine.py
```

Live provider benchmarks are separate acceptance evidence, not replacements for
deterministic tests. Report their prerequisites and costs when a task needs them.
