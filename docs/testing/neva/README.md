# Neva build tracker

The single place that says which part of the Neva work is done. Design:
[proposal](../../proposals/neva-assistant.md). Research:
[harness research](../../research/neva-assistant-harness-research.md).

Status keys: `[ ]` not started, `[~]` in progress, `[x]` done and verified,
`[!]` blocked (reason written next to it). A box is ticked only after its check
ran on the final code and the evidence is linked.

Each phase ends with a checkpoint file in this folder
(`phase-N-checkpoint.md`) holding the measurements, n and confidence intervals,
judge verdicts and the commands that produced them.

## Standing rules (apply to every phase)

These come from the user and do not expire.

1. **No technical leaks.** Neva's answers, tool descriptions, tool results it
   repeats, errors and suggestions never contain route paths, the word
   "endpoint", "API", HTTP status codes, internal module, class or file names,
   `NOVA_SYSTEM` table names, stack traces or provider/storage addresses. Users
   see plain words ("the SQL editor", "your saved file", "Nova could not reach
   the AI service").
2. **Tools call services, not Nova's own HTTP routes.** Every tool runs
   in-process as the caller, through the owning service. (Audit 2026-11-09: no
   assistant or Studio tool calls Nova's HTTP routes today; keep it that way
   with a test.)
3. **Security first.** Caller credentials and the single active role only;
   consent enforced by the harness; fail closed; redacted audit for every
   governed action and refusal; no secret in context, answers, logs or traces.
4. **Lay-user testing.** Every live test is run from the point of view of a
   non-technical person who maintains Nova and wants help ("my report is slow",
   "why can't Ana see sales", "what does net revenue mean here"). Judged on:
   did it solve the problem, was it understandable, was it correct, did it leak
   anything technical, did it ask before changing things.
5. **Nothing is lost.** The parity suite (phase 0) stays green after every
   change.
6. **Measured, not claimed.** Quality claims need numbers with n and a
   confidence interval on the live provider; tuning uses a dev/test split and
   reports the test half only.
7. **UI work applies `/unslop`** and is judged in light, dark and phone width.

## Phase 0: baseline

Goal: know exactly what Nove does today and how well, before changing it.

- [ ] 0.1 Technical-leak audit of prompts, skills, knowledge docs, tool
      descriptions, tool error texts and provider error texts; list every leak.
- [ ] 0.2 Leak check as code: a scrubber test that fails on route paths,
      "endpoint", status codes, internal names, `NOVA_SYSTEM` names or stack
      traces in tool results and final answers (deterministic, offline).
- [ ] 0.3 "No HTTP self-calls" test: assistant and agent tools import no HTTP
      client aimed at Nova's own routes.
- [ ] 0.4 Parity suite: one scripted eval trajectory per tool (21), per skill
      (14) and per surface (6), plus frontend tests for every panel affordance
      (approval card, password field, file upload, code card Run/preview/fix,
      query attach and rewrite diff, model and approval-mode selectors, dock,
      resize, thread history).
- [ ] 0.5 Lay-user golden set: 40 tasks in English and Indonesian across
      explain, find, fix SQL, build with consent, data questions, access and
      governance, monitoring and maintenance; each with the expected outcome
      and a grading rule. Dev/test split fixed before any tuning.
- [ ] 0.6 Live Nove baseline on the configured provider: success rate,
      pass^3, technical-leak count, wrong-but-confident count, latency
      p50/p95, tokens; judged; recorded in `phase-0-checkpoint.md`.

Acceptance: 0.2–0.4 green on today's code; baseline recorded with n and CIs.

## Phase 1: rename to Neva

- [ ] 1.1 Persona (both copies), planner text, tool descriptions, skills,
      knowledge docs renamed; technical wording from 0.1 rewritten in plain words.
- [ ] 1.2 Frontend strings and identifiers (`useNoveSurface` → `useNevaSurface`,
      `askNove`, `NoveMark`, files) renamed.
- [ ] 1.3 Lookup values renamed with their readers: `nove-workflows`,
      `nove-code-`/`nove-card-`, `nove-new-user-password`.
- [ ] 1.4 Docs: `arch-09` renamed and updated.
- [ ] 1.5 Test: no "Nove" in user-visible strings or prompts.

Acceptance: parity suite and leak check green; stored data untouched
(`assistant_*` names unchanged).

## Phase 2: harness

- [ ] 2.1 Extract the loop builder from the agents router; Studio and Dataviz
      call it unchanged (their evals unchanged).
- [ ] 2.2 `neva_system_config()`: never persisted; console tools and skills
      (audience filter inverted); standing rules; guards from policy.
- [ ] 2.3 Budget scaling: fast for simple intents, analyst (16 steps, iterative
      re-planning, summarization) for complex ones; limits measured, then fixed.
- [ ] 2.4 Re-plan instead of failing the turn on a bad plan.
- [ ] 2.5 File attachments reach the loop.
- [ ] 2.6 "Always allow read-only" persisted per thread in Redis.
- [ ] 2.7 Cross-session memory scoped to user, role and security-context
      version (decision 2026-11-09).
- [ ] 2.8 Trajectories: re-planning, budget choice, memory isolation across
      roles, consent unchanged.

Acceptance: parity, leak and Studio/Dataviz evals green; simple-question
latency within the bound agreed from the phase 0 baseline.

## Phase 3: Intelligence UI in the panel

- [ ] 3.1 Extract `TurnContent`, `IntelligentUi`, result table/chart, citations,
      answer footer from Studio coupling (Dataviz and agent-API props optional).
- [ ] 3.2 Console threads get UI action/state handling in the shared module.
- [ ] 3.3 Panel renders `ui` blocks, tables, charts, citations, evidence
      health, follow-up suggestions and the `ProcessRail` activity trace.
- [ ] 3.4 Greeting card shared with Studio: daypart greeting, page-aware
      suggestions, recent threads.
- [ ] 3.5 Thread history search, rename, delete.
- [ ] 3.6 Neva mark; `/unslop` pass; screenshots light, dark, phone judged.
- [ ] 3.7 Every Nove affordance still present (tests from 0.4).

## Phase 4: ontology

- [ ] 4.1 Fix: `scope=working` readable by stewards only.
- [ ] 4.2 Fix: unreleased revision history hidden from non-stewards.
- [ ] 4.3 Fix: concept `classification` limits what reaches a model prompt.
- [ ] 4.4 Ranger and patched-FE acceptance for 4.1–4.3.
- [ ] 4.5 Tools: resolve, explain, search, trace metric, related, release info,
      my proposals (read); propose, withdraw (write, consent every time).
- [ ] 4.6 Grounding order: ontology → semantic view → verified query → ad-hoc
      SQL; ask when a term is ambiguous.
- [ ] 4.7 Trajectories: grounding order, consent on propose, "proposed" never
      "changed", hidden domain refused like a missing one.

## Phase 5: smarter answers

- [ ] 5.1 Drafted SQL verified as the caller (objects resolve, EXPLAIN passes)
      and repaired in the loop before it is offered.
- [ ] 5.2 Written objects read back after a change.
- [ ] 5.3 Source label in every answer; "Trusted" only for governed sources.
- [ ] 5.4 Surfaces added for Database Explorer, Tasks, Monitoring pages, Access
      Control, Business Ontology, Agents, Users.
- [ ] 5.5 Ambiguous or unanswerable questions get a clarifying question or
      answerable alternatives instead of a guess.

## Phase 6: acceptance

- [ ] 6.1 Live run of the golden set (test half) on the configured provider.
- [ ] 6.2 Lay-user judge rubric: solved, understandable, correct, cited, no
      technical leak, asked before changing; pass^3.
- [ ] 6.3 Comparison with the phase 0 baseline with CIs; no task class worse.
- [ ] 6.4 Security review of the full diff; Ranger acceptance; leak check green.
- [ ] 6.5 `phase-6-checkpoint.md` with the decision per change.

## Log

| Date | Phase | What changed | Evidence |
|---|---|---|---|
| 2026-11-09 | – | Research, proposal and decisions recorded | `docs/research/neva-assistant-harness-research.md`, `docs/proposals/neva-assistant.md` |
| 2026-11-09 | 0.1 | Audit started: no tool calls Nova's HTTP routes; "endpoint" wording found in 7 skill/knowledge docs and provider error texts | this file |
