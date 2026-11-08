# Ambiguous questions and periods without data (Phase E)

Recorded 2026-11-08 for findings F2 (ambiguity is rarely detected) and F3 (periods
outside the data are answered), from the [Phase A checkpoint](../../testing/supernova/phase-a-checkpoint.md).

## What the literature says

- **AMBROSIA** (Saparina and Lapata, NeurIPS 2024 Datasets and Benchmarks,
  [arXiv 2406.19073](https://www.arxiv.org/abs/2406.19073)): this benchmark covers
  scope ambiguity, attachment ambiguity and vagueness. Ambiguity remains even when
  the database is given, and the strongest LLMs still struggle to notice and resolve
  it. Vagueness ("sales" with several sales measures) is the type our gold sets
  contain.
- **PRACTIQ** (Dong et al., NAACL 2025,
  [arXiv 2410.11076](https://arxiv.org/html/2410.11076v1)): this conversational
  text-to-SQL benchmark has four ambiguous and four unanswerable categories. In each
  conversation the assistant asks a clarifying question, the user answers, and then
  SQL is written. Both classifying the question and predicting SQL after
  clarification are hard for state-of-the-art LLMs.
- **TrustSQL** (Lee et al., ICLR 2025): this is already our scoring rule (+1 for the
  right behaviour, −1 for a confident wrong answer, 0 for a refusal). A question
  outside the data is "unanswerable", not an empty answer.
- **Databricks Genie** asks a follow-up question instead of guessing when a question
  is vague. Practitioners steer that behaviour with instructions such as "ask which
  revenue measure is meant"
  ([Databricks community](https://community.databricks.com/t5/generative-ai/can-genie-ask-clarifying-questions/m-p/125575)).
  This is the same behaviour our gold sets expect.

## What our data shows before the change

These numbers come from the no-hint runs on release `ont_rel_0005`, covering the
serbadata v2 and serbadata_business gold sets:

- Of 28 ambiguous cases, 3–8 were clarified.
  - Most vague questions name no ontology concept at all ("sales", "revenue",
    "route distance"), so grounding returns `none`.
  - Others resolve through a short alias that the steward attached to one member
    of a family ("retur" → Returned units, "diskon" → Discount amount).
- Every one of the 10 questions about a period outside the data (December 2026,
  April 2026, January/February 2026) was answered or failed, never abstained.
  Before the start of the data, the result is empty. After today, Studio refuses
  the window with a plan error.

## Options tested and decisions

### F3: data window

Decision: we read the data window from the data itself, and only when a dated
result is empty.

- The first and last day are read through two governed plans that run as the
  caller, with the plan's governed filters applied. Row filters therefore shape the
  window exactly as they shaped the result, and no window of hidden rows leaks.
- A requested or comparison period wholly outside that window becomes
  `OUTSIDE_DATA_WINDOW`, which carries the covered range.
- A period that starts after today is reported as having no data yet.
- A normal answer costs no extra query.

Alternatives rejected:

- Profiling windows ahead of time: this belongs to Phase B, needs a
  security-context-keyed cache, and goes stale.
- Asking the model: windows are facts, not judgements.

### F2: vagueness, lexical rule

We measured a lexical rule offline before building anything. A word left over after
exact concept matching was treated as vague when it occurs in the names of two or
more metric concepts, after excluding entity words and tokens that are too frequent.
We swept 24 configurations over 285 gold questions.

| Best configuration | Ambiguous caught | Clear questions wrongly asked |
| --- | --- | --- |
| names ≤ 2 words, token in ≤ 3 concepts | 7 / 28 | 25 / 257 |
| widest | 17 / 28 | 138 / 257 |

The rule fails on modifier words such as "jumlah", "total", "tingkat", "nilai" and
"received". It cannot separate a head noun from a modifier in either Indonesian
(head first) or English (head last). **Rejected.**

### F2: vagueness, candidate-aware planner (chosen)

The planner already turns a material constraint it cannot resolve into a
clarification. It now also treats as unresolved a measure word that two or more
catalog metrics fit about equally, when nothing in the question tells them apart. It
lists the fitting metrics and does not pick one.

- A measure named by a metric's name or synonym is resolved.
- A measure named by an approved ontology definition is resolved. A steward's
  alias is a business decision, so the ontology outranks vagueness. Misleading
  aliases are fixed in the alias review, not by asking every time.

This follows the AMBROSIA and PRACTIQ finding that models handle ambiguity better
when the interpretations are made explicit (here, the catalog's metric
descriptions). The risk is asking about clear questions. The paired measurement
checks both sides: clarification recall on ambiguous cases, and McNemar on
answerable accuracy.

## Scoring correction

In the no-hint runs, a router answer of "no view computes this" was scored as
`failed`. Studio tells the user in that case, so it is now scored as `abstained`.
This applies to both modes equally, and the checkpoint reports results before and
after the correction.
