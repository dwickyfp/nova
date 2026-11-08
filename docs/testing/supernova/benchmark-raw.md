# Supernova ontology benchmark

## Answers (no view hint)

| Gold set | Mode | Run | Answerable correct | Ambiguous asked back | Unanswerable refused | Reliability |
|---|---|---|---|---|---|---|
| serbadata | with_ontology | `evn_49693c20…` | 120/123 = 97.6% (93.1–99.2%) | 7/14 = 50.0% (26.8–73.2%) | 19/20 = 95.0% (76.4–99.1%) | 0.885 |
| serbadata | without_ontology | `evn_695a435c…` | 122/123 = 99.2% (95.5–99.9%) | 6/14 = 42.9% (21.4–67.4%) | 19/20 = 95.0% (76.4–99.1%) | 0.879 |
| serbadata_business | with_ontology | `evn_7e868e63…` | 133/134 = 99.3% (95.9–99.9%) | 8/14 = 57.1% (32.6–78.6%) | 22/24 = 91.7% (74.2–97.7%) | 0.901 |
| serbadata_business | without_ontology | `evn_d6e1ff67…` | 132/134 = 98.5% (94.7–99.6%) | 7/14 = 50.0% (26.8–73.2%) | 23/24 = 95.8% (79.8–99.3%) | 0.901 |

**The ontology's effect (paired, answerable):**

- serbadata: without 122/123 = 99.2% (95.5–99.9%), with 120/123 = 97.6% (93.1–99.2%); 0 fixed, 2 broken, McNemar p = 0.5
- serbadata_business: without 132/134 = 98.5% (94.7–99.6%), with 133/134 = 99.3% (95.9–99.9%); 1 fixed, 0 broken, McNemar p = 1

## Claims

- Accuracy by consensus: 90.6% (85.0–96.2%), n=166
- Judge κ on the held-out half: 0.7931034482758629 (n=72); dev half 0.5812917594654794

## Gold-set judge

- Rubric rubric-4: κ on the held-out half 0.5909090909090909 (n=96); dev half 0.8039969254419678

## Join discovery

- Databases: NOVA_INTELLIGENCE_BENCH, NOVA_RETAIL_FIELD, NOVA_TPCH_LAB; 285 columns in 166 s
- Precision 50/50 = 100.0% (92.9–100.0%), recall 50/50 = 100.0% (92.9–100.0%), F1 1
- Calibration: ECE 0.0152, AUC 0.9992, n=1065
- Decided without a person 1011/1065 = 94.9% (93.4–96.1%); accepted and right 47/47 = 100.0% (92.4–100.0%); rejected but real 0/964 = 0.0% (0.0–0.4%)
