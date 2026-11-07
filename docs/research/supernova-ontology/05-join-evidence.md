# Join evidence and its calibration (Phases B–D)

Recorded on 2026-11-08. This note records the methods we chose, the alternatives we measured against them, and the reasons for each choice. The measured numbers are in the [B/C/D checkpoint](../../testing/supernova/phase-bcd-checkpoint.md).

## Methods

- **Auto-BI** (Li et al., SIGMOD 2023) treats join discovery as a classifier over column pairs. It uses inclusion, key uniqueness, names and value types, then makes a global choice so that each foreign-key column points at no more than one key. On real BI models it reports an F1 above 0.9. Nova keeps the per-column single-target constraint and the evidence features. Its rule-based classifier comes from errors measured on our two lab databases (listed in the checkpoint).
- **KMV sketches** (Beyer et al., SIGMOD 2007): we keep the bottom-k hashes of each column's distinct values. These sketches let us estimate Jaccard similarity and inclusion without moving any values.
  - We measured that when a small column sits inside a large one, the estimate is noisy: 0.77 against a true 0.91 at k = 256.
  - So sketches only filter candidate pairs at an inclusion of 0.3 or more. Exact `COUNT(DISTINCT)` queries, run as the caller, then decide. This is the filter-then-verify pattern used by LSH Ensemble (Zhu et al., VLDB 2016) and JOSIE (Zhu et al., SIGMOD 2019).
- **HLL distinct counts** (`approx_count_distinct`) are off by a few percent at small sizes. For example, a 1,000-row customer key read as 986 distinct values. A probable key (HLL uniqueness of 0.95 or more) is therefore confirmed with an exact count before it can serve as a join target.
- **Histogram binning** (Zadrozny and Elkan, ICML 2001) calibrates a score by using the measured precision of its bin. Our bins are evidence classes, and we take the Jeffreys posterior mean so that sparse classes stay near 0.5 until labels arrive.
- **Platt scaling and logistic regression** (Platt 1999; Niculescu-Mizil and Caruana, ICML 2005) were the plan in the framework proposal. We measured them against binning and rejected them for now (see below).

## Decisions measured, not assumed

| Question | Measured answer | Decision |
| --- | --- | --- |
| Do the derived layer's constant confidences need a model? | ECE 0.027 on 141 judged claims. Learned per-stratum precision does worse (0.044). | Keep the constants. |
| Logistic regression or evidence classes for join probability? | Leave-one-database-out: classes reach AUC 0.994 and ECE 0.020. Logistic regression reaches AUC 0.946 and ECE 0.035, and its NDV and size features do not transfer between databases. | Use evidence classes. |
| Should thresholds be tuned per database or fixed at the targets? | Cross-fitted tuning picked τ = 4×10⁻⁵ from a perfectly ranked database and accepted 375 wrong pairs elsewhere. With fixed thresholds of 0.97 and 0.01, accepted pairs were 13/13 right and rejected pairs 0/506 wrong. | Use calibrated probabilities with fixed thresholds. |
| Is the reverse direction of a declared join a positive? | No. Labelling it positive put 7 false positives in the "not chosen" class. | Only the declared direction is labelled right. |

## Security

- Profiles are computed with the caller's own session and active role, so Ranger row filters and masks shape the statistics.
- Sketches stay in memory only.
- A join run's results, and the run itself, are visible only to the same principal in the same role. Even table and column names can be more than another administrator may see.

## Open

- A schema the team did not design (TPC-H/TPC-DS shaped) is needed to show the rules generalise. Both lab schemas follow clean `*_id` conventions.
- Query-history mining (join pairs and aliases in `USAGE_QUERY_STATS`) is a second detector. It becomes necessary when names are not conventional.
