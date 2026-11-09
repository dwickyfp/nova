# Neva assistant harness: research

Date: 2026-11-09. Branch `feat/dataviz`. Input to
[the Neva proposal](../proposals/neva-assistant.md).

This records what the console assistant (Nove, to be renamed Neva) does today,
what Studio's harness and Intelligence UI add, what the ontology offers an
assistant, and what current practice says about assistant harness design.
Internal findings cite files at the commit above; external findings cite their
source. Items marked **unverified** come only from secondary write-ups.

## 1. Nove today

Paths are relative to `backend/app/modules/assistant/` and
`frontend/src/features/assistant/` unless shown otherwise.

### Turn

- One shared `AssistantLoop` (`service.py`). Studio composes the same loop
  (`agents/turns.py:70`, `agents/router.py:2488`, `agents/quality.py:417`,
  `agents/harness_experiments.py:422`).
- Page actions with exact phrases skip the model (`service.py:932-976`).
- A strict JSON planner picks intent (13 kinds), tools, up to 2 skills and a
  presentation (`planning.py:442`). For Nove a failed plan ends the turn
  (`service.py:1030-1047`); Studio has a fallback plan.
- Answer checks: evidence enforcement, numeric claim checks, verified-answer
  contract, follow-up suggestions (`service.py:1726-1938`,
  `answer_contract.py:1039`).
- Bounds for Nove (defaults): 8 model calls, 60 s, 2 successful runs per tool,
  4 SQL writes, 300 s approval wait, 24k-token context by default
  (`service.py:94-112`, `context.py:57-69`). The console builds the loop with
  defaults only (`router.py:76`).

### Capabilities that must survive the redesign

| Kind | Inventory |
|---|---|
| Tools, read | `load_skill`, `search_knowledge`, `catalog_search`, `validate_sql`, `inspect_query_error`, `verify_query_repair`, `inspect_agent_configuration`, `invoke_client_capability`, `data_to_chart`, `query_execute`, `compute_metrics`, `ml_execute`, `ai_search`, `semantic_view_query`, `feature_lookup`, `inspect_role_access` |
| Tools, write | `query_mutate`, `grant_role_access`, `provision_user`, `create_semantic_view`, `create_agent` (and role switch through `query_execute`) |
| Skills (14) | accountadmin-guardrail, ai-functions, copy-into, create-ml-model, create-table, create-task, create-user, debug-sql, native-ml, nova-streams, scope-boundary, sql-reference, stage-query, writing-style |
| Knowledge (16 docs) | product areas from SQL and storage to security, ML and monitoring, plus `nove-workflows` |
| Surfaces | `workspace.sql`, `intelligence.<tab>`, `roles.list`, `roles.detail`, `monitoring.query_history`, fallback `nova.global` (`surface-registry.ts:118`) |
| Context | app context up to 24 KB with page, entity, selection, editor, execution and 12 events (`app_context.py:203`); query attach with rewrite diff (`query-attach.ts`); threads in `CONFIG_ASSISTANT_*` |
| UI | panel, dock and resize; composer; activity trace; tool-call card with Approve/Deny, protected password field and file upload; code cards with Run, inline preview and "Fix with Nove"; approval-mode and model selectors; thread history |
| Safety | consent classes and fail-closed default (`tools/_boundary.py:211`), owner-checked decisions (`router.py:514-597`), injection scan and credential redaction (`guards.py`) |

### Gaps

1. One plan per turn, no re-planning; iterative analyst mode is Studio-only.
2. 8 steps and 60 s are too few for multi-step investigation.
3. No governed meaning: no `semantic_query` grounding, no ontology tools, no web search.
4. SQL drafts are checked for grammar only.
5. Rich output is lost: intelligent UI is off for the console
   (`service.py:174`), and table, chart, citation, `ui`, suggestions, guard and
   evidence events are parsed but not rendered (`use-assistant-transcript.ts:226-290`).
6. File attachments are accepted by the schema but never reach the loop.
7. The read-only grant lives in memory; no cross-session memory.
8. Five surfaces; most of the console falls back to `nova.global`.
9. Two copies of the persona (`skills.py:164`, `service.py:4420`).
10. Thread history has no search, rename or delete.

### The rename is safe for stored data

"Nove" appears in about 78 backend, 257 frontend (non-test), 124 test and 62
doc lines. No table, API path, cookie, cache key or event name uses it; they use
`assistant_*`. Lookup values to rename together: knowledge id `nove-workflows`,
code-card id prefixes `nove-code-`/`nove-card-`, password field
`nove-new-user-password`.

## 2. What Studio's harness adds

The engine is shared; the difference is how the router fills `LoopContext` and
builds the loop (`agents/router.py:2130-2510`):

- Per-agent registry, skills, `harness_policy` (standing rules, tool guidance,
  guards) and prompt from `agent_service.build_loop_inputs`
  (`agents/service.py:90-237`).
- Budget profiles fast/analyst/deep with 8/16/40 steps, `iterative=True` for
  analyst, a spend limit, history summarization, `GuardChain.from_policy`
  (`agents/service.py:52-74`, `router.py:2488-2500`).
- Memory scoped to owner and role, through `memory_prompt` (`memory.py:720-902`).
- Semantic-view binding drives grounding, analysis tools and prefetch
  (`service.py:397-400, 1090-1096, 1535`).
- `intelligent_ui=True` enables the OpenUI composer and deterministic autocompose
  (`service.py:1133-1136, 4223`).
- Run journal, model titling, quality observation, Missions.

A system agent that is never persisted already exists twice:
`dataviz_assistant_config()` (`agents/dataviz_assistant.py:178-212`) and the
skill author (`skill_author.py:5-23`). Neva can follow that precedent instead of
a second loop.

