# Business Intelligence benchmark

This benchmark tests Nova's governed lifecycle through the production Studio,
Semantic View, Intelligence, memory-review, and task-handler contracts. It is
independent of the existing Studio L0/L2/L3 benchmark families. Those families
remain regression gates.

## Evidence levels

A scripted provider verifies the pipeline, persistence, isolation and numerical
results. It does not establish empirical improvement in an LLM. Real StarRocks
and Redis runs establish engine behavior on the tested configuration; native
RBAC runs do not establish patched-FE Ranger acceptance. Local results do not
establish a GitHub Actions result.

Live runs use the registered default `deepseek-v4-1-flash`, require explicit
spend authorization, and remain deferred. A live result needs at least three
paired repetitions. The report keeps accuracy, consistency, uncertainty,
latency, token availability and cost reservations separate. The 15 percentage
point learning-sensitive target is assessed with a paired case-cluster
bootstrap interval. Unreviewed narrative and causal-safety dimensions remain
unavailable; they are never filled with successful scores.

## Files and isolation

The implementation is in `backend/tests/benchmark/business_intelligence/`:

| Module | Responsibility |
| --- | --- |
| `generate.py`, `dataset.py`, `seed.py` | Seeded warehouse, manifest and row-integrity checks |
| `model.py`, `bootstrap.py` | Published starter Semantic View and four restricted specialists |
| `learning_corpus.py` | 80 teaching interactions, independent of evaluator answers |
| `gold/cases.py`, `gold/queries.py` | 60 Holdout A and 40 Holdout B cases and independent SQL |
| `client.py`, `experiment.py` | Actual Studio turns, feedback, semantic review, consolidation and paired evaluation |
| `freeze.py` | Implementation, dataset, provider and corpus hashes |
| `evaluate.py`, `statistics.py` | Evidence grading, explicit unavailable dimensions and paired statistics |
| `live_budget.py`, `run.py` | Opt-in provider execution with reservations before calls |
| `scripted_provider.py`, `scripted_planner.py` | Offline provider boundary using only the supplied public catalog |
| `stories.py` | Export two completed development stories after lineage, independent numerical checks and outcome retries pass |

The small profile contains 1,000 customers and 5,000 orders; standard contains
25,000 and 150,000; full contains 50,000 and 300,000. Dependent tables scale with
those profiles. The warehouse covers commerce, inventory, marketing, checkout,
payments, support and service operations. Generation uses a fixed seed and
observation cutoff.

Gold SQL, expected plans, evaluator rubrics and future outcome-training data
must never become agent resources. Evaluation threads set
`learning_enabled=false`. Teaching uses separate threads and reviewed semantic
proposals. Outcome-training fixtures must use a separate warehouse/view and
must not mutate the paired evaluation observations.

The real-engine development test `tests/integration/test_intelligence_studio_live.py`
exercises both business stories in a separate future-training warehouse. Set
`NOVA_INTELLIGENCE_STORY_REPORT` to an unused output directory to export their
public evidence as `stories.json` and `stories.md`. Export happens only after
the test verifies that the original observation rows and semantic version are
unchanged. Provide explicit throwaway signing and encryption keys, plus all
test-stack endpoint overrides, as required by the integration CI job. The test
does not load a developer's deployment secrets into its report.

The patched-FE lifecycle test can pause for actual browser checks with
`NOVA_INTELLIGENCE_BROWSER_ACCEPTANCE=1`. Its populated, stale and
empty/denied checkpoints retain the same restricted identity and real API.
The browser runner verifies loaded lineage and evidence, keyboard actions,
desktop/light and narrow/dark layouts before acknowledging each checkpoint.

## Reproduction

Use an isolated test installation with the repository's bootstrap and runtime
migrations applied. Configure its FE and Redis endpoints using the existing
Nova settings. Provision a non-root business identity with read-only access to
`NOVA_INTELLIGENCE_BENCH`; do not use `ACCOUNTADMIN` for Studio turns. Set
`NOVA_BENCH_USER`, `NOVA_BENCH_PASSWORD` and `NOVA_BENCH_ROLE` through the
execution environment, never a committed file or command argument.

