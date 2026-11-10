# Phase 6 checkpoint: acceptance

Date: 2026-11-10. Code: `feat/dataviz` at 8402b26 for the measured runs (the
fixes listed under "After the measured run" came later and were checked on dev
tasks only). Same provider, runner, golden set and lay-user rubric as
[phase 0](phase-0-checkpoint.md), [phase 2](phase-2-checkpoint.md) and
[phase 2b](phase-2b-checkpoint.md), on the golden set's **test half** (20 tasks
× 3 runs = 60 turns), plus the new [page-context set](page-context-set.json)
(12 tasks × 3 runs = 36 turns).

Judging: three independent LLM judges (one per task group) applied the
rubric, read earlier judgments for the standard, and checked every figure, SQL
statement and page action against the dev engine with read-only SQL. Earlier
phases were judged by hand; scores are comparable, but the new judges list
jargon more strictly, so the technical-word counts are not.

Evidence: `runs/phase-6-neva.jsonl`, `runs/phase-6-page-context.jsonl`,
`judgments/phase-6-neva.jsonl`, `judgments/phase-6-page-context.jsonl`,
`runs/phase-6-after-grounding.jsonl` (dev-task check of the later fixes).
95% intervals: percentile bootstrap over turns (4,000 resamples, seed 7).

## Results: golden set, test half

| Measure | Phase 0 (Nove) | Phase 2b | **Phase 6** |
|---|---|---|---|
| Solved | 0.21 (0.13–0.29) | 0.77 (0.67–0.87) | **0.82 (0.73–0.91)** |
| Understandable | 0.42 (0.30–0.53) | 0.97 (0.92–1.00) | **0.98 (0.95–1.00)** |
| Correct | 0.55 (0.43–0.68) | 0.93 (0.87–0.98) | **0.95 (0.88–1.00)** |
| Asked before changing | 0.98 | 1.00 | **1.00** |
| pass^3 | 0.05 | 0.65 | **0.75** |
| Wrong and confident | 0 | 4 | **3** (all DA04) |
| Answers with no text | 21 | 0 | **1** (a timeout, now fixed) |
| Automatic technical-leak hits | 7 | 0 | **0** |
| Latency p50 / p95 | 20.3 / 46.1 s | 33.1 / 76.1 s | **25.4 / 61.7 s** |

Solved by category (phase 0 → 6): explain 0.42 → 1.00, find 0.33 → 1.00,
fix SQL 0.11 → 1.00, access 0.17 → 1.00, maintain 0.22 → 0.67, data 0.00 →
0.38. **No category is worse than phase 0.** Against phase 2b, access (0.67 →
1.00) and fix SQL (0.89 → 1.00) improved; maintain moved 0.72 → 0.67 inside the
interval.

## Results: page context

| Measure | Phase 6 |
|---|---|
| Solved | **0.92 (0.85–0.97)** |
| Used the page (did not ask for it again) | **1.00** |
| Correct | 0.97 (0.92–1.00) |
| Wrong and confident | 0 |
| Asked before changing | 1.00 (SQL only proposed, filters only set) |
| Latency p50 / p95 | 32.9 / 52.0 s |

By kind: SQL on the page (explain, optimize, edit, write, fix) 0.83, page
admin (filters, tasks) 0.96, Database Explorer 1.00, glossary 1.00. Every SQL
Neva put in the editor plans on the engine; the "faster" query returns exactly
the same rows as the original.

## Decisions per change

| Change | Decision | Evidence |
|---|---|---|
| All system instructions sent as one message | Keep: the gateway dropped the page context, plans and loop corrections | live before/after; `test_provider_request_options.py` |
| SQL proposed into the editor as a reviewed diff, explained after | Keep | page set PW01–PW05 |
| Only SQL that passed its check reaches the editor | Keep | `test_neva_lookups_and_views.py`, `test_neva_editor_proposal.py` |
| `validate_sql` plans one SELECT as the caller | Keep; Ranger acceptance passes | `test_neva_sql_planning_ranger.py` |
| Written tables read back | Keep | `test_query_mutate_readback.py` |
| Source label, Trusted only for governed sources | Keep | `test_neva_answer_source.py`, `answer-source.test.tsx` |
| Ontology reader boundaries (4.1–4.3) | Keep; real-metadata acceptance by five roles | `test_ontology_reader_boundaries.py` |
| Neva glossary tools and grounding order | Keep | PO01/PO02 1.00 |
| Filter values matched to the page's spelling, page summaries sent | Keep: before, three of the filters would have failed in the real page | `surface-registry.test.ts`, page set PU01/PR01/PM01 |
| "Several datasets" prompt rule | **Reverted**: on dev tasks it sent Neva through every catalog, named internal catalogs and picked a demo dataset | `runs/phase-6-after-grounding.jsonl` (first attempt) |
| Period rule, no permission asked for read-only work | Keep | dev tasks: no turn asks permission to read |
| Model calls bounded by the turn's time budget; a stalled turn ends plainly | Keep: DA02/DA03/DA05 had run past 300 s with no reply | eval `test_a_slow_model_call_ends_with_the_turn_s_time_budget`, `test_a_stalled_turn_ends_plainly…` |

## After the measured run (dev tasks)

With the reverts and the time bounds in place, the seven data tasks finished
in 39–108 s (none past the budget) and DA04 answered 6,758 orders, the retail
figure, instead of the benchmark view's 5,000.

## What still fails

1. **Governed queries ask back too often (data 0.38).** The Semantic View tool
   asks which measure is meant when the person's words do not match a metric
   exactly (DA02, DA03, DA05, DA07), even when one reading is the usual one.
   The fix belongs in the governed query planner, not in Neva's prompt.
   *Fixed in [phase 6b](phase-6b-checkpoint.md): data tasks now solve 0.88.*
2. **Health questions (MO03).** Neva has no tool that reads system health; it
   points to the right pages instead.
3. **Optimizing SQL is shallow.** PW02 keeps results identical but only tidies
   the query; it does not read the plan to find real savings.
4. **Jargon in SQL explanations** ("LEFT JOIN", "Semantic View", "metadata").
5. **Tracker item 5.6** (knowledge search measured on its own question set) remains open.

## Security review

An independent review of the phase 4–6 diff found four issues, all fixed with
tests: table functions such as `FILES()` could have been planned without
consent; page text could close its prompt tag; HIGHLY_SENSITIVE definitions
reached the Dataviz context; unreleased concept names could leak through
proposals and business events. A second review found four bugs and four
clean-code issues, all fixed. Ranger acceptance (`test_ontology_evidence_ranger`,
`test_neva_sql_planning_ranger`) passes on the patched FE; no technical-leak
hits in 96 live turns.
