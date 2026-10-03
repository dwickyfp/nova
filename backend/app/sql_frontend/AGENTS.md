# SQL Frontend Agent Guide

Inherit the [root](../../../AGENTS.md) and [backend](../../AGENTS.md) contracts.
Read [current architecture](../../../docs/arch-13-sql-frontend.md) and inspect
the affected code/tests. Paths below are repository-relative; test commands run
from `backend/` unless marked otherwise.

## Extension path

Nova owns syntax, meaning, policy, and routing; StarRocks owns physical query
planning and distributed execution. Preserve this direction:

```text
grammar -> central parser -> typed AST -> semantic analysis/binding
        -> registered planner -> ordered lowering rules -> execution plan
        -> executor -> StarRocks or existing Nova service
```

- `backend/app/sql_frontend/` is the production extension point.
  `QueryService` owns request lifecycle, sequencing, history, metrics, and
  responses; new statement detection does not belong in service branches.
- Grammar and generated Python live in `backend/app/sql_dialect/grammar/`.
  Add syntax in marked Nova regions, register typed AST builders and exact-type
  planners, and use the current effect/capability interfaces. Reject duplicate
  registrations and unsupported managed statements rather than forwarding them.
- Slice executable SQL from source spans. Do not rebuild it with parser
  `getText()`, regex routing, prefix matching, or string-based feature detection.
  Existing lexical guards, script splitting, and credential-lowering helpers
  retain their established purposes; this rule is not a blanket regex ban.
- Binding requests only the metadata needed, through caller-authorized adapters
  and request-local caches. Native forwarding must not introduce unnecessary
  metadata queries or broaden caller permissions.
- Planning may read authorized catalog metadata; it must not submit mutations,
  persist metadata, execute actions, or resolve storage/provider secrets.
  Keep passwords, resolved keys, connections, and private execution source out
  of serializable plans and diagnostics.
- Declare effects and destructive-confirmation requirements explicitly. Preserve
  semantic checks, rule effect constraints, capability checks, and executor
  confirmation. Composite execution must honor its declared behavior; never
  imply rollback or atomicity that the pinned engine/adapters cannot guarantee.
- Resolve stage references and authorize every selected scope before any storage
  secret is resolved. Preserve per-reference credential/configuration binding,
  native SQL semantics, and redacted results/audits/errors.
- Query API, proxy, EXPLAIN, assistant validation, and ML preparation use the
  common frontend interfaces appropriate to their paths. Keep their contracts,
  caller context, and audit behavior intact.

The parser APIs in `backend/app/modules/query/dialect/`, compact task/ML parsers,
and legacy security routing are compatibility APIs, not new production syntax
detectors. Existing translator/injector helpers remain execution-time lowering
adapters; do not replace them with another storage or secret system.

## Validation

Follow the backend unit/coverage, eval, changed-file Ruff, and real-engine gates
for SQL execution changes. Focused regressions additionally cover parsing/AST,
semantic rejection, binding/cache isolation, planning purity, capabilities,
effects/confirmation, execution failure, redaction, and actual consumers:

```bash
uv run pytest tests/unit/test_sql_frontend_parser.py tests/unit/test_sql_frontend_binding.py tests/unit/test_sql_frontend_planning.py tests/unit/test_sql_frontend_executor.py tests/unit/test_sql_frontend_consumers.py
```

For grammar or generated-artifact changes, run the CI drift check from `backend/`:

```bash
uv run python scripts/check_grammar_drift.py
uv run pytest tests/unit/test_sql_dialect_grammar.py
```

Regenerate with the canonical script from the repository root. ANTLR uses Java
17 at build time only; the generated runtime parser is Python. Never hand-edit
generated parser files. Stage the expected artifacts, then verify that a
fresh regeneration is byte-identical and creates no untracked grammar artifacts:

```bash
bash backend/scripts/generate_grammar.sh
git diff --exit-code -- backend/app/sql_dialect/grammar
git ls-files --others --exclude-standard -- backend/app/sql_dialect/grammar
```

After intentional generation, review/stage the expected grammar changes before
the repeat-generation comparison; a pre-existing intentional diff is not a
generator failure. CI performs the comparison against the committed tree.
Use the pin in the grammar, engine configuration, and patch tree for support
checks. New syntax needs accepted/rejected corpus cases and execution regressions
through an unchanged consumer, not only a parser unit test.
