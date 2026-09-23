# Semantic and assistant hardening — implementation checkpoint

> Local implementation and offline verification; the full supplied guide is not yet complete.

## Executive summary

The assistant now requires successful execution of mandatory capabilities before
answering data questions, including qualitative conclusions. The semantic path
tracks unresolved constraints, scopes catalogs before provider use, parses
expressions with the existing StarRocks grammar, and refuses unproven multi-fact
aggregation. No new database or agent loop was introduced.

This workspace is also receiving concurrent changes in assistant routing,
skills, security, and ML. Those changes have been preserved. A test collection
temporarily encountered a duplicated keyword while the same fixture was being
edited elsewhere; a subsequent run passed. Verification should be repeated on
a stable combined checkout before merge.

## Agent runtime

- Data routes require completion of their capabilities; unavailable capabilities
  and repeated non-execution have explicit termination reasons.
- Agent-bound semantic requests cannot silently fall back to raw SQL.
- Final composition disables execution, including unexpected tool calls. Queued
  actions finish before composition begins.
- Strict mode narrows execution to the next required capability. All modes retain
  consent, validation, bounded repair, and evidence gates.
- Text-only providers use structured actions through the same engine. Native
  tool-call fields and tool-role messages are omitted on that path.
- Argument validation uses the effective provider schema and checks nested
  objects, arrays, nullable values, unknown properties, and numeric bounds.
- SSE output uses the evidence-filtered answer rather than buffered provisional
  text when the two differ.

## Semantic correctness

- Custom catalog terms contribute to routing; ordinary segment analysis is no
  longer automatically interpreted as a clustering request.
- Unresolved material literals remain explicit and block compilation; they cap
  confidence rather than disappearing from a supposedly complete plan.
- Bounded distinct-value lookup uses caller credentials and the query service.
- YoY, QoQ, MoM, and WoW are explicit. Comparisons select disjoint current/prior
  windows. Incompatible windows and unsupported custom comparisons are refused.
- Expression qualification uses the pinned grammar, preserves functions and
  strings, and rejects comments, subqueries, variables, and unsafe trailing SQL.
- Relationship fields resolve to their physical expressions. Metric expressions
  can resolve logical fields and contribute referenced datasets to join planning.
- Multiple metric base datasets are refused until a safe shared aggregation
  strategy is implemented. This is a deliberate limitation, not successful
  support for arbitrary multi-fact analytics.
- Semi-additive non-additive-dimension metadata survives normalized definitions.

## Authorization

Caller-scoped, zero-row queries check dataset access before semantic catalogs
are supplied to providers. Empty authorization means no access. Scoping removes
cross-dataset expression references and metric dependency chains that reach
unavailable datasets. Query and search share model resolution. Search refuses
ambiguous or non-text field targets. Result text and tables use value redaction.

Real-engine role behavior and row-level policy interaction still need integration
verification; offline tests do not establish those properties in deployment.

## Conversation state

Tool-owned patches replace geographic keyword guessing. Semantic plans, metrics,
filters, and comparison state are recorded. Authoritative plan patches replace
filter/time maps, and explicit null values can remove individual keys. Follow-up
planning preserves prior constraints when it can resolve the new request.

Arbitrary natural-language replacements and compound follow-ups are not fully
covered. A follow-up replan after literal lookup now retains the prior metric,
filters, and time context. Exact literals matching multiple fields, or conflicting
equality values for one field, remain unresolved rather than adding silent filters.

## VQR

Retrieval combines lexical/sequence signals, plan terms, and unambiguous synonyms
from the authorized catalog. The redundant lexical-overlap gate was removed;
selected hits must still agree with the newly planned semantics and fingerprint.
This is catalog-grounded retrieval, not embedding-based retrieval.

Saving an example now compares parsed sources, projections, filters, grouping,
having, distinctness, join conditions/types, ordering, and limits against the
compiled plan. Formatting and table/output alias changes are accepted. Obvious
contradictions are rejected before persistence, with endpoint tests confirming
caller ownership and fingerprint metadata. Nested queries, wrappers, sampling,
and unsupported source modifiers are refused. This conservative checker is not
a general SQL-equivalence prover; some equivalent rewrites require the canonical
compiled form.

## Provider portability

Schemas are normalized without claiming strict compatibility for unsupported
unions or explicitly open objects. Text-only success and invalid-action
trajectories pass offline. The semantic-plan generator now shares a closed,
fully specified response schema with local validation, including unresolved
concepts, nullable time fields, filter values, ordering, and limits. Structured
providers receive the strict schema; text-only providers receive the same schema
as data and undergo the same validation. A live-provider matrix remains untested.

## Compiled semantic guidance

The planner supports explicit `Always apply named filter 'name'.` rules and
`When 'term' is ambiguous ... ask clarification.` rules. Filter names must exist
in the governed model. Unsupported freeform guidance fails closed with an
actionable message rather than being ignored or executed as arbitrary SQL.
The supported grammar is intentionally narrow; general natural-language business
policy interpretation is not implemented.

## Tests

- Latest combined targeted run: **389 passed**, with one dependency deprecation
  warning, across semantic, routing, tool,
  authorization-observability, parser/serialization, migration, assistant, and
  eval tests.
- An earlier wider run included benchmarks: 280 passed, 1 skipped, 7 failed.
  Six failures were mismatched legacy test fixtures; the other concerned a
  concurrently changed skill catalog. The later targeted run included these
  unit suites and passed. Benchmarks were not rerun after every final edit.
- Type checking: the initial eight-module check passed; the follow-up check of
  guidance, plan contract, verification, planning, and runtime also passed.
- Follow-up contract/VQR/guidance test file: **31 passed**.
- Latest scripted trajectory report: **32/32 scenarios, 96/96 checks passed**.
- The expanded intelligence report passed 31 routing cases, 6 semantic cases,
  and 4 adversarial checks. This is a small deterministic corpus, not an estimate
  of production answer quality.
- Lint and formatting checks passed for the follow-up semantic modules and tests.
  These are scoped results, not claims about unrelated parallel changes.
- A whole-workspace whitespace check later reported a trailing blank line in
  the concurrently edited ML intercept; it was not changed by this work.
- Live StarRocks, live-provider, browser, and full integration tests were not run.

## Adversarial eval

Added coverage includes qualitative claims without execution, unavailable
capabilities, text-only invalid actions rejected before consent, unknown
constraints, disjoint YoY windows, denied expression dependencies, multi-fact
refusal, injected expression text, and recursive argument validation.

## Regressions

Existing educational-answer and context-curation fixtures now ask educational
questions rather than data questions with scripted ungrounded answers. Consent
fixtures declare the arguments they actually pass. The completion gate was not
weakened to preserve those old fixture mismatches.

The account-creation procedure no longer generates or displays credentials.
The SQL documentation index now describes the wired ML inference intercept.
The requested antislop skill was unavailable; manual checks were used instead.

## Remaining limitations

Required remaining work includes broader multilingual literal/follow-up handling,
expanded business-guidance coverage, and real-engine/provider integration tests.
VQR verification remains conservative and retrieval has no embedding index.
Confidence values are heuristic scores, not calibrated probabilities. The full
guide is not claimed complete. Concurrent changes are preserved; verification
must be repeated on the combined checkout before merge.
