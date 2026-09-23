# Nova full validation — 2026-09-23

## Verdict

The available automated suites were exercised, including real StarRocks integration and browser component tests. The checkout is **not fully green**: the real-engine assistant benchmark fails, and repository-wide static checks fail. Passing tests do not establish complete implementation of the semantic-hardening guide or production readiness.

The workspace had concurrent, uncommitted changes throughout validation. Results describe the files observed by each invocation, not one immutable revision. HEAD was `d944948e211e1cb95943ff7a7faf16e00c521bf9`. No production source was edited during this validation pass, and no parallel changes were reverted.

## Results

| Check | Result |
| --- | --- |
| Backend unit + eval + benchmark collection | 3,394 passed, 1 skipped; 49.71 seconds |
| Same suites with branch coverage | 3,394 passed, 1 skipped; 103.87 seconds |
| Real-engine integration suite | 156 passed, 2 skipped; 338.42 seconds |
| Two new real-engine semantic regressions | 2 passed; 1.84 seconds |
| Assistant real-engine benchmark, explicitly enabled | 1 failed; no valid latency result |
| Frontend tests, including Vitest browser components | 88 files, 684 tests passed; 63.75 seconds |
| Frontend TypeScript + Vite production build | Passed; large bundle warning remains |
| Frontend ESLint | 0 errors, 22 warnings |
| Frontend Prettier check | Failed: 306 files require formatting |
| Backend Ruff lint | Failed: 128 errors |
| Backend Ruff format check | Failed: 135 files require formatting, 321 unchanged |
| Backend mypy | Failed: 135 errors in 57 files; 297 source files checked |
| Pinned grammar drift check | Passed for both grammars |
| ANTLR 4.13.2 regeneration into a temporary directory | All three generated Python files identical to checked-in files |

Initial restricted runs failed to bind localhost sockets/access Docker; the main backend and frontend suites were rerun with local test access. An initial ML worker startup timeout did not recur in the complete rerun or the coverage run. These initial failures were not counted as successful tests.

### Skips and benchmark failure

The offline benchmark skip was the engine round-trip test, because no checkout-owned test engine was available at its default port. It was then explicitly run against the disposable engine and **failed**.

Its `LoopContext.user` fixture supplies a username and encrypted password but omits the authenticated role fields. The execution path resolves `SecurityContext.from_session(...)`; the disposable engine audit recorded `SecurityContextError`, and the test failed its `outcome.ok` assertion. The fixture uses root, which is also unsuitable for the hardened user-session boundary. This should be corrected in the test using a valid authenticated test user, without weakening production security checks.

The integration suite reported these two skips verbatim:

- `test_function_detail_is_reconstructable_and_runs`: engine has UDFs disabled (`enable_udf=false`).
- `test_plan_blocks_objects_without_definition`: engine does not support `CREATE TASK`.

The latter is the test's reported reason, not a general claim that this engine cannot run tasks: the separate native task lifecycle tests passed in the same run. Its skip condition merits follow-up.

### Static-check findings

The lint/format/type failures span the repository; they are not all attributed to the semantic work. A read-only HEAD export had 123 Ruff errors versus the working checkout's 128. Because parallel edits were active, this is not an attribution of five errors to any particular task.

Notable type failures in the working checkout included invariant dictionary argument types in `agents/tools/semantic_query.py` and incompatible inferred payload types in `assistant/service.py`. Other failures include missing dependency stubs and existing module typing issues. No bulk formatter or automatic lint fix was run.

A fresh-cache baseline mypy run followed generated parser imports and produced 2,678 errors; its configuration/cache behavior was not comparable to the working run, so it is not used to infer a regression count.

## Coverage and scope

Coverage XML reports 31,937 / 69,599 executable lines (**45.89%**) and 4,518 / 12,794 branches (**35.31%**). Excluding generated `sql_dialect/grammar/StarRocks*` files gives 18,000 / 25,825 lines (**69.70%**) and 3,780 / 6,818 branches (**55.44%**). These figures are from the offline suite, not merged integration coverage.

Integration covered actual StarRocks 4.1.4, catalog and migration operations, MySQL CLI/proxy paths, Arrow/ML execution, task scheduling, restart behavior, credentials, and RBAC. New tests in `backend/tests/integration/test_semantic_engine_hardening.py` verify:

- A compiled Surabaya month/YoY query returns current revenue 10 and prior-year revenue 20, excluding an intervening month's 900 and another city's 50.
- Logical metric and relationship fields map to physical columns and produce one West-region aggregate of 980 for the test fixture.

The frontend tests are browser component tests, not a complete deployed-app route/login/interaction E2E suite. No live paid LLM/provider calls, production data tests, sustained load/soak tests, or exhaustive browser/device matrix were run. Coverage gaps remain, so “fully tested” cannot mean every possible behavior is proven.

## Isolation and reproduction

Tests used disposable Compose project `nova-qa-20260923`, never the user's running Nova stack. Dedicated ports: FE MySQL 49030, FE HTTP 48030, Arrow 49408, object storage 49000/49001, Redis 46379. Seeding ran only against these ports/project. The seed script's hardcoded network name was substituted at shell invocation time, without modifying the script. Use fresh CI-only signing/encryption keys when reproducing; do not use production credentials.

After all engine tests completed, `docker compose -p nova-qa-20260923 -f docker-compose.test.yml down --volumes` removed the four disposable containers and their network. Disposable test data was not retained. A subsequent container listing confirmed the user's original Nova, Ranger, migration-source, Redis, and object-storage containers remained running and healthy.

Main commands, from `backend/` unless noted:

```sh
.venv/bin/pytest -q tests/unit tests/eval tests/benchmark
.venv/bin/pytest -q tests/unit tests/eval tests/benchmark --cov=app --cov-branch
.venv/bin/pytest tests/integration -v -rs --tb=short
.venv/bin/pytest -q tests/integration/test_semantic_engine_hardening.py
.venv/bin/pytest -q -s tests/benchmark/test_assistant_engine.py
.venv/bin/ruff check app tests --output-format concise
.venv/bin/ruff format --check app tests
.venv/bin/mypy app
.venv/bin/python scripts/check_grammar_drift.py
```

The integration commands require `COMPOSE_PROJECT_NAME`, matching `NOVA_TEST_*` port variables, `STARROCKS_*`, Redis and storage endpoints pointing to the isolated project, `RANGER_ENABLED=false`, and `NOVA_PROXY_E2E_NETWORK=nova-qa-20260923_default`. The benchmark additionally requires `NOVA_ORCH_SR_PORT=49030`.

From `frontend/`: `npm test -- --reporter=dot`, `npm run build`, `npm run lint`, and `npm run format:check`.

Local machine-readable artifacts (temporary; not committed):

- `/private/tmp/nova-full-offline-unrestricted-20260923.xml`
- `/private/tmp/nova-full-coverage-tests-20260923.xml`
- `/private/tmp/nova-full-coverage-20260923.xml`
- `/private/tmp/nova-full-integration-20260923.xml`
- `/private/tmp/nova-semantic-engine-20260923.xml`
- `/private/tmp/nova-engine-benchmark-20260923.xml`

## Follow-up gates

1. Repair the engine benchmark's authenticated-session fixture and rerun it.
2. Triage static-check failures against the owners of concurrent edits; avoid repository-wide formatting churn.
3. Resolve the two integration skips with explicit capability checks/configuration.
4. Add deployed-app E2E coverage and controlled live-provider validation before a production-readiness claim.
