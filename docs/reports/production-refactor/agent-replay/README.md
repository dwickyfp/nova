# Agent replay verification

The direct Studio producer now journals each Mission update before delivering
it. It saves the last projection before assigning the successful completion
sequence, so projection-storage failure stops the run without publishing a
successful terminal event or advancing to another tool.

Replay drains every retained page of a failed, interrupted or cancelled run.
Previously it synthesized completion after the first 100 events. A completed
run whose remaining events are absent now returns `run_history_unavailable`
and closes the stream. It does not change the source run, fabricate the missing
history, call a provider, or repeat an external action. Normal completion and an
already-consumed cursor keep their previous behavior.

The final 18 regression cases were also run with the parent `08a91e0e` router
loaded into an isolated test process: 15 failed and three passed. The running
server and working-tree source were not replaced. The final focused suite
passes all 97 replay, journal, storage-failure and Mission-service cases.

Earlier attempts are retained in [commands.json](commands.json), including the
initial timeout reproduction, six pagination failures and the Mission fixture
corrections. The initial Mission fixture omitted required public fields; it
also incorrectly required the best-effort interruption notice to wait for
storage. The corrected fixture checks saved Mission updates and successful
completion before delivery, and checks the eventual saved interruption when
storage remains available. It preserves the existing immediate failure notice
when storage is broken. No test was disabled. An incomplete unit run was stopped
after the pagination defect was found; it is not counted as full-suite evidence.

[Live API evidence](live-api.json) records 21 passing assertions on disposable
metadata fixtures in the existing patched local StarRocks stack. Cases include
empty and partial history, ordinary completion, an already-consumed cursor,
201-event histories with and without a saved terminal event for each failure
status, and refusal under a different security-context version. Every run and
event is unchanged by replay. The API-created agent and conversation were
deleted through their normal endpoints; no fixture run/event rows remain.
These controlled storage fixtures make no live model call and do not replace
the separate live-AI acceptance matrix.

The [read-only source profile](source-profile.json) found 533 runs and 13,621
events, including 108 historical collaboration runs. Sixty-two direct runs
have a high-water mark but no retained events: 23 completed, 37 failed and two
cancelled. Their threads still exist. No source row was repaired or deleted;
the cause of this historical loss is not established, and it is not attributed
to the Mission producer defect. This is a live aggregate, not a frozen export
or a migration reconciliation result.

The updated Studio error guidance was verified through the running API and
viewed at `/docs/studio/ask-questions`. Its docs browser suite passes 15 cases.
Final full-suite results and the exact application/test/doc hashes are recorded
in [commands.json](commands.json). Diagnostic output and credentials are omitted
from exported test reports.

| Final local check | Result |
| --- | --- |
| Backend unit/branch coverage | 10,818 passed, zero skipped; 65% combined coverage |
| Agent trajectories | 349 passed, zero skipped |
| Agent scorecard | 54/54 scenarios, 189/189 checks |
| Full-application mypy | Passed; three existing notes |
| Changed-file Ruff | 96 Python files passed |
| User-data adapter boundary | Passed |
| Frontend lint/build | Passed; zero lint errors, 32 existing warnings |
| Frontend coverage | 1,605 tests across 194 files passed |

The parent `08a91e0e` checkpoint has eight successful CI jobs. Its downloaded
artifacts report 486 PG cases without skips and 217 engine passes with 24 skips;
[the CI record](../ci-08a91e0e.json) retains every skip reason. The unpatched CI
engine and those skips do not establish governed, external-fault or live-AI
acceptance. CI for this replay change is recorded separately after push.

This repairs the existing StarRocks-backed replay path. PostgreSQL run/event
transactions, durable consent, worker fencing, session revocation, remaining
domain ports and the global one-way cutover remain open. The owner waivers for
live embeddings and the original large-load/soak profile are unchanged.