### Intelligence UI

- Backend: one `openui` program per answer, validated by the Node runtime
  (catalog `nova-openui-3`), numbers re-checked against verified evidence
  (`intelligent_ui.py:34-310`). Stream frames `ui`/`ui_delta`, `table`, `chart`
  (Vega-Lite + viz spec), `citation`, `suggestions`, `evidence_health`, `guard`,
  `plan` (`events.py:42-60`).
- Frontend: `turn-content.tsx`, `intelligent-ui/renderer.tsx`,
  `components.tsx`, `result-cards.tsx`, `result-table.tsx`,
  `answer-footer.tsx`, `thought-turn.tsx` (`ProcessRail`), reducer
  `studio-transcript.ts`.
- Coupling to extract: the UI action/state API is hard-wired to
  `/agents/{agentId}/threads/...` (`intelligent-ui/api.ts:5`); `ui_action`
  validation lives in the agents router; `turn-content.tsx` imports Dataviz
  buttons.

### Greeting

`studio/greeting.ts` (daypart from the client timezone, re-read each minute) and
`Greeting` in `studio-chat.tsx:1094` ("Good {daypart}, {name}", four suggested
questions). The console's `assistant-empty-state.tsx` has page suggestions and
recent threads; the two can merge.

## 3. Ontology for an assistant

From `docs/arch-18-business-ontology.md` and `backend/app/modules/ontology/`.

- Visibility follows the caller's single active role through the domain path
  (`ontology/access.py:46-64`); hidden equals missing (404).
- Stewardship: anyone who sees a domain may propose; a steward of every affected
  domain, not the submitter, reviews; release prepare, publish and rollback are
  `ACCOUNTADMIN` only (`stewardship.py:388-524`, `releases.py:78-198`).
- Bindings are pinned to the metric's claim fingerprint; stale bindings are
  never usable.
- Studio tools today: `ontology_resolve`, `ontology_explain` (read) and
  `knowledge_propose` (session_change, consent every time)
  (`agents/tools/ontology.py`). `ground()` fails closed with
  `ONTOLOGY_UNAVAILABLE` or `NO_AUTHORIZED_REPRESENTATION`
  (`grounding.py:103-189`).
- Gaps found while reading:
  - `scope=working` is documented as steward-only but `snapshot("working")`
    has no steward check (`service.py:161`); it is still filtered by domain
    visibility.
  - `get_concept` returns full revision history, including unreleased labels and
    who changed them (`service.py:308-322`).
  - Concept `classification` (RESTRICTED, HIGHLY_SENSITIVE) is displayed, not
    enforced.

An assistant must never review, apply, publish, roll back, administer domains,
run `learn` or evaluation endpoints, or claim a definition changed when only a
proposal was filed.

## 4. Harness design practice

| Recommendation | Source |
|---|---|
| Start with one capable agent loop with tools; add workflows only where steps are known and only when it measurably helps | [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents); [OpenAI, A practical guide to building agents](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf) |
| Loop is gather context → act → verify → repeat; prefer rule-based verification, LLM judge last | [Claude Agent SDK post](https://claude.com/blog/building-agents-with-the-claude-agent-sdk) |
| Write effort-scaling rules: simple questions need about 3–10 tool calls | [Anthropic, multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Subagents only for wide, parallel, read-only research; never parallel writes. Cognition argues against multi-agent by default | Anthropic (same); [Cognition, Don't build multi-agents](https://cognition.com/blog/dont-build-multi-agents) |
| Smallest high-signal context; preload stable essentials (role, page, capability map), fetch the rest just in time; prompts at the "right altitude" with a few canonical examples | [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) |
| Fewer, workflow-shaped, namespaced tools; concise responses by default; errors that teach the fix | [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) |
| Rate tools by risk and pause on high risk; for writes, preview → consent → execute, enforced by the harness | OpenAI guide (Tool safeguards); [MCP tool annotations](https://modelcontextprotocol.io/community/interest-groups/tool-annotations.md) |
| Compile generated SQL and repair in a loop; classify ambiguous or unanswerable questions and offer answerable alternatives | [Snowflake, Cortex Analyst behind the scenes](https://www.snowflake.com/en/blog/engineering/snowflake-cortex-analyst-behind-the-scenes/) |
| Label answers by trust: curated/verified sources are "Trusted", ad-hoc SQL is not | [Databricks, Trusted assets](https://docs.databricks.com/aws/en/genie/trusted-assets) |
| Route metric questions through governed definitions first; semantic layers raise accuracy sharply on covered questions (vendor benchmarks, small n) | [dbt 2026 rerun](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026?version=2); [Hex Context Studio](https://hex.tech/product/context-studio/) |
| Evaluate with 20–50 tasks from real failures, deterministic graders first, calibrated LLM judges, grade outcomes, keep capability and regression suites, use pass^k; trajectory checks only for invariants (no write without consent) | [Anthropic, Demystifying evals for AI agents](https://anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| Make clear what the system can do from the empty state | [Microsoft, Guidelines for Human-AI Interaction](https://www.microsoft.com/en-us/research/publication/guidelines-for-human-ai-interaction/) |
| Escalate chat into a full surface (notebook) | Hex Threads, **unverified** |

Disagreements kept open: agentic search vs vector retrieval (Agent SDK prefers
agentic search; Snowflake retrieves verified queries and values semantically),
and trajectory vs outcome grading (resolved above by limiting trajectory checks
to invariants).

No usable primary source was found for streaming activity traces,
interruptibility or rich answer blocks; those parts of the proposal are design
judgment, measured in acceptance, not cited practice.
