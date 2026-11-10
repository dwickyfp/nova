# Nova Notebook: checkpoints and evidence

Tracker for the Nova Notebook build on `feat/nova-notebook`: one checkpoint per
phase, with the versions it ran on and the evidence behind each result.
Design and scope: [spec](../../proposals/nova-notebook-spec.md); current
architecture: [arch-20](../../arch-20-nova-notebook.md) (written in phase 8).

## Pinned versions

| Component | Version | Pin |
|---|---|---|
| OpenSandbox server | `release-1.2.0` | `opensandbox/server@sha256:25bebdcf…6f30` |
| OpenSandbox execd (injected) | `release-1.2.0` | `opensandbox/execd@sha256:c75836c4…526c` |
| OpenSandbox egress sidecar | `release-1.2.0` | `opensandbox/egress@sha256:a3d30246…f54d` |
| Python SDK | `opensandbox==1.2.0`, `opensandbox-code-interpreter==1.2.0` | `backend/uv.lock` |
| Runtime base | `python:3.12-slim` (3.12.16) | `sha256:5e2bdb1b…a5b` |
| DuckDB | 2.0.0, extensions `mysql_scanner f7fc0d4271`, `parquet`, `json` | `docker/notebook-runtime/requirements.lock` |
| Kernel stack | `jupyter_server 2.21.1`, `ipykernel 7.4.0` | same lock |

The lock is universal and hash-pinned (`docker/notebook-runtime/lock.sh`).
The local image `nova-notebook-runtime:dev` is about 1.18 GB, against about
2.6 GB for the official multi-language code-interpreter image.

## Phase 0: upstream spike (2026-11-10)

Host: macOS, OrbStack (8 CPU, 7.8 GiB VM, about 4.3 GiB already used by the Nova stack).
Script: `backend/scripts/notebook_runtime_spike.py`, run against the
`opensandbox-server` compose service.

| Check | Result | Consequence |
|---|---|---|
| 3 sandboxes created at once, one server | Pass. 3 distinct sandbox ids, each ready in about 4 s | One container per runtime works |
| Persistent Python context | Pass. `x` survives between runs | One context per runtime |
| Isolation | Pass. Sandbox B does not see A's `x` | — |
| DuckDB default connection | Pass. `duckdb.set_default_connection` | Bootstrap sets `db` and the default |
| stdout/stderr streaming | Pass. Interleaved stream events with timestamps | Live output |
| `execution_complete` ordering | **Can arrive before trailing stdout events** | The adapter reads to the end of the stream; it never stops at `execution_complete` |
| Error | Pass. `ZeroDivisionError` with traceback | — |
| Rich MIME | **execd forwards only `execute_result` bundles** (`text/plain`, `text/html` seen). `display_data`, including matplotlib PNG and `display(..., raw=True)`, never arrives | Nova captures displays in the kernel (`nova_runtime.display`) and returns them as the bundle of a trusted follow-up expression |
| Python SDK result | `ExecutionResult` keeps only `text` | The provider reads raw execd events, keeping every MIME key |
| Interrupt | Cancelling the HTTP stream does **not** stop the code; the next run fails with "session is busy" | Cancel calls execd interrupt with the execution id from the `init` event |
| Metrics | Pass. `cpu_count=1`, `memory_total_in_mib=1024` follow the request | CPU/RAM meter from `get_metrics` |
| Kernel spec | execd **skips a kernelspec named `python3`** and needs another Python kernelspec | The image installs `nova-python` |
| Image pulls | The first create with egress timed out while the server pulled the sidecar image | Images are pulled ahead (`HOW_TO_RUN.md`) |
| User | `uid=1000(nova)`, no Docker socket | — |
| Egress, `dns` mode | **IP rules not enforced**: host Redis (6379) reachable | Server runs `dns+nft` |
| Egress, `dns+nft` | Pass. Deny by default; `0.250.250.254/32:4406` open; host 6379 and 8000 and `1.1.1.1:443` blocked; PyPI opened and closed at runtime with `patch_egress_rules`/`delete_egress_rules` | Only the Nova proxy is reachable; PyPI opens only for an approved install |
| Host name inside a sandbox | Under deny-by-default, DNS for `host.docker.internal` is blocked; the gateway IP is reachable | The runtime connects to the proxy by IP |

### DuckDB to the Nova MySQL proxy

Probe: DuckDB 2.0 `mysql` extension in the runtime image, to the dev proxy on 4406, as `nova_admin`.

| Check | Before | After the proxy change |
|---|---|---|
| Login (`mysql_native_password` through the relay) | Pass | Pass |
| `ATTACH '' AS nova (TYPE mysql, READ_ONLY)` metadata (schemata, tables) | Pass | Pass |
| `START TRANSACTION READ ONLY` (sent for `READ_ONLY` attach) | **Fail**: the Nova SQL frontend rejects it | Pass: answered by the proxy |
| Table scan `SELECT * FROM nova."NOVA_DEMO"."order_items"` | **Fail**: returns `-1`; the scanner binds result columns at prepare time and the proxy announced none | Pass: rows returned |
| `mysql_query('nova', …)` full pushdown | **Fail**: `-1` | Pass: `[('ACCOUNTADMIN', 2)]`; runs as the session's active role |
| Secret visibility | — | `duckdb_secrets()` shows `password=redacted` |

Proxy changes, with tests in `backend/tests/unit/test_mysql_proxy_readonly_and_describe.py`:
- `COM_STMT_PREPARE` announces a query's columns, described through the governed pipeline as `SELECT * FROM (…) LIMIT 0`. Anything that is not a plain read describes nothing.
- `START TRANSACTION READ ONLY` is answered by the proxy. It refuses writes with MySQL error 1792 until `COMMIT`/`ROLLBACK`.

Gate: **passed**. The native path (`ATTACH` to the governed proxy) is the design; no Arrow gateway fallback is needed.

Lesson: the probe's first draft built a SQL literal without escaping, and the engine error echoed the documented default password. Every literal the runtime builds is now escaped, and error text is masked.

## Next checkpoints

- Phase 1: notebook documents in Workspaces
- Phase 2: runtime service, queue, worker, SSE
- Phase 3: `nova_runtime`
- Phase 4: notebook tokens (governed, read-only), Ranger two-role acceptance
- Phase 5: packages and files
- Phase 6: notebook UI
- Phase 7: Neva and Dataviz
- Phase 8: live acceptance demo
