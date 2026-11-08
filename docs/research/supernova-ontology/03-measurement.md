# Measuring a metrics-first enterprise ontology

Researched 7 November 2026 (literature and web). Several papers were read from their abstracts and excerpts only, not the full text: Gao et al., Dellschaft & Staab, and OQuaRE. Targets marked **(lit)** come from the cited source. Targets marked **(proposed)** are recommendations, because the literature sets none.

## 1. Findings

### Ontology and knowledge-graph quality

- **OntoQA** defines structural metrics:
  - relationship richness RR = |P|/(|SC|+|P|);
  - inheritance richness IR = |SC|/|C|;
  - attribute richness AR = |AT|/|C|.

  These describe structure, not correctness, and one survey notes OntoQA has no empirical validation. ([source](https://scholarcommons.sc.edu/aii_fac_pub/294))
- **OQuaRE** adapts ISO/IEC 25000 (SQuaRE) as a hierarchy of characteristics, sub-characteristics and metrics. It suits a health roll-up tree. ([paper](https://research.manchester.ac.uk/en/publications/oquare-a-square-based-approach-for-evaluating-the-quality-of-onto/), [code](https://github.com/tecnomod-um/oquare))
- **Zaveri et al. (2016)** survey linked-data quality: 18 dimensions and 69 metrics, including accuracy, completeness, consistency, timeliness and conciseness. This is the standard taxonomy for naming metrics. ([survey](https://semantic-web-journal.net/content/quality-assessment-methodologies-linked-data-survey))
- **Dellschaft & Staab (ISWC 2006)** evaluate a learned ontology against a gold standard: lexical precision/recall for terms, and separate taxonomic precision/recall for the hierarchy. Score the two layers separately. ([doi](https://doi.org/10.1007/11926078_17))
- **Gao et al., "Efficient KG Accuracy Evaluation" (PVLDB 2019)** estimate accuracy from a small annotated sample, with a confidence interval.
  - They use cluster (entity-level) sampling, because labelling one entity at a time is cheaper, with two-stage weighted and stratified variants.
  - Changing KGs are evaluated incrementally with a reservoir.
  - They report up to 60% lower annotation cost on a static KG and 80% on an evolving one. ([paper](https://arxiv.org/abs/1907.09657))

### Text-to-SQL and semantic-layer evaluation

- **Metrics.**
  - Execution accuracy (EX) compares result sets.
  - Exact match compares SQL text and is too strict.
  - Test-suite accuracy (Zhong et al. 2020) runs queries against several database instances to remove EX false positives.
  - BIRD adds a Valid Efficiency Score: gold runtime ÷ predicted runtime, and 0 when the answer is wrong.
- **Spider 2.0** has 632 enterprise workflow tasks. An o1-preview agent solved 21.3%, against 91.2% on Spider 1.0; enterprise accuracy is far below academic accuracy. ([paper](https://arxiv.org/abs/2411.07763))
- **FLEX (NAACL 2025)** is an LLM judge for SQL correctness. Its Cohen's κ with experts is 0.87, against 0.62 for plain EX, because EX passes coincidental matches and fails correct answers to ambiguous questions. ([paper](https://arxiv.org/abs/2409.19014))
- **TrustSQL** scores reliability:
  - correct SQL and correct abstention are rewarded;
  - wrong SQL, and SQL written for an unanswerable question, are penalized;
  - no method tested consistently beat the baseline of abstaining on everything. ([paper](https://arxiv.org/abs/2403.15879))
- **AMBROSIA** covers ambiguous questions (scope, attachment, vagueness). Models rarely notice the ambiguity, which is why the clarification rate is worth measuring. ([review](https://liner.com/review/ambrosia-a-benchmark-for-parsing-ambiguous-questions-into-database-queries))
- **Snowflake Cortex Analyst evaluations** use verified queries as ground truth.
  - The queries under test are removed from a temporary copy of the semantic view.
  - Results are compared by execution, and the reports show Accuracy, Regressions (questions that previously passed and now fail) and latency. ([docs](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations))
- **Databricks Genie benchmarks** pair each question with reference SQL.
  - **Good:** the data matches, even with extra columns, a different sort order, or values equal to 4 significant digits.
  - **Bad:** an empty result or an error.
  - **Manual review:** a question with no reference.
  - Use 2–4 phrasings per question, and keep benchmark SQL out of the agent's context. ([docs](https://docs.databricks.com/aws/en/genie/benchmarks))

### LLM as a judge

- **Zheng et al. (MT-Bench 2023).** GPT-4 agrees with humans more than 80% of the time, about the same as humans with each other.
  - Biases found: position, verbosity, and self-enhancement.
  - Mitigations: swap the order of answers, and grade against a reference for maths and code. ([paper](https://arxiv.org/abs/2306.05685))
- **Thakur et al. (2024).** Percent agreement hides disagreement, so report Cohen's κ. The best judges reach κ ≈0.79–0.84, against 0.96 between humans, and judges tend to be lenient. ([paper](https://arxiv.org/abs/2406.12624))
- **G-Eval.** The judge writes its own evaluation steps, fills in a form, and scores as a probability-weighted sum over score tokens. Needs logprobs. ([paper](https://arxiv.org/abs/2303.16634))
- **Prometheus 2.** An open evaluator model, suitable on-premise. Its rubric describes each score level, it writes feedback before scoring, and it supports pointwise and pairwise grading. ([paper](https://arxiv.org/abs/2405.01535))
- **PoLL (Verga et al. 2024).** A panel of three small judges from different model families correlates better with humans than a single GPT-4 judge, shows less self-preference, and costs more than 7× less. ([paper](https://arxiv.org/abs/2404.18796))

### Calibration

- **ECE** bins predictions by confidence and takes the weighted gap between accuracy and confidence in each bin (Guo et al. 2017; [paper](https://arxiv.org/abs/1706.04599)).
- **Brier score** is the mean of (p−y)².
- A **reliability diagram** plots accuracy against confidence, bin by bin.
- **Tian et al. (EMNLP 2023).** Confidence that RLHF models state in their text is often better calibrated than token probabilities, often by about 50% relative ECE. Chain-of-thought does not consistently help. ([paper](https://arxiv.org/abs/2305.14975))

### Catalogue and observability practice

- **Monte Carlo** has five pillars: freshness, volume, distribution, schema and lineage. ([blog](https://www.montecarlodata.com/blog-introducing-the-5-pillars-of-data-observability))
- **Atlan** gives each asset a weighted metadata-completeness score (points per filled field), glossary coverage reports, and DAU/WAU/MAU adoption telemetry. ([docs](https://docs.atlan.com/product/capabilities/discovery/references/metadata-completeness), [reporting](https://docs.atlan.com/product/capabilities/reporting))
- No official DataHub or Collibra source was found for an equivalent score; only third-party comparisons, which are low confidence.

## 2. Proposed metric set

| Group | Metric | Definition | Source | Target | Judge's role |
|---|---|---|---|---|---|
| Coverage | Column grounding | Semantic View dimensions and measures mapped to a published concept ÷ all of them | Bindings, Semantic Views | ≥90% of queried columns **(proposed)**, weighted by usage | Proposes mappings; they don't count until accepted |
| Coverage | Question coverage | Gold or production questions whose concepts all exist ÷ all questions | Gold set, Studio traces | ≥85% **(proposed)** | Labels the missing concept |
| Coverage | Definition completeness | Atlan-style weighted score (description, owner, formula, synonyms, steward) | Ontology metadata | 100% for certified concepts **(proposed)** | Judges whether the description is informative, not just present |
| Accuracy | Fact accuracy | p̂ from a stratified cluster sample (Gao), with a 95% Wilson CI | Labels from the judge plus a human subsample | p̂ ≥0.95, CI half-width ≤0.03 **(proposed)** | First-pass labeller; humans correct a subsample |
| Accuracy | Extraction P/R | Lexical and relational precision/recall against the gold set, scored separately; F1 | Gold ontology | P ≥0.9 before automatic publishing **(proposed)** | Judges whether near-equal labels mean the same |
| Accuracy | Formula consistency | Metric definitions whose SQL results match the stated meaning on probe queries ÷ all | Probe query execution | 100% for certified metrics | Compares the natural-language meaning with the SQL and its sample output |
| Calibration | ECE, Brier | ECE = Σ_b (n_b/N)\|acc_b − conf_b\|, 10 bins; Brier = mean (p−y)² | Suggestion confidence and the steward's decision | ECE ≤0.05 **(proposed)**; publish the reliability diagram | Confidence stated in text (Tian), then temperature-scaled |
| Grounding | Execution accuracy | The agent's SQL result matches gold under Genie tolerance | Gold Q/SQL run as the same user and role | A trend; no absolute threshold transfers from Spider/BIRD | Judge (FLEX style) arbitrates EX mismatches and coincidental passes |
| Grounding | Reliability score | Average of: +1 correct answer, +1 correct abstention or clarification, −c wrong answer, −c answered an unanswerable question (TrustSQL) | Gold set including unanswerable and ambiguous questions | Beats the abstain-on-everything baseline **(lit)** | Classifies each reply as answer, abstention or clarification |
| Grounding | Clarification P/R | Clarifications on ambiguous items ÷ ambiguous items; unneeded clarifications ÷ clear items | Ambiguous items (AMBROSIA style) | Recall ≥0.8, unneeded ≤0.1 **(proposed)** | Judges whether the clarification addresses the real ambiguity |
| Grounding | Claim support | Numbers and facts in the answer traceable to the result ÷ all claims | Answer text plus result | ≥0.98 **(proposed)** | Judge checks each claim against the result rows, with a reference |
| Drift | Staleness | Bindings whose source changed after validation ÷ all bindings; age of the last estimate | DDL hashes, release log | 0 certified items stale for more than 7 days **(proposed)** | Re-validates; tells meaning changes from cosmetic ones |
| Drift | Accuracy drift | Δp̂ between releases, with a two-proportion test | Successive estimates | Block when the CI excludes the previous p̂ on the low side | – |
| Usage | Grounded answers | Studio answers that use at least one ontology concept ÷ all answers; distinct users and roles per week | Studio traces, audit log | A trend | – |
| Usage | Steward throughput | Median decision time; backlog | Review queue | A trend | – |
| Automation | Auto-decided | Items decided without a human ÷ all items, **always shown with** the sampled accuracy of that stratum | Decision log, audit sample | Raise only while that stratum has p̂ ≥0.95 **(proposed)** | Calibrated confidence decides above a threshold; humans audit a random sample |

How to show trust: a value and its uncertainty, for example "Accuracy 96% (93–98%, n=240, verified 3 days ago)". Show a reliability diagram rather than a bare confidence number, and let every metric drill down to its sampled evidence rows.

## 3. Evaluation protocol

| Step | Procedure |
|---|---|
| Gold set | 150–300 questions per domain with steward-verified SQL, 2–4 phrasings per intent (Genie), about 15% unanswerable and about 10% ambiguous (TrustSQL, AMBROSIA). Stratify by concept, metric and difficulty. Gold SQL never enters the agent's context. The gold set is versioned with releases. |
| Ontology sample | Stratified cluster sampling by concept (Gao TWCS), sized for ±3–5% at 95% confidence (worst case about 385 independent items for ±5%; clustering cuts labelling cost). Re-sample incrementally on each release. |
| Judge rubric | Prometheus style: a description per score level and binary sub-criteria (right entity, right metric, right filter or time grain, claims supported). The judge receives the question, the gold SQL, both results and both SQL texts. It writes its reasoning first, then the verdict. |
| Bias control | For pairwise grading, run both orders and accept only consistent verdicts. Use a judge from a different model family than the agent. Penalize unsupported length. Release gates use a three-judge PoLL panel by majority; nightly runs use one cheap judge. |
| Judge–human agreement | Two people double-label 100–200 items per release. Report Cohen's κ between the humans and between the judge and the humans, not percent agreement. Require judge κ ≥0.8 **(proposed; the best judges reach 0.79–0.84 in Thakur, and FLEX reaches 0.87)**. Below that, revise the rubric or change the judge. |
| Cost | Skip the judge when EX passes on the test suite and the result is non-trivial. Call it only for EX failures, ties, or answers with text claims. Cache verdicts by (question, SQL hash, data version). |
| Release gate | Block a release when any of these holds:<br>(a) a gold item that passed before now fails, with no approved waiver;<br>(b) execution accuracy falls by more than its 95% CI;<br>(c) the reliability score is below the previous release or the abstain baseline;<br>(d) ECE rises above 0.05;<br>(e) judge κ falls below 0.8. |

Other sources: [Wilson CI](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval), [AmbiQT / interactive disambiguation](https://arxiv.org/abs/2507.06467).
