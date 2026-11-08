# Semantic and ontology layers for AI agents: what vendors ship

Researched 7 November 2026 (web). Each claim links to its source. Vendor accuracy figures are self-reported unless marked otherwise.

## Snowflake

**Semantic Views.** Schema-level objects that hold tables, relationships, facts, dimensions, metrics and synonyms. Snowflake now recommends them over YAML semantic models.

**Verified Query Repository (VQR).** "Gold" question-and-SQL pairs that Cortex Analyst reuses for similar questions. ([docs](https://docs.snowflake.com/en/user-guide/views-semantic/verified-query-repository))

**Suggestions** (preview since 9 October 2025):
- Read the role's recent SQL history, and the Analyst and Agent questions that no verified query matched.
- Propose new verified queries (up to 10 per batch), metrics, filters and computed facts.
- Nothing is applied automatically: a person accepts, edits or dismisses each suggestion.
- "Optimization" runs each verified query up to 4 times.

([suggestions](https://docs.snowflake.com/en/user-guide/views-semantic/verified-query-suggestions), [optimization](https://docs.snowflake.com/user-guide/snowflake-cortex/cortex-analyst/analyst-optimization))

**Semantic View Autopilot** (GA 3 February 2026):
- Generates semantic views from query history, usage patterns, Tableau files (TWB/TWBX/TDS) and uploaded trusted SQL.
- Infers relationship types from actual column cardinality.
- When historical queries disagree, clusters them and proposes the majority definition. Example: more than 200 queries that define "active user" the same way.
- Keeps monitoring usage and suggests updates; stewards certify the suggestions.

([blog](https://www.snowflake.com/en/blog/semantic-view-autopilot/))

**Accuracy.**
- Claim: 90%+ on an internal set of 150 business questions, against 51% for a single GPT-4o prompt on the same set, roughly 2×. ([engineering blog, 29 August 2024](https://www.snowflake.com/en/engineering-blog/cortex-analyst-text-to-sql-accuracy-bi/))
- The set is internal; there are no Spider or BIRD results.
- The Autopilot blog gives no accuracy figures.

**Evaluation.**
- Cortex Analyst evaluations use the verified queries as ground truth.
- The queries under test are removed from a temporary copy of the semantic view, so they cannot leak into generation.
- Results are compared by execution; the reports show Accuracy, Regressions and latency. ([docs](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations))
- I found no evaluation console as complete as Genie benchmarks.

**Open Semantic Interchange (OSI).**
- Announced 23 September 2025 by Snowflake, Salesforce, dbt Labs, BlackRock and RelationalAI, with 15+ partners.
- The v1 spec is Apache-2.0 and covers metrics, dimensions, datasets and relationships.
- It is a format only, with no runtime.

([announcement](https://www.businesswire.com/news/home/20250923902448/en/), [spec](https://www.getdbt.com/blog/the-osi-spec-updates))

**Gaps:** cloud-only; curation is still review-centric; accuracy is not publicly benchmarked; suggestions are still preview.

## Databricks

**Unity Catalog metric views.**
- YAML measures and dimensions, plus "agent metadata": display names, formats, and up to 10 synonyms per field.
- Synonyms are imported into Genie automatically.
- Relationships between metric views are Beta; materialization is Experimental.

([docs](https://docs.databricks.com/aws/en/business-semantics/metric-views))

**Genie knowledge store.**
- Holds join relationships and reusable SQL expressions (measures, filters, dimensions), capped at 200 snippets per space.
- "Prompt matching" is on by default. It samples column values and maps a user's words to stored values ("FL" → "Florida").
- Tables with row filters or column masks are excluded from prompt matching.
- The docs say Genie "does not automatically learn" from feedback.

([docs](https://docs.databricks.com/en/genie/knowledge-store))

**Benchmarks** are the most mature evaluation feature among the vendors researched.
- Up to 500 questions per space; the docs recommend 2–4 phrasings per question.
- Chat mode scores by comparing result sets:
  - row order is ignored, numbers must match to 4 significant digits, and extra columns are allowed;
  - empty results and errors score Bad;
  - verdicts are Good, Bad or Manual review.
- Agent mode uses an LLM judge guided by an evaluation note.
- A "Generate SQL" button drafts the reference answer; "Update ground truth" saves a model answer as the new reference.
- After a run, Genie Code analyzes the results and suggests instruction changes.
- Benchmarks are never put into Genie's context.

([docs](https://docs.databricks.com/en/genie/benchmarks))

**Automatic metadata.**
- AI-generated comments on catalogs, schemas, tables, columns and functions; a person reviews and saves them. ([docs](https://docs.databricks.com/aws/en/comments/ai-comments))
- Data quality monitoring (Public Preview, blog 4 February 2026): schema-level anomaly detection that learns seasonality, plus profiling, with issues triaged through lineage. ([blog](https://www.databricks.com/blog/data-quality-monitoring-scale-agentic-ai))

**Accuracy.**
- "Pushing the frontier of data agents with Genie" (8 May 2026): on an internal benchmark of real data-analysis tasks, Genie went from 32% (a leading coding agent) to over 90%.
- Knowledge search improved table discovery by up to 40%.
- Techniques named: parallel thinking, multi-LLM sub-agents, GEPA prompt optimization.

([blog](https://www.databricks.com/blog/pushing-frontier-data-agents-genie))

A second-hand source reports "Genie One" at 84.5% against 52.4% on 28 questions. Not verified. ([source](https://blog.pebblous.ai/blog/databricks-genie-one-governed-data/en/))

**Gaps:** context is per Genie space; no automatic learning from feedback; curation is manual per space; cloud-only.

## Palantir Foundry / AIP Ontology

- Object types, link types (cardinality, backed by a foreign key or a join dataset), action types (the governed write path with side effects), and functions (TypeScript or Python).
- The Ontology SDK (Python, Java, TypeScript) and AIP Logic and agents operate on the same objects.
- Object-level security comes from the platform, and ontology changes can be branched.

([overview](https://palantir.com/docs/foundry/platform-overview/overview/))

**What makes it mature:** semantics, writeback and actions, security, and application building are one system.

There are no published text-to-SQL accuracy figures. The ontology is modelled by hand, usually with forward-deployed engineers. Branching and versioning details come from secondary blogs and are not verified in official docs.

## Microsoft Fabric IQ and Power BI

**Fabric IQ** was announced at Ignite in November 2025; the Ontology item is still preview (Learn page updated September 2026).
- Covers entity types, properties, relationships, rules in natural language, and metrics that carry DAX measures over from the source model.
- Can be generated from Power BI semantic models or imported from RDF/OWL.
- An ontology agent drafts definitions and data bindings, using a "proposal-first" review model.
- Can produce a graph model, consumed by data agents and operations agents (anomaly detection, governed actions).

([overview](https://learn.microsoft.com/en-us/fabric/iq/overview))

**Power BI "Prep data for AI"** (preview):
- AI data schema: which tables and columns the AI can see.
- AI instructions.
- Verified answers: a visual plus trigger phrases.
- "Approved for Copilot" marking.

([docs](https://learn.microsoft.com/power-bi/create-reports/copilot-prepare-data-ai))

No accuracy figures. Gaps: preview, cloud-only.

## Others

**dbt Semantic Layer / MetricFlow.**
- Metrics in YAML; out-of-model questions are answered "cannot answer".
- Benchmark (7 April 2026, open repository dbt-labs/dbt-llm-sl-bench): 11 questions on ACME Insurance, 20 runs per model.
- Results:

| Condition | Sonnet 4.6 | GPT-5.3-Codex |
|---|---|---|
| Text-to-SQL on the modelled project | 90.0% | 84.1% |
| Through the semantic layer | 98.2% | 100% |

- Text-to-SQL on the unmodelled project: about 64.5%.
- Out-of-scope questions score 0% by design.

([blog](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026))

**AtScale.**
- TPC-DS, 40 questions, Gemini 1.5 Pro: 20% from the schema only, against 92.5% with the semantic layer (2024).
- High-complexity questions: 0% against 70%.
- 2025 claim: 0–30% without the layer, 100% with it.
- Public TPC-DS leaderboard; all runs by the vendor.

([article](https://www.bigdatawire.com/2024/08/15/atscale-claims-text-to-sql-breakthrough-with-semantic-layer))

**data.world (Sequeda et al.).**
- GPT-4 zero-shot on an insurance schema (OMG P&C): 16.7% on SQL against 54.2% over a knowledge graph (2023).
- Adding ontology-based query checks with LLM repair (2024): 72.55%. About 8% of answers were "I don't know"; the error rate was 20%.

([2023](https://arxiv.org/abs/2311.07509), [2024](https://arxiv.org/abs/2405.11706))

**Independent study (arXiv 2604.25149, 28 April 2026).**
- 100 questions on Contoso retail data in ClickHouse.
- Schema only: 45.5–50.5%. Schema plus a 4 KB semantic document: 67.7–68.7%, a gain of 17–23 points (p<0.01).
- Within each condition, Opus 4.7, Sonnet 4.6 and GPT-5.4 were statistically indistinguishable: the context mattered, not the model.

**Looker / LookML + Gemini.** Internal testing reportedly showed the semantic layer cuts data errors in natural-language queries "by as much as two thirds", with no published method. ([blog](https://cloud.google.com/blog/products/business-intelligence/how-lookers-semantic-layer-enhances-gen-ai-trustworthiness))

**Salesforce Tableau Semantics** (GA 2025). Semantic models in which AI suggests joins and calculated fields and helps deduplicate and maintain metrics. ([blog](https://www.tableau.com/blog/what-is-tableau-semantics))

**Timbr / Stardog.** Ontology and knowledge-graph semantic layers exposed to agents over MCP (Stardog with Bedrock AgentCore). No accuracy figures. ([blog](https://aws.amazon.com/blogs/machine-learning/build-a-semantic-layer-for-agentic-ai-on-aws-with-stardog-and-amazon-bedrock-agentcore/))

**Cube.** The searches found no Cube-specific material, so it is not assessed.

## Capability matrix

Legend:

| Code | Meaning |
|---|---|
| Y | Documented and shipped |
| P | Preview or beta |
| S | Suggested to a person for review |
| M | Manual only |
| – | Not found |

| Capability | Snowflake | Databricks | Palantir | Fabric IQ / Power BI | dbt | AtScale | Looker | Tableau Semantics | Stardog / Timbr |
|---|---|---|---|---|---|---|---|---|---|
| Automatic discovery of entities and joins | S | M | M | S | M | M | M | S | M |
| Metric definitions | Y | Y | Y | Y | Y | Y | Y | Y | Y |
| Metrics proposed from usage | S | – | – | S | – | – | – | S | – |
| Synonyms and value matching | Y / S | Y / Y | M | Y | M | M | M | – | Y |
| Verified queries | Y + S | Y | – | Y | – | – | – | – | – |
| Evaluation and benchmarks | M | Y | – | – | Open benchmark | Public leaderboard | – | – | – |
| Learning from usage | S | – | – | – | – | – | – | – | – |
| Drift and quality monitoring | S | P | – | P | – | – | – | P | – |
| Governance and review | Y | Y | Y | Y | Git PR | Y | Git | Y | Y |
| Lineage | Y | Y | Y | Y | Y | Partial | Partial | Y | Partial |
| Actions and writeback | – | – | Y | P | – | – | – | Agentforce | – |
| Open interchange | OSI | – | – | RDF/OWL | OSI | Universal layer | – | OSI | RDF/OWL |
| On-premise | No | No | Yes (rare) | No | Yes (open-source core) | Yes | No | No | Yes |

## Capabilities that define a mature agent-grounding ontology in 2026

1. **Typed concepts with executable bindings.** Every metric compiles to one canonical query.
2. **Join and relationship inference from data statistics,** offered as proposals with a confidence.
3. **Usage mining.** Cluster conflicting definitions, propose the majority definition, and detect missing metrics and filters.
4. **A verified-query library** that grows automatically from unmatched agent questions, accepted by a steward.
5. **Lexical grounding:** synonyms plus value dictionaries and entity matching, with masked or filtered columns excluded.
6. **A built-in evaluation harness:**
   - many phrasings per question;
   - result comparison with tolerances;
   - an LLM judge for multi-step answers;
   - an accuracy score per release;
   - a regression gate on schema changes.
7. **Closed-loop optimization.** Evaluation failures become proposed instruction or definition changes.
8. **Drift detection,** of the data (anomalies, profiles) and of meaning (usage diverging from definitions, schema changes breaking bindings).
9. **Proposal-first stewardship** with certification states, versioned releases and branches.
10. **Security propagation.** Row and column policies apply to the semantics, the samples and value matching.
11. **Calibrated abstention.** Answer "cannot answer" outside the modelled scope.
12. **Open interchange and agent access** (OSI, RDF, MCP/API), plus actions and writeback for operational agents.

## Published accuracy figures

| Source | What it measures | Without semantics | With semantics |
|---|---|---|---|
| Snowflake 2024 (internal, 150 questions) | SQL correctness | GPT-4o 51% | 90%+ |
| Databricks 2026-05 (internal tasks) | End-to-end analysis | Coding agent 32% | Genie >90%; table search up to +40% |
| dbt 2026-04 (11 questions × 20 runs, open) | Answer correctness | 84.1–90.0% (64.5% unmodelled) | 98.2–100% in scope; abstains out of scope |
| AtScale 2024 (TPC-DS, 40 questions) | Correctness, 3 retries | 20% | 92.5% |
| data.world 2023/2024 (insurance) | Correctness | 16.7% | 54.2% (KG); 72.55% (ontology checks and repair) |
| arXiv 2604.25149 (100 questions, independent) | First-shot correctness | 45.5–50.5% | 67.7–68.7% |
| Google Looker | NL query "data errors" | – | Up to two thirds fewer (no method published) |

Except for the arXiv study, every figure is self-reported by the vendor on a small or internal question set.

## Gaps an on-premise competitor can fill

- Snowflake, Databricks, Fabric, Looker and Tableau are cloud-only.
- No vendor combines automatic proposals from statistics and usage with a built-in benchmark loop and drift checks. Snowflake suggests but evaluates weakly; Databricks evaluates but does not learn.
- Accuracy claims cannot be reproduced. A transparent per-release accuracy scorecard would stand out.
- Semantics that follow policy, with Ranger row filters and masks also applying to samples and value matching, are only partly addressed elsewhere. Databricks simply excludes those tables.
- Actions and writeback exist only in Palantir, and in preview in Fabric.
- OSI import and export gives buyers portability.

**Not verified:** Palantir branching and versioning details (secondary sources only); whether Snowflake suggestions are GA; Cube.
