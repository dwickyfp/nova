# Machine learning for an automatic business ontology

Researched 7 November 2026 (literature and web). Figures marked "≈" come from the researcher's knowledge of the papers and were not re-checked against the source in this session. Unmarked figures were checked against their source.

## Summary

| # | Area | What it adds to Nova | Reported accuracy | Labels needed | On-premise / compute | Recommendation |
|---|---|---|---|---|---|---|
| 1 | Semantic column types (Sherlock, Sato, DoDuo, LLM) | Field types (email, country, currency), entity hints, features for joins | DoDuo ≈0.93 weighted F1 on VizNet; ArcheType 85.97 weighted F1 on SOTAB; large drop out of domain | Pretrained, or none for LLM zero-shot | Sherlock on CPU; DoDuo inference on CPU; LLM on site | **Now:** profilers, regexes, value-embedding features, LLM zero-shot over a fixed label set. **Later:** fine-tuned DoDuo |
| 2 | Join / FK / IND discovery (statistical + ML ranking) | The relationship graph; the biggest win | Auto-BI F1 >0.9 on 100K real BI models; hybrid statistics + LLM precision 78–100% on clean schemas | None for statistics; a small gold set for the ranker | CPU; SQL sketches in StarRocks | **Now, top priority** |
| 3 | Entity resolution (Magellan, Ditto, ZeroER, Splink, LLM) | Confirms two datasets share instances (the same customer in CRM and ERP) | Ditto up to state of the art; zero-shot GPT-4 beat Ditto on 3 of 5 benchmarks | Ditto: hundreds to thousands of pairs; ZeroER and Splink: none | Splink and ZeroER on CPU; Ditto trains on GPU | **Later** |
| 4 | KG embeddings / GNN link prediction | Predicts missing ontology edges | FB15k-237 MRR ≈0.33–0.36 on a large, dense graph | Many triples per relation | Cheap, but data-starved | **Not worth it** for graphs of hundreds to thousands of nodes |
| 5 | Ontology learning with LLMs, synonyms, metrics from query logs | Concept names, hierarchy, aliases, metric candidates | LLMs4OL: GPT-4 ≈78% F1 on UMLS taxonomy; GPT-3.5 67.8 F1 on GeoNames; fine-tuning helps a lot | None to few | Local or configured LLM | **Now:** query-log mining and embedding synonyms; the LLM proposes, statistics verify |
| 6 | Evidence fusion and calibration (Snorkel, Bayes, Platt/isotonic, ECE, active learning) | One calibrated confidence per claim, and routing of human review | Standard methods; quality depends on the gold set | A small dev set (≈200–500 per claim type) | CPU, trivial | **Now, the backbone** |
| 7 | Text-to-SQL impact of semantic layers and KGs | Measures what the ontology is worth | 16% → 54% (data.world); +17–23 points from a semantic document (2026) | Benchmark questions | Existing eval harness | **Now, as the downstream acceptance metric** |

## 1. Semantic column type annotation