From `backend/`, generate and explicitly seed a disposable database:

```bash
uv run python -m tests.benchmark.business_intelligence.generate --help
uv run python -m tests.benchmark.business_intelligence.seed --data /path/to/observations
```

Seeding refuses an existing database unless `--replace` is explicit. The loader
accepts only the benchmark database. Do not replace observations after freezing.

After implementation and provider configuration are final, freeze the live
experiment without making a provider call:

```bash
uv run python -m tests.benchmark.business_intelligence.run freeze \
  --dataset-manifest /path/to/observations/manifest.json \
  --frozen /path/to/evaluator/frozen
```

The runner compares live warehouse rows with the frozen CSV values, not just a
manifest's claimed hash. It verifies implementation, data and provider
configuration before each evaluation stage. A changed implementation requires a
new paired experiment; a failed or interrupted run cannot overwrite its output.

For a deterministic pipeline experiment, freeze with `--provider-mode scripted`,
then run the `scripted` operation with the same paths, an unused `--output`,
and `--repetitions 1`. This replaces only provider boundaries, disables paid
embeddings, and executes the same authenticated application routes. Its limited
planner matches supplied catalog names and synonyms; it cannot read evaluator
plans or gold SQL. Unsupported wording remains unscored or unsuccessful rather
than being supplied a scripted gold answer.

Paid execution is deliberately a separate operation. Only after authorization,
run `live` with `--allow-paid-calls`, an unused `--output` directory, at least
three repetitions, a positive `--maximum-dollars`, and current
`--input-dollars-per-million` and `--output-dollars-per-million` rates. The
runner executes production HTTP routes in its own process so outbound calls
cannot escape its provider guard. It pins lexical memory retrieval across both
conditions and disables unbudgeted embedding calls at the provider boundary.
Reservations include the transport retry ceiling; they are not billed-cost
measurements.

## Reports and completion

`report.json` and `report.md` summarize stages. Per-stage rows include case IDs,
thread/message references, correctness dimensions and unavailable evidence.
`learning.json`, `knowledge-reviews.json` and `consolidation.json` record the
production learning path. `cost-reservations.json` reports the conservative
provider reservation and leaves actual billed cost unavailable.

The experiment remains `awaiting_narrative_review` until causal claims,
authorization behavior and narrative rubrics have been reviewed against actual
traces. Interrupted runs retain their failure type and partial case results;
raw server errors and credential-bearing provider configuration are excluded.

No official B0/B1/B2 or paid-provider improvement result has been accepted yet.
Development tests and their logs must not be relabeled as official holdout
results.

The [development stories](development-stories-20261002/stories.md) passed the
production API test on the isolated StarRocks 4.1.4 stack. Their companion
[JSON evidence](development-stories-20261002/stories.json) records independent
outcome validation, exact decomposition, approvals and private inferred
learning. The test also rejected an unauthorized empty-table query branch.
Those historical story artifacts predate the added cross-domain comparisons
and second decision option. The [extended stories](development-stories-cross-domain-20261002/stories.md)
passed a new engine run in 1053.33 seconds. Their
[JSON evidence](development-stories-cross-domain-20261002/stories.json) confirms
Jakarta inventory fell from 14000 to zero while marketing spend stayed at
1750000 IDR, and payment latency rose from 90 to 1400 milliseconds while
marketing spend stayed at 4000000 IDR. Each decision retained two independently
checked numerical options. A later Studio turn received the corresponding
private inferred outcome memory. The scripted provider checked that context
and preserved the distinction between observation and causal attribution;
this result does not measure live LLM quality.
The [local validation report](local-validation-20261002.json) keeps completed
checks, failed attempts and outstanding acceptance gates separate.

The [browser acceptance](browser-20261002/results.json) used the current isolated
program snapshot against the patched FE and Ranger API. Populated, stale and
empty/denied views passed in desktop light and 360 px mobile dark themes, with
keyboard selection and retry, no page errors and no document overflow. Dataset
revocation hid shared decisions and prevented detail access. These are local
results; GitHub Actions and paid provider evidence remain unverified.
