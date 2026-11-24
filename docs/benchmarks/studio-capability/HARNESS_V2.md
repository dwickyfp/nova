# Studio benchmark harness v2

> Grades recorded Studio turns from structured events, figure provenance and
> the page tree instead of text patterns. Nothing in v2 grading uses regular
> expressions; a unit test fails if any v2 module imports or calls `re`.

The code lives in `backend/tests/benchmark/studio_capability/v2/`. The v1
runner (`run.py`) still drives Studio; v2 adds a typed corpus, a gate that
blocks live runs on a stale corpus, a quota budget, a structured judge,
paired statistics and shadow-run tooling. v1 scoring is retired after two
reviewed shadow runs (owner decision D8).

## Verdicts

Every unit gets `pass`, `fail` or `judge` (the deterministic grader cannot
decide; the LLM judge or a person does). Two layers are reported:

| Layer | What it checks |
| --- | --- |
| Core | Outcome kind, safety (no leaked values, no save without the user, no approval cards), figures traced to the evidence the user saw |
| Full | Core plus the UI tree: required and absent element kinds, dangling references, schema errors, inert controls, unbound KPI tiles or charts, inputs that repeat the question |

| Module | Role |
| --- | --- |
| `fold.py` | Folds the protocol-v2 event journal into `Signals`: tools, cards, parts, tables, components, and evidence values with their source |
| `outcome.py` | Reads `run.finished.outcome` (TurnOutcome, D1) with the product's own model, including `basis`, `hitl` and `action_receipts`; recordings made before the contract, or with an outcome that does not validate, get an outcome derived from signals and marked `derived` |
| `ui_tree.py` | Parses OpenUI pages with the product parser, evaluates them, and checks each component against `ui_components.json` (JSON Schema 2020-12) |
| `provenance.py` | Matches expected figures against evidence: exact, then x100 or /100, then sign; small figures never match an aggregate |
| `adapters.py` | Attaches dataset previews and the thread trace after a live turn (as the user); reads deep-research findings |
| `grade.py` | Combines the above; tool selection and numeric consistency reuse `app/modules/agents/quality_scoring.py` |

When Studio marks its outcome `basis: unverified` (nothing in the turn's facts
decides it) and that outcome does not match the expectation, the unit goes to
the judge instead of failing.

## Corpus v3

`corpus/v3/units.jsonl` holds 601 typed units (`expectation.py`, pydantic,
unknown fields rejected). `convert.py` builds it from v1 plus the recorded
changes (E1, the prototype corpus patch, owner decisions D2-D5); each change
is listed in `corpus/v3/CHANGELOG.md`. Units that need a measure the semantic
view does not have yet are `deferred` and skipped.

`ground_truth.json` keys each reference by a hash of its SQL. References
imported from v1 are marked `imported:v1-...`. The linter
(`corpus_v3.py`) checks ids, owner rules, references, bindings and measures;
for a live run it treats imported or missing references as errors, so
`run.py` stops until ground truth is recomputed through Nova:

```bash
cd backend
uv run python -m tests.benchmark.studio_capability.v2.ground_truth --compute   # live, read-only SQL through Nova
uv run python -m tests.benchmark.studio_capability.v2.corpus_v3                # lint for a run
uv run python -m tests.benchmark.studio_capability.v2.corpus_v3 --offline      # lint for re-scoring
```

## Re-scoring recorded runs (offline)

```bash
uv run python -m tests.benchmark.studio_capability.v2.rescore \
    --run baseline=/path/bench/baseline --run rebench=/path/bench/rebench \
    --rca /path/units.csv --out /path/rescore
```

Writes `rescore.csv` (v1, v2 core and v2 full per unit), `summary.json`
(counts, agreement with root-cause labels, paired McNemar) and `flips.md`.

## Live runs and budget

`run.py --token-budget N` estimates each unit from recorded usage (category
median, then the unit's own, then the overall median) and stops cleanly
before a unit that would exceed what is left. A provider refusal is never
counted as a Studio failure. The manifest records the corpus version, the
ground-truth fingerprint and the budget.

## Shadow runs

```bash
uv run python -m tests.benchmark.studio_capability.v2.shadow plan --prs PR-1,PR-2 \
    --history /path/bench/baseline --history /path/bench/rebench \
    --token-budget 3000000 --out /path/shadow-1
uv run python -m tests.benchmark.studio_capability.run --ids "$(cat /path/shadow-1/ids.txt)" \
    --token-budget 3000000 --out /path/shadow-1
uv run python -m tests.benchmark.studio_capability.v2.shadow compare \
    --run /path/shadow-1 --baseline /path/bench/rebench --write-ledger
```

The plan covers the units each PR should flip, one passing canary per
category and repeat runs for stochastic units. `compare` writes the v1/v2
flip table and the paired report, and records the run in the baseline's
shadow ledger.

## Paired report

`report.py --before DIR --after DIR [--twin DIR]` gives fixed and regressed
units with an exact McNemar test over units decided in both runs, pass^k over
repeat runs (flaky units are not counted as fixes), judge-score differences
with a 95% bootstrap interval that resamples whole sessions, and tokens per
shared unit.

## Judge v2

`judge.py` sends one turn packet with structured output (strict JSON
schema), temperature 0 and the deterministic facts that bind the scores. It
can abstain (`needs_human`). Pairwise mode runs both orders and keeps a
result only when the two orders agree. Results are cached by journal,
expectation, rubric version and model. The judge refuses to run with the
model under test.

```bash
export NOVA_BENCH_JUDGE_BASE_URL=... NOVA_BENCH_JUDGE_API_KEY=... NOVA_BENCH_JUDGE_MODEL=...
uv run python -m tests.benchmark.studio_capability.v2.judge absolute --run /path/run
uv run python -m tests.benchmark.studio_capability.v2.judge pairwise --before /path/before --after /path/after
```

## Gold set and calibration

`gold.py` builds a 40-turn labelling page (15 judge turns, 10 refusals or
limitations, 10 data answers, 5 pages) with a fixed seed. It shows what the
user saw and the expectation, not the harness verdicts. The labeller exports
JSON from the page.

```bash
uv run python -m tests.benchmark.studio_capability.v2.gold --run rebench=DIR --run baseline=DIR --out DIR
uv run python -m tests.benchmark.studio_capability.v2.judge calibrate --run DIR --gold labels.json [--llm-labelled]
```

`calibrate` reports Cohen's kappa against the owner thresholds (D7): outcome
at least 0.70, dimensions at least 0.60. `action_failed`, `error` and
`limitation` count as one "no answer" class for units that do not expect an
action. When the gold labels come from a model, pass `--llm-labelled`: the
report flags the kappa as "LLM-labelled gold" and lists 10 units (fixed seed)
for the owner to check by hand.
