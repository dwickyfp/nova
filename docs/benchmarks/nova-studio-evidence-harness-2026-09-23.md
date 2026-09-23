# Nova Studio evidence harness — 2026-09-23

> Implementation and measured checks for Agent Studio memory governance, answer evidence, quality checks, and durable runs.

## Scope

The assistant loop remains the single bounded engine in `app/modules/assistant/`. Agent Studio configures it through `app/modules/agents/`. Persistent feature state lives in StarRocks `NOVA_SYSTEM` and is scoped by user, agent, role, and thread where applicable.

| Capability | Implementation | Boundary |
|---|---|---|
| Numeric answer receipt | Every numeric literal in a data answer is checked against bounded authorized table cells; the receipt records evidence ID, column, role, intent revision, selected metrics, semantic fingerprint, and SQL digest. Unsupported prose is withheld while the result table remains available. | This proves literal support, not semantic or causal correctness. |
| Business rule promotion | A remembered user rule can become a proposal for a specific semantic metric. The user sees current/proposed expressions and executes both under their active role before approval. The prior model version is retained. | Proposal approval has no atomic cross-table transaction or rollback endpoint. A preview is a point-in-time comparison. |
| Intent and diagnosis | Intent state records revisions; ambiguous concepts require clarification. `diagnose_change` decomposes a two-period change into arithmetic components and an explicit unassigned residual. | Arithmetic decomposition is not causal attribution. |
| Quality Lab | The semantic inspector replays saved verified questions against the current model and flags changed SQL structure. An offline 50-case adversarial answer corpus and scripted harness trajectories gate code changes. | Saved questions must be reviewed and supplied for each deployment. This lab does not yet execute data or grade LLM prose. |
| MCP tools | Explicitly selected HTTP MCP tools run behind per-call consent, SSRF controls, argument limits, result caps, redaction, and audit. Agent requests accept dynamic tool IDs and reject unavailable MCP selections. | No external MCP service was available for a live end-to-end call in this run. |
| Run journal and reconnect | SSE frames are stored in scoped StarRocks run tables in batches. A disconnected browser resumes by run ID and sequence without reposting the user message. A background producer continues in the same backend process; its subscriber queue is bounded at 128 frames. Heartbeats and a 60-second stale lease mark a crashed producer interrupted; replay ends with an explicit interruption result. | A backend restart does not automatically replay model/tool execution, because doing so could repeat a side effect. The stored frames are recoverable. |

## Reproducible evidence

- `backend/.venv/bin/python -m tests.eval.report`: **41/41 trajectories, 135/135 checks**. Includes numeric grounding, unsupported-answer withholding, diagnosis, consent, boundedness, and redaction.
- `backend/.venv/bin/python -m tests.benchmark.answer_contract_report`: **50/50 curated cases**, 24 accepted and 26 rejected, no false accepts or rejects in this corpus. Latest median check **0.0131 ms**; observed p95 **0.0430 ms**. The cases include swapped rows, swapped metric columns, percentages, signs, dates, and Indonesian currency.
- Targeted backend suite: **144 passed, 1 skipped**. The skip is an existing benchmark that requires a StarRocks benchmark port not published by this checkout. The separate live integration suite for rule proposals and the run journal passed **2/2** on local StarRocks port 29030, including after the FE returned healthy.
- Targeted frontend tests including the Quality Lab UI: **20/20**. TypeScript and targeted ESLint passed.
- Configured `Sales Agent` with `deepseek-v4-1-flash`: three additional paired synthetic memory runs. Baseline without the taught rule could not recall it in **3/3**; memory retrieval recalled the rule in **3/3**; correction to include discounts was reflected in **3/3**. The synthetic memory was deleted after each run. This exercised the configured live provider and memory repository, not the logged-in browser chat route.
- The 20-event StarRocks journal was initially 18,254.56 ms with one write per event. Batching eight events reduced that sample to 2,561.26 ms. Batch size 32 is the current implementation. After the FE returned healthy, five consecutive runs took **195.08, 204.21, 207.89, 411.68, and 439.00 ms** (median **207.89 ms**, observed nearest-rank p95 **439.00 ms**). Earlier runs included outliers of 12,086.54 and 16,776.48 ms, and three attempts could not connect while `nova-starrocks-fe` stopped. The five-run result is a local steady-state sample, not a production latency SLO.

## Interpretation

These results establish deterministic behavior for the tested paths and a small live recall improvement for the configured Sales model. They do not establish universal 100% correctness, a statistically valid production data answer score, or superiority to Snowflake CoWork. The Chrome session for Nova was signed out during this run, so the full logged-in Sales Agent UI data path could not be benchmarked. The backend image was rebuilt and the local container restarted; startup completed and OpenAPI exposed the new routes. The 50-case corpus is developer-curated; a production gate still needs at least 50 human-reviewed questions with expected data and answers from the user's domain.

## UI quality gate

The rule proposal and Quality Lab copy distinguishes conversation memory, governed semantic rules, SQL structure checks, and live data results. The proposal panel uses responsive wrapping and no fixed height. Targeted browser tests cover the memory dialog and narrow viewport. Antislop UI/copy/layout gate: **PASS for tested surfaces**; no visual claim is made for untested application routes.
