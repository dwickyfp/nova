# 01 — The SQL Frontend Pipeline

> Nova parses and plans SQL before routing it to StarRocks or an existing Nova service.

Source of truth: `backend/app/sql_frontend/`. QueryService keeps request lifecycle,
normalization, metrics, history, script sequencing and response responsibilities.
See [the frontend architecture and extension workflow](../arch-13-sql-frontend.md).

## Entry Points

| Entry point | Integration |
| --- | --- |
| Workspace / POST `/api/v1/query/execute` | QueryService → frontend → executor |
| MySQL proxy | Session handling and variable substitution → QueryService |
| Workspace EXPLAIN | Shared frontend and engine executor |
| ML training and streaming prediction input | Shared frontend preparation and stage runtime |
| Assistant syntax validation | Central parse/build/validation; no catalog or execution |

## Execution

```text
normalization + security guard
  → central ANTLR parser
  → typed statement AST
  → effects + semantic validation + optional lazy binding
  → registered planner + engine capabilities
  → one pass of ordered rules
  → credential-free execution plan
  → central executor
  → redacted result + audit
```

The hard guard runs before parsing or Ranger actions. It preserves ACCOUNTADMIN,
root, protected UDF and egress defenses. Direct execution checks the complete
input and accepts exactly one statement. Scripts retain the existing splitter,
sequential results and stop-on-first-error behavior.

Central parsing rejects errors with sanitized line/column diagnostics. Original
and normalized source remain separate, and token spans refer to normalized
source. Comments and literals cannot create false stage or ML references.
Planners select by typed statement, never by a chain of SQL regular expressions.
Unregistered Nova statement types fail closed.

Native SQL retains its normalized engine text. EXPLAIN is a StarRocks request.
Native plans do not query metadata or detect engine versions. Extensions request
binding explicitly through a per-request cache and lower according to an explicit
capability profile.

Plans declare reads, writes, deletes, schema changes, security changes and
external I/O. Required destructive confirmation is separate from writes and is
checked after planning and before execution. Ordered composites stop on failure;
they do not imply a transaction or rollback.

## Stage Preparation and Redaction

Stage references come from the central tree. The stage execution helper resolves
scoped metadata, checks each reference's access and only then resolves storage
secrets. Existing translation and injection produce FILES() SQL, with separate
credentials and CSV settings for each reference. LIST, COPY and export forms use
the same classification and retain existing validation and guards.

Credentials and authenticated connections stay in private execution state.
Engine plans contain safe templates and stage descriptors. Only late engine
preparation creates SQL carrying credentials; responses and audit rows use its
redacted form. The result/response safety layers continue to redact credentials
and fail closed when redaction cannot safely complete.

CREATE TASK, ML operations and password-change policy use registered service
actions. Task metadata cannot contain stage references or explicit credentials.
Ranger-managed security statements use typed operations; unsupported managed
grant forms cannot fall through to native execution.

## Compatibility

The old stage/ML/task parser APIs remain for existing consumers, and the semantic
expression parser retains its bounded expression entrypoint. QueryService no
longer uses these wrappers as runtime feature detectors. SQL validation uses the
central frontend and does not bind catalog objects.

See [stage syntax](02-stage-queries.md), [guardrail invariants](09-guardrails-invariants.md)
and [the migration validation report](../benchmarks/sql-frontend-migration.md).
