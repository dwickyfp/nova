# Nova Agent and Semantic Intelligence Runtime

> Nova now owns routing, context selection, semantic planning, SQL compilation,
> validation, recovery, and evidence. Provider models handle bounded language tasks.

---

## Architecture

```text
user request
  -> TurnRouter
  -> ActiveConversationState
  -> CapabilityRegistry + SkillRouter
  -> ContextCompiler
  -> provider adapter (FAST / GUIDED / STRICT)
  -> validated action
  -> capability runtime
       semantic_query -> model router -> catalog retriever -> SemanticPlan
                      -> relationship/fanout/additivity validation
                      -> deterministic StarRocks SQL compiler
       query_execute  -> guarded read-only query path
       ml_execute     -> task-specific Nova ML schema
       data_to_chart  -> verified chart artifact
  -> canonical ToolOutcome
  -> ResultVerifier -> EvidenceTracker
  -> tool-free FinalComposer
```

The loop remains bounded by iteration, total duration, per-tool calls, one repair,
planning steps, and context tokens. Trace records contain structured decisions,
not chain-of-thought.

## Agent Runtime

`assistant/intelligence.py` supplies deterministic turn routing, tool gating,
active state, skill selection, JSON argument validation, evidence enforcement,
and ordered prompt compilation. `provider_capabilities.py` contains transport
differences such as native tools, forced tool choice, strict schemas, structured
output, tool-role messages, and context size. Nova business logic does not branch
on provider names.

Agent instructions are compiled once into a bounded contract. Secret-shaped
values are rejected before persistence. Unsafe override text is retained only in
the compiled audit record and is not sent to the provider. Default skills load as
trusted procedures; discoverable skills are selected per turn. The global catalog
is no longer prompt content.

Every tool returns a canonical envelope with `ok`, `data`, `evidence`, artifacts,
warnings, metadata, and structured recoverable errors. Native adapters use the
tool role. Text-only adapters use an assistant-owned `TOOL_RESULT_DATA` block,
never a user message. Permission, consent, policy, and secret failures terminate.
Argument, semantic-concept, and schema failures receive at most one focused repair.

## Semantic Intelligence

Ossie remains Nova's interchange format. It compiles to `SemanticModelIR`, which
distinguishes facts, dimensions, metrics, dataset grain, metric additivity,
relationships and cardinality, named filters, sample literals, examples, and
routing/query instructions.

The semantic path is:

```text
question -> SemanticModelRouter -> SemanticCatalogRetriever -> LiteralResolver
         -> SemanticPlan -> SemanticGraph -> Fanout/Additivity validators
         -> SemanticCompiler -> StarRocks SQL -> requesting user's connection
```

The provider may repair or supply a strict SemanticPlan when deterministic
selection is ambiguous. It never supplies the SQL or join path. Unknown,
many-to-many, and one-to-many paths that can duplicate a metric are rejected.
Non-additive and semi-additive metrics are checked before compilation.

Verified queries persist the question, version-bound plan, verified SQL, result
signature, verifier, tags, and success counters. Retrieval ignores stale model
fingerprints. Semantic workload telemetry stores only query shape, version,
latency, and status. It can generate review-only materialized-view and semantic
feedback suggestions; it never changes governed models or creates an MV.

## APIs and Persistence

Semantic Studio exposes validation, lint/readiness, question/SQL preview, and VQR
create/list endpoints. The additive migration is
`backend/migrations/20260921_agent_semantic_intelligence.sql`.

It adds compiled/discoverable agent configuration and creates:

- `NOVA_SYSTEM.CONFIG_SEMANTIC_VERIFIED_QUERIES`
- `NOVA_SYSTEM.AUDIT_SEMANTIC_QUERY_USAGE`

Legacy instruction fields, skill lists, semantic definitions, and the
`semantic_query` API continue to work. Missing grain/cardinality metadata receives
lint warnings and unsafe cross-dataset aggregation fails closed.

## Nova Studio Surface

Agent Configuration uses automatic runtime orchestration, exposes the persisted
compiled instruction contract, and provides separate
Default/Discoverable/Unavailable states for each skill. Provider capability and
turn risk determine the effective strategy; users do not select an internal
harness mode. Server-generated compiled instructions are read-only and are never
sent back as editable agent configuration.

The Semantic Models page includes an inspector with three focused views:

- Query Preview shows confidence, the relationship path, SemanticPlan, compiler
  warnings, and generated StarRocks SQL without executing the query.
- Quality shows compiler readiness, objective completeness indicators, validation
  failures, and lint findings.
- Verified Queries lists version-bound examples; a reviewed preview can be saved
  directly as a question, SemanticPlan, and read-only SQL tuple.

Observability traces show the routed intent, effective FAST/GUIDED/STRICT mode,
selected tools and skills, prompt budget by category, explicit state transitions,
active conversation state, and the evidence ID accepted from each verified tool
result. Tool data remains visually and structurally separate from user messages.

## Evaluation

```bash
cd backend
uv run pytest -q tests/unit/test_agent_intelligence.py \
  tests/unit/test_semantic_intelligence.py
uv run python -m tests.eval.intelligence_report
uv run pytest -q tests/eval
uv run python -m tests.eval.report
```

`intelligence_report` is the provider-free portability gate. It currently covers
28 routing/tool cases and six multi-model semantic cases. `tests.eval.report`
uses scripted providers for consent, recovery, injection, context, and termination
trajectories. Live-provider scores are intentionally not invented when credentials
or an authorized test warehouse are unavailable.

### Prompt comparison

Using Nova's existing deterministic `characters / 4` estimate on the checked-in
plain-assistant seed, the platform contract changed from 6,014 characters
(approximately 1,503 tokens) to 1,526 characters (approximately 381 tokens), a
74.6% reduction. Agent Studio additionally removed the duplicated global skill
catalog and gates tool schemas per route. Default task procedures and relevant
semantic slices remain because they carry task-specific correctness.

## Security

All SQL and ML data access still uses the requesting user's connection. The
semantic compiler does not open an engine connection or bypass StarRocks RBAC.
Catalog retrieval accepts an authorized dataset set and returns only that slice.
Provider context contains no storage credentials. Tool text remains data across
the action, repair, and final-composition phases.

## Known Limits

- Token accounting uses Nova's existing deterministic character estimate, not a
  provider tokenizer.
- High-cardinality literal resolution consumes candidates from `semantic_search`;
  index quality remains dependent on the configured search index.
- Live weak-model before/after comparison requires configured provider credentials
  and an authorized benchmark dataset. The offline harness is ready and runs in CI.
- The current materialized-view advisor returns candidates for review. It does not
  create physical objects.
