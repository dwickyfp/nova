# Smart acceptance lab

A live check that Studio's Smart mode answers like one analyst: a person who
knows the business, and nothing about Nova, asks about finance and employee data
served by two separate agents.

It is not part of CI. It needs a running stack, a configured model provider, and
it spends provider credit.

## What it holds

| File | Purpose |
| --- | --- |
| `seed.py` | Deterministic `NOVA_SMART_LAB` tables, the lab user and role, read access, two Semantic Views, a Finance and an HR agent |
| `gold.py` | Expected values from SQL, computed without any agent |
| `cases.py` | The questions, with the gold and the chart, table or schedule proposal each must show |
| `run.py` | Asks the running backend as the lab user and records what a user sees |
| `judge.py` | Scores a pass |

## Run it

The backend and `python -m app.agent_worker` must be running. Seeding uses the
engine's administrative configuration, and `RANGER_*` on a governed stack.

```bash
cd backend
uv run python -m tests.benchmark.smart_native.seed
uv run python -m tests.benchmark.smart_native.gold
uv run python -m tests.benchmark.smart_native.run /tmp/pass1.json
uv run python -m tests.benchmark.smart_native.judge /tmp/pass1.json
```

The generated lab password, the session token, object ids and gold values are
kept in `~/.cache/nova-smart-lab` (`NOVA_SMART_LAB_STATE` overrides it), outside
the repository. `NOVA_SMART_LAB_API` points the client at another backend.

## What passes

A case passes only when all of these hold for its last turn:

- every gold figure is stated, allowing only the rounding the answer shows;
- no product wording reaches the reader: identifiers, backticks, agent or tool
  names, evidence ids, or notes about removed numbers;
- the chart, table or schedule proposal the case requires came with the answer;
- the stream carried no error and every turn completed;
- the judge model, reading as that business person, gives at least 4 of 5 for
  accuracy, completeness, nativeness and groundedness. It reads each answer three
  times and the middle score per dimension counts, because a single reading
  sometimes miscounts digits (58,851,000,000 read as millions).

Smart is accepted when every case passes in three consecutive passes on the same
code. A failure resets the count. Do not relax a case to make it pass.

Scheduled runs need worker impersonation and
`scripts/provision_task_worker_access.py` for the lab user; see
[HOW_TO_RUN.md](../../../../HOW_TO_RUN.md).
