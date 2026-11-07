# Supernova Phase A: Measurement foundation

Status: implemented 7 November 2026; measured results in the [Phase A checkpoint](../testing/supernova/phase-a-checkpoint.md). This is phase A of the [Supernova Ontology Framework](supernova-ontology-framework.md). The basis is [research 03](../research/supernova-ontology/03-measurement.md).

## Goal

The ontology gets measured accuracy, not claimed accuracy:

- answer accuracy with the ontology versus without it;
- accuracy of its claims;
- calibration of its confidence;
- agreement between judges.

Every number has an n, an interval, and a date. The Business Ontology page opens on those metrics.

## Decisions, and why

| Decision | Reason |
|---|---|
| **The gold set is not stored as Semantic View verified queries** | Studio uses verified queries at runtime (`_verified_match`), so the answer key would leak into what is being tested. Databricks bars this too. They are also capped at 20 per view. |
| **Ontology evaluation is owned by the ontology module, not agent quality** | Agent quality (`agents/quality.py`) evaluates an agent version through the full AssistantLoop with assertions. It has no gold result, `agent_id` is required, and it cannot test an unpublished release. Ontology evaluation evaluates a release. Storage follows the ontology's own append-only pattern: `CONFIG_ONTOLOGY_EVAL_CASES`, `CONFIG_ONTOLOGY_EVAL_RUNS`, `CONFIG_ONTOLOGY_EVAL_RESULTS`. |
| **The tested path is the one Studio uses**, minus the chat loop | `ground(question, release_id)` → `planning_catalog` + `generate_plan(business_concepts=…)` → `missing_metrics` + `validate_plan` → compiler → execution as the caller. One model call per question, so a full run is affordable on every release. Agent behaviour is still measured by agent quality. |
| **Ablation "without ontology" lives in evaluation code** | The same pipeline without grounding and without `business_concepts`. No branch or flag in the production path. |
| **Gold by construction** | Each answerable case is a `SemanticPlan` built deterministically from the published IR: metric × breakdown × absolute period × filter × top-N. The gold SQL is compiled from that plan and re-executed on every run as the same user and role. Only the plan and the row count are stored; result rows and measure values never are. A filtered question does keep the category value it asks about (for example a city), because the value is part of the question. Evaluation details are admin-only. |
| **Questions are phrased by a model, then checked by two judges** | The model writes business Indonesian and English without the metric's identifier. The automated judge checks that the question asks exactly for that plan. Claude reviews as a second judge, from a different model family. A case is kept only if both say yes. |
| **Absolute periods** (`2026-10`, `2026-Q3`) | Relative periods ("bulan ini") change their answer over time. The lab data runs from 1 May to 4 November 2026. |
| **Unanswerable (~15%) and ambiguous (~10%) cases** | Measures abstention and clarification (TrustSQL, AMBROSIA). Ambiguous cases come from names shared between domains and from roles that do not fit, such as a sales metric "per driver". |
| **Answers are judged by execution, not by the judge** | Result matching follows Genie: order-free unless there is an `order_by`, numbers to 4 significant digits, extra columns allowed, empty or error means wrong. The judge only classifies replies on unanswerable and ambiguous cases, and audits claims. This limits self-preference, because the planner and the judge are the same model in dev. |
| **One automated judge for now, in a configurable panel** | Only one model is active in dev (`cx/gpt-6-luna`). The panel takes every registered model. Claude is the second judge at every checkpoint, so κ is measured between two model families. |
| **The caller drives runs in resumable steps** | Internal scheduler tasks are capped at 120 s. Each `step` evaluates a small batch in the caller's live session and stores the results, so a crash resumes. No new scheduler. |
| **Statistics move into `app/common/statistics.py`** | Wilson, Brier and the paired bootstrap already exist in `tests/benchmark/business_intelligence/statistics.py`. Phase A adds ECE, Cohen's κ, the stratified estimate and McNemar's test. The test copy then imports from `app`. |

## Metrics produced

| Metric | Formula | Unit of analysis |
|---|---|---|
| Answer accuracy | Correct ÷ answerable, Wilson 95% interval, with and without the ontology | Answerable gold case |
| Ontology effect | Difference in accuracy plus McNemar's test on the same cases (paired) | Case pair |
| Reliability | TrustSQL: +1 correct answer or abstention/clarification where it should be; −1 wrong answer or an answer where there should be none; 0 otherwise, divided by n | All cases |
| Wrong answer rate | Answers that are wrong ÷ all cases | All cases |
| Clarification recall | Ambiguous cases clarified ÷ ambiguous cases | Ambiguous cases |
| Claim accuracy | Stratified sample per claim type (entity, dimension, MEASURES, RELATED_TO with or without role, DERIVES, EQUIVALENT_TO, official alias), estimate weighted by stratum size with an interval | Derived claim and official alias |
| Calibration | ECE (10 bins) and Brier of the stored confidence against the judged verdict | Claim |
| Judge agreement | Cohen's κ between the automated judge and Claude | Case fidelity and claim |
| Coverage | Derived metrics already official; gold questions whose concepts resolve | Live |

## Acceptance criteria

1. **SerbaData gold set v1:** at least 150 cases across all three lab views, about 15% unanswerable and about 10% ambiguous. Every answerable plan compiles and executes. Fidelity is confirmed by both judges.
2. **Second domain:** a gold set for `nova_business_360` is generated by the same generator, with no domain-specific code.
3. **Runs with and without the ontology** on the same release and the same gold set. Results per case are stored. Accuracy, the paired effect, reliability, wrong answers and clarification are reported with intervals.
4. **Claim audit:** at least 150 claims, judged by both judges. Claim accuracy is reported with an interval, along with the ECE of the current confidence.
5. **κ between the two judges is reported.** The target is ≥ 0.8. If it is lower, the rubric is revised before the numbers are used for decisions.
6. **Overview tab** shows only stored measurements: n, interval, date and delta. Where nothing has been measured it says "belum diukur". An Evaluation tab lists runs and results per case.
7. **Checks:** backend unit tests with coverage, the eval scorecard, mypy, ruff, engine tests, and frontend lint, build and coverage.
8. **The checkpoint is recorded** in `docs/testing/supernova/phase-a-checkpoint.md`, with the numbers, the judge verdicts and their limits.

## Out of scope for Phase A

- The column profiler, query-history mining, fused confidence and automatic decisions (phases B–D).
- Release gating on evaluation results. This phase measures and stores; the gate turns on once κ is stable.
- Human labels. κ against people is still open until a steward labels a sample.
