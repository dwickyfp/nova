# Supernova ontology research

Research that backs the [Supernova Ontology Framework](../../proposals/supernova-ontology-framework.md). Each file keeps its sources and states what was and was not verified.

| File | Question it answers |
|---|---|
| [01-market-landscape.md](01-market-landscape.md) | What Snowflake, Databricks, Palantir, Fabric IQ, dbt, AtScale and others ship to ground agents, and what accuracy they claim |
| [02-ml-methods.md](02-ml-methods.md) | Which machine learning and statistical methods can build and link an ontology automatically, with evidence, cost and a recommendation |
| [03-measurement.md](03-measurement.md) | How ontology quality, agent answers, calibration and LLM judges are measured, and which metrics and protocol to use |
| [04-ambiguity-and-coverage.md](04-ambiguity-and-coverage.md) | Phase E: detecting vague questions and periods without data; lexical rule measured and rejected, candidate-aware planner and data-window check chosen |
| [05-join-evidence.md](05-join-evidence.md) | Phases B–D: join discovery (Auto-BI rules, KMV filter then exact verify), evidence-class calibration measured against logistic regression, fixed decision thresholds |

## Conclusions that drive the design

1. **Semantic context is worth +17 to +40 points of accuracy,** consistently across studies. Examples:
   - 45–50% → 68% in the independent study;
   - 20% → 92.5% at AtScale;
   - 16.7% → 54.2% → 72.6% at data.world;
   - 51% → 90%+ at Snowflake.

   Only the arXiv 2604.25149 study is independent; the rest are vendor-run.
2. **The context mattered more than the model.** Frontier models were statistically indistinguishable within each condition (arXiv 2604.25149). Investment belongs in the ontology, not in swapping models.
3. **No vendor closes the full loop.**
   - Snowflake proposes from usage but evaluates weakly.
   - Databricks evaluates strongly but does not learn from feedback.
   - All of the strongest are cloud-only.
4. **Join discovery is the biggest machine-learning win.**
   - Statistics plus a learned ranker plus global selection (Auto-BI) reach F1 >0.9 on real BI models.
   - It runs on CPU, mostly as SQL in StarRocks.
5. **Confidence must be calibrated, not chosen.**
   - Weak supervision while labels are few.
   - Platt or isotonic once 200–500 labels exist per claim type.
   - Measured with ECE, Brier and reliability diagrams.
   - An LLM's self-reported confidence is overconfident, so it is only a feature.
6. **Embedding KGs and GNNs are not worth it** for enterprise graphs of hundreds to thousands of nodes. Too few triples per relation, no explanation for a steward, and they duplicate structural evidence.
7. **Accuracy is reported as an estimate with an interval.**
   - Stratified cluster sampling (Gao et al.) cuts labelling cost by up to 60–80%.
   - Shown as "96% (93–98%, n=240)".
8. **Execution accuracy alone is not enough.**
   - FLEX: κ 0.87 against 0.62 for EX.
   - Measure reliability, rewarding correct abstention (TrustSQL), and clarification on ambiguous questions (AMBROSIA).
9. **A judge must be measured against people.**
   - The best judges reach κ ≈0.8; humans with each other reach 0.96.
   - Use a panel of three judges from different model families (PoLL), with rubrics and order swapping.
10. **Benchmarks themselves are wrong surprisingly often:** 52.8% annotation errors in BIRD Mini-Dev. Gold sets are audited too.

## Gaps in this research

- **Not verified:**
  - Palantir branching and versioning (secondary sources only).
  - Whether Snowflake suggestions are GA.
  - Cube.
  - AskData's venue.
  - Figures marked ≈ in 02.
- **Gao et al., Dellschaft & Staab and OQuaRE** were read from abstracts or excerpts only.
- **No open benchmark yet tests an ontology** (rather than text-to-SQL) on enterprise data. Nova has to build its own gold set.
