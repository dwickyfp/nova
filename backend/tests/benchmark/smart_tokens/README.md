# Smart token benchmark

Measures how many model tokens a Studio Smart request spends, per model call,
so a change to Smart can be compared before and after. The first case is a
two-chart dashboard (see `docs/specs/2026-10-30-smart-dashboard-token-budget.md`).

It is not part of CI. It needs the running stack, a configured model provider,
and it spends provider credit.

| File | Purpose |
| --- | --- |
| `probe.py` | `python -m app.agent_worker` with the provider client wrapped: one JSON line per model call with the participant (`/root`, `/root/<task>`, or `internal` for planner and chart calls), the prompt's parts in estimated tokens, the tools offered and called, and the usage the provider reported. No message content is written. |
| `run.py` | Asks Smart each measured request on a new thread and records each run's id, status and time window. |
| `report.py` | Matches calls to runs and summarises them; `--compare` puts two summaries side by side. |

## Run it

Stop any other `app.agent_worker` first: every Smart run must pass through the
probe, and only one request should run at a time.

```bash
cd backend
NOVA_TOKEN_PROBE_FILE=/tmp/calls.jsonl STARROCKS_FE_MYSQL_PORT=29030 \
  uv run python -m tests.benchmark.smart_tokens.probe        # leave running

NOVA_TOKEN_PROBE_PASSWORD=... uv run python -m tests.benchmark.smart_tokens.run \
  /tmp/runs.json --repeat 3
uv run python -m tests.benchmark.smart_tokens.report /tmp/runs.json /tmp/calls.jsonl \
  --json /tmp/summary.json
uv run python -m tests.benchmark.smart_tokens.report --compare BEFORE.json AFTER.json
```

`NOVA_TOKEN_PROBE_USER` (default `nova_admin`) signs in with its default role;
`NOVA_TOKEN_PROBE_API` points at another backend.

## Reading it

- **Reported** tokens are the provider's own usage and are what the Smart
  budgets count. **Parts** use Nova's estimator (characters / 4) and only show
  where a prompt's size comes from.
- The root's token allowance is `SMART_ROOT_TOKENS` in
  `backend/app/modules/agents/harness_worker.py`.
- Model output varies: run each side several times. Means are over all runs,
  failed ones included, so a change that only makes failures cheaper is no saving.