**Sherlock** (Hulsebos et al., KDD 2019). 1,588 per-column features (character distributions, word and paragraph embeddings, statistics) fed to a small neural network over 78 DBpedia types. ≈0.89 support-weighted F1 on VizNet. Runs on CPU. ([code](https://github.com/mitmedialab/sherlock-project))

**Sato** (Zhang et al., VLDB 2020). Adds table context through a topic model and a CRF. ≈+14% macro F1 over Sherlock.

**TURL** (Deng et al., VLDB 2021) and **DoDuo** (Suhara et al., SIGMOD 2022). Transformers over the whole table. DoDuo reports ≈0.93 weighted / ≈0.84 macro F1 on VizNet. **RECA** (Sun et al., VLDB 2023) adds related tables and gains slightly over DoDuo. Training needs a GPU; inference works on CPU.

**LLM-based.**
- **ArcheType** (Feuer et al., PVLDB 17) samples context, prompts, and remaps labels using open models. It reports 85.97 weighted F1 on SOTAB, state of the art for zero-shot. ([code](https://github.com/penfever/ArcheType))
- **Chorus** (Kayali et al., VLDB 2024) and **Korini & Bizer** (2023) show competitive few-shot CTA with LLMs.

**Caveat.**
- These models break under domain shift. GitTables (Hulsebos et al., SIGMOD 2023) found that models trained on web tables perform badly on enterprise-like CSVs.
- Enterprise semantic types such as SKU or store code are not in the DBpedia or Schema.org label sets.

**For Nova.** Column types are features:
- for join candidates (two columns that are both postal codes);
- for entity typing;
- for blocking.

They do not define business concepts.

**Recommendation: now.**
- Deterministic profilers: regexes and validators, value statistics, cardinality.
- Character n-gram or MinHash sketches.
- LLM zero-shot over Nova's own type vocabulary from samples and names, where policy allows sending samples.
- Do not train Sherlock or DoDuo until steward decisions provide labels.

## 2. Join, foreign-key and inclusion-dependency discovery (highest value)

**Exact and approximate INDs.** SPIDER, BINDER, and Faida (Kruse et al., ICDE 2017), which finds approximate INDs from hashes and HyperLogLog. All are in Metanome. ([Metanome](https://github.com/HPI-Information-Systems/metanome))

**Containment search at scale.**
- MinHash LSH and **LSH Ensemble** (Zhu et al., VLDB 2016).
- **Lazo** (Fernandez et al., ICDE 2019).
- **JOSIE**, exact top-k overlap (Zhu et al., SIGMOD 2019).
- The Python library `datasketch` implements MinHash, LSH Ensemble and HLL on CPU.

**Aurum** (Fernandez et al., ICDE 2018). Builds an enterprise knowledge graph from content- and schema-similarity edges. The closest earlier architecture to what Nova is doing.

**ML ranking of FK candidates.**
- Rostin et al. (WebDB 2009): random forest over IND features.
- Zhang et al. (VLDB 2010): randomness test for multi-column FKs.
- HoPF (Jiang & Naumann 2020): holistic PK/FK selection.
- **Auto-BI** (Lin, He, Chaudhuri, PVLDB 16, 2023): a local join classifier, then a global k-Min-Cost-Arborescence over the schema graph. F1 >0.9 on more than 100K real BI models and on the TPC benchmarks, in under a second for about 100 tables. ([paper](https://arxiv.org/abs/2306.12515), [PVLDB](https://www.vldb.org/pvldb/vol16/p2578-he.pdf)) No paper titled "Auto-Relate" was found.

**Recent hybrids.**
- "Scalable Join Inference for Large Context Graphs" (2026). Prunes statistically, has an LLM adjudicate PK/IND candidates, then refines from query history. Precision 78–100% on TPC-H, TPC-DS, BIRD and production data; worse on poorly normalized schemas. ([paper](https://arxiv.org/abs/2603.04176))
- **Nexus** (2026). Infers joins from metadata only, by low-rank matrix completion with an LLM in an EM loop, for settings where values cannot be read. The abstract gives no figures. ([paper](https://www.alphaxiv.org/abs/2602.08186))

**Cardinality.** Compute it directly in StarRocks:
- NDV from an approximate distinct count (HLL);
- null rate;
- containment |A∩B|/|A| from sketches.

1:1, N:1 and M:N follow from how unique each side is.

**Recommendation: now.** Build a feature vector per candidate pair:
- containment in both directions;
- key uniqueness and NDV ratio;
- name and embedding similarity;
- type compatibility;
- range overlap;
- JOIN frequency in the query log;
- relationships already declared in Semantic Views.

Rank pairs with a logistic or gradient-boosted model trained on:
- the TPC-DS, BIRD and Spider schemas;
- existing Semantic View relationships;
- steward decisions.

Then select a global arborescence, as Auto-BI does. Profile under the security context of the caller or of an authorized service principal, and keep only sketches so raw values never leave StarRocks.

## 3. Entity resolution

- **Magellan** (Konda et al., VLDB 2016): blocking plus a classical classifier; `py_entitymatching`.
- **Ditto** (Li et al., VLDB 2021): fine-tuned RoBERTa, up to +29% F1 over the earlier state of the art. Needs labelled pairs. ([code](https://github.com/megagonlabs/ditto))
- **ZeroER** (Wu et al., SIGMOD 2020): an unsupervised generative model close to supervised baselines.
- **LLMs** (Peeters, Steiner & Bizer, EDBT 2025): zero-shot GPT-4 beat fine-tuned Ditto on 3 of 5 benchmarks. Open models (Llama-2-70B variants, Mixtral) are viable when data cannot leave the site, and fine-tuning helps a great deal. ([paper](https://arxiv.org/abs/2310.11244))
- **Splink** (Fellegi–Sunter with unsupervised EM): mature and fast on CPU. ([code](https://github.com/moj-analytical-services/splink))

**For the ontology.** Instance-level resolution is evidence that two datasets describe the same concept, for example when customers overlap by 80%. It is also the basis for any later master-data work. The schema-level graph does not need it.

**Recommendation: later.** Splink or ZeroER first; the LLM only adjudicates ambiguous pairs.

## 4. Knowledge-graph completion and link prediction

Methods: TransE (Bordes 2013), ComplEx (Trouillon 2016), RotatE (Sun 2019), R-GCN (Schlichtkrull 2018), and LLM link prediction (KG-BERT, KICGPT). Library: PyKEEN.

Results: MRR ≈0.33–0.36 on FB15k-237 (14K entities, 237 relations, about 270K triples).

**Why not for Nova.**
- An enterprise ontology has hundreds to thousands of nodes and a few relation types (HAS_ROLE, DERIVES, JOINS, EQUIVALENT), so there are too few triples per relation.
- The predictions would duplicate structural evidence Nova already computes directly, such as containment.
- Stewards could not be given an explanation.

**Recommendation: not worth it.** Revisit only for a multi-tenant or very large catalog. Text embeddings of names and descriptions plus structural features do the job better.

## 5. Ontology learning, synonyms and metric mining

**LLMs4OL** (Babaei Giglou et al., ISWC 2023; 2024 challenge). ([repository](https://github.com/HamedBabaei/LLMs4OL), [2024 proceedings](https://www.tib-op.org/ojs/index.php/ocp/article/download/2484/2313))
- Zero-shot LLMs do moderately on term typing and taxonomy: GPT-4 ≈78% F1 on UMLS, GPT-3.5 67.8 F1 on GeoNames.
- Domain fine-tuning was the main lever in 2024; rules plus BERT reached 0.99 F1 on WordNet term typing.
- Non-taxonomic relation extraction stayed weak.

**OntoGPT/SPIRES** (Caufield et al., Bioinformatics 2024). Schema-guided extraction from text. Only relevant if Nova ingests documentation.

**Synonym mining.** Candidate sources:
- SQL aliases (`AS revenue`);
- BI-layer renames and dashboard titles;
- column co-occurrence in queries;
- embedding similarity (sentence-transformers, bge or e5, on CPU);
- LLM proposals that pass a validator (Nova's current approach).

Treat each source as a separate noisy signal rather than trusting any one of them.

**Metric discovery from query logs.**
- Parse the query history (e.g. with sqlglot; Nova already has the StarRocks grammar), normalize aggregate expressions into AST fingerprints, cluster them, and rank by frequency, distinct users and dashboards.
- GROUP BY and JOIN usage give the fact and dimension roles.
- **AskData** profiles the database and mines query logs; reported at 81.95% on the BIRD test set (venue not verified).
- Commercial practice: Momenta ("semantic layer from your SQL history") and MotherDuck ("AI writes the semantic layer", [blog](https://motherduck.com/blog/AI-writes-the-semantic-layer.md)).
- The academic evidence here is thinner than for joins.

**Recommendation: now.**
- Use JOIN frequency from the query log as join evidence.
- Cluster aggregate fingerprints to find metric candidates. This extends Nova's formula-fingerprint equivalence.
- Use embeddings plus alias co-occurrence for synonyms.
- Use the LLM only for names, descriptions and hierarchy proposals, always checked by statistics or the validator.

## 6. Fusing evidence into a calibrated confidence

**Weak supervision.** The Snorkel label model (Ratner et al., VLDB 2017) learns the accuracy and correlation of each labelling function without ground truth. Each detector (containment, name rule, query-log join, LLM vote) is a labelling function. ([code](https://github.com/snorkel-team/snorkel))

**Discriminative calibration.** Once about 200–500 gold labels exist per claim type:
- use logistic regression on the features, read directly as calibrated log-odds (Platt);
- switch to isotonic regression above about 1,000 labels.

**Measuring calibration.** ECE and Brier score, with a reliability diagram (Guo et al., ICML 2017).

**LLM confidence.** An LLM's self-reported confidence is overconfident (Xiong et al., ICLR 2024). Use it only as a feature, or as a vote count across sampled prompts, never as the final probability.

**Active learning.**
- Uncertainty sampling, prioritized by impact (the edge's centrality or query-log usage), so stewards only see items with p of about 0.3–0.8.
- Auto-accept above a threshold chosen to hit a target precision (for example ≥0.97 on held-out data).
- Auto-reject below a low threshold.
- Every steward decision becomes a label for retraining.

**Recommendation: now.** This is what makes "mostly without humans" auditable.

## 7. Semantic layers and text-to-SQL

**data.world** (Sequeda, Allemang, Jacob 2023). GPT-4 zero-shot: 16% on raw SQL against 54% over a KG/ontology, on an insurance schema. One vendor, one domain, one model. A follow-up adds ontology-based query checks (≈72%). ([paper](https://arxiv.org/abs/2311.07509))

**Paired benchmark 2026** (arXiv 2604.25149). Claude Opus 4.7, Sonnet 4.6 and GPT-5.4 on Contoso data. A 4 KB semantic document raised accuracy from 45.5–50.5% to 67.7–68.7% (+17–23 points, p<0.01). The choice of model within a tier did not matter.

**Schema linking.**
- **CHESS** (Talaei et al. 2024): LSH retrieval of values plus schema pruning; BIRD ≈66–73%.
- **Spider 2.0** (Lei et al., ICLR 2025): enterprise schemas; early agents solved ≈17–21% of tasks, and the leaderboard has risen since. ([repository](https://github.com/xlang-ai/Spider2))
- BIRD claims now reach about 80% (Gemini-SQL2, self-reported), against about 93% for humans. ([news](https://aiweekly.co/alerts/googles-gemini-sql2-tops-bird-text-to-sql-at-8004))
- An annotation-error audit found 52.8% errors in BIRD Mini-Dev and 62.8% in Spider 2.0-Snow, so small leaderboard gaps are noise. ([audit](https://experts.illinois.edu/en/publications/pervasive-annotation-errors-break-text-to-sql-benchmarks-and-lead/))
- Value-aware schema linking (CHESS-style LSH over column values) is the part that depends on the ontology's synonym and value indexes.

**Recommendation: now, as the outcome metric.** Run Nova's evaluation questions with and without the ontology, and report the change in execution accuracy and in the rate of confidently wrong answers.

## Recommended pipeline

1. **Profile** (CPU, inside StarRocks). Per column:
   - NDV via HLL, null rate, min/max, pattern classes;
   - a MinHash sketch;
   - value-embedding centroids for categorical columns.

   Store only sketches and statistics in `NOVA_SYSTEM`; raw values never move.
2. **Type.** Deterministic validators plus LLM zero-shot over Nova's own type vocabulary (ArcheType style). Types are features.
3. **Join graph** (the core).
   1. Candidates from LSH Ensemble or JOSIE containment.
   2. Features: containment, uniqueness, type and name similarity, query-log JOIN counts, existing Semantic View relationships.
   3. A logistic or GBM ranker, then a global arborescence in the Auto-BI style.
   4. Cardinality from NDV ratios.
4. **Meaning.**
   - Entities from datasets and keys.
   - Roles from FK targets plus column types.
   - DERIVES and equivalence from parsed formulas, plus clusters of query-log aggregate fingerprints.
   - Synonyms from aliases, embeddings and validated LLM proposals.
   - Names and hierarchy from the LLM, as proposals only.
5. **Instance overlap** (later). Splink or ZeroER on key columns for "same concept" links.
6. **Fuse.**
   - Each detector is a labelling function for the Snorkel label model while labels are scarce.
   - Move to a Platt- or isotonic-calibrated classifier per claim type once steward labels accumulate.
   - Each claim stores its probability and its list of evidence.
7. **Route.** Auto-publish above a threshold set for target precision, send the uncertain band to an active-learning queue, and auto-reject below a low threshold. Steward decisions come back as labels.

### What to measure

- Per claim type: precision, recall and F1 against a gold set built from:
  - TPC-DS and TPC-H;
  - BIRD and Spider schemas;
  - the SerbaData/retail benchmark views;
  - steward-confirmed edges.
- Calibration: ECE, Brier score and a reliability diagram.
- Precision of auto-accepted claims, against the target.
- Steward minutes per 100 claims, and the share decided automatically.
- Join graph F1, per edge and per arborescence.
- Downstream: the change in text-to-SQL execution accuracy, and in the rate of confidently wrong answers, with and without the ontology.
- Drift: precision regression when re-run after a schema change.

Three ML pieces to start with: statistical join discovery with a learned ranker, query-log mining, and calibrated weak supervision. LLMs name things and adjudicate ambiguous cases; they are not a source of confidence. Skip KG embeddings and GNNs.
