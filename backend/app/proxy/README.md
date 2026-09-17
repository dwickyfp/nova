# Nova MySQL Protocol Proxy

Any MySQL client — the `mysql` CLI, JDBC, a Python driver, dbt — can connect to
Nova on port **4406** and use Nova's `@stage` dialect transparently.

```
mysql --host=127.0.0.1 --port=4406 --user=nova_admin --password=... --execute="SELECT * FROM @products.products_new.csv LIMIT 5"
```

## How to run it

**Embedded in the API** (the default). The FastAPI lifespan starts the proxy
whenever `PROXY_ENABLED` is true, so running the backend is enough:

```bash
cd backend
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Standalone**, for deployments that split the proxy into its own process
(`docs/arch-07-mysql-proxy.md` shows the compose shape):

```bash
cd backend
PROXY_ENABLED=true uv run python -m app.proxy
```

Standalone mode initialises the system connection pool itself and shuts down
cleanly on `SIGINT`/`SIGTERM`, draining in-flight queries before it exits.

## Configuration

Environment variables (all optional; defaults match the `proxy:` block in
`docker/nova.yaml`):

| Variable | Default | Meaning |
|---|---|---|
| `PROXY_ENABLED` | `true` | Start the proxy with the API lifespan |
| `PROXY_HOST` | `0.0.0.0` | Bind address |
| `PROXY_PORT` | `4406` | Bind port |
| `PROXY_MAX_CONNECTIONS` | `100` | Concurrent client limit; connections past it are refused at accept time |
| `PROXY_CONNECT_TIMEOUT` | `10` | Seconds to wait for a StarRocks login |
| `PROXY_READ_TIMEOUT` | `300` | Seconds of client silence before the connection is closed |

The proxy shares `STARROCKS_HOST` / `STARROCKS_FE_MYSQL_PORT` with the rest of
the backend — that is where it authenticates.

## What it does

| Statement | Behaviour |
|---|---|
| `SELECT * FROM @stage.file.csv` | Rewritten to `FILES()` with injected credentials, then executed |
| `SELECT ...` / DDL / DML | Executed through `QueryService`, unmodified |
| `SHOW DATABASES` | Executed, then `NOVA_SYSTEM`, `information_schema`, `sys`, `_statistics_` are filtered out |
| `USE <db>` | Tracked in the proxy's session and applied to the engine connection |
| `SET @var = value` | Tracked in the proxy's session; later `@var` references are substituted before the statement reaches the engine. Never sent to the engine as a `SET` |
| `SET ROLE <role>` | Tracked in the proxy's session and applied to each subsequent query |
| `DROP ROLE ACCOUNTADMIN` / `REVOKE ... ACCOUNTADMIN` | Refused by the guard in `QueryService` |

Every statement goes through `QueryService.execute_statements`, so the
`@stage` → `FILES()` translation, credential injection, the ACCOUNTADMIN guard
and the audit write are the *same code* the HTTP API uses. The proxy adds
transport, session tracking and nothing else.

### User variables

`SET @x = 1` is stored on the session and `@x` is substituted into later
statements by `session.substitute_user_variables`, which is what drivers and
ORMs (`pymysql`, SQLAlchemy, dbt, MySQL Shell) rely on to keep a value across
queries on one connection. The substitution is a tokenizer, not a regex pass, so
it only rewrites references that are really references:

* `'@x'` inside a string literal is data and is left alone;
* `-- @x` and `/* @x */` are comments and are left alone;
* `@@version` is a system variable and is left to the engine;
* `@stage1.data.csv`, and `@stage1` where the position makes it a stage
  (`FROM`/`JOIN`/`INTO`/`LIST`), are stage references and are left to the dialect
  engine — **even when a session variable has the same name.**

A reference with no stored value is left verbatim, and the engine answers for an
unset variable in its own way (`NULL`).

The last rule is decided by the *same* classifier the dialect engine uses
(`parser._classify_at_token`), not by the presence of a dot. A name that is both
a session variable and a stage is ambiguous, and the stage wins: substituting
the variable would rewrite `SELECT * FROM @stage1` into `SELECT * FROM 'CSV_FILE'`
and lose the stage, while leaving it lets the client's stage resolve. An earlier
revision read the stored value first and only checked for a dot afterwards, so
`SET @stage1 = 1; SELECT * FROM @stage1` substituted — the defect fixed here.

### Stage or variable: context decides

`@x` and `@stage1` are spelled the same way, so the dialect engine classifies
each `@name` by **position**, not by the presence of a dot:

* a stage is introduced by `FROM`, `JOIN`, `INTO` or `LIST`, or is written with
  a dotted path or a trailing `/` — `SELECT * FROM @stage1`,
  `LIST FILES @stage1`, `@stage1/`, `@stage1.data.csv`;
* a variable is an expression operand — after an operator, comma or opening
  paren — so `SELECT @x`, `1 + @n` and `SET @my_stage = 1` are all variables,
  including when the name happens to match a stage.

Getting this wrong in either direction is a silent failure: reading `@x` as a
stage breaks `SET @x; SELECT @x`, and reading `@stage1` as a variable sends
`SELECT * FROM @stage1` to the engine untouched. Comments are skipped when the
classification happens, because the engine skips them too.

## Authentication

Nova has one source of truth for credentials: StarRocks. The proxy does not add
a second, and it never sees a password.

It reads StarRocks' own `HandshakeV10`, presents that scramble to the client as
its own, and forwards the client's response back on the same upstream socket.
StarRocks — the only holder of the password hash — decides. A fresh upstream
login per client connection gives a fresh scramble, which is what makes each
relayed response single-use.

The alternatives do not work, and this is measured rather than assumed:

* **Sending Nova's own `mysql_native_password` challenge and reading a cleartext
  password** fails because modern clients never send one. Against `mysql` 8.0.46
  the handshake response carries a 20-byte scramble hash in every capability
  combination, including with `CLIENT_PLUGIN_AUTH` unset; the client sets that
  flag regardless of what the server advertises.
* **Recovering the password from the response** is impossible by construction —
  it is a one-way function.
* **Relaying a `caching_sha2_password` challenge** is impossible without a
  server-side RSA key: the response cannot be verified by a party that does not
  hold the hash.

`app/proxy/auth.py` documents each of these at the point it matters, and
`parse_starrocks_handshake` fails closed if StarRocks ever offers a plugin other
than `mysql_native_password` — a misconfiguration surfaces as a configuration
error, not as a wrong-password failure.

## Security notes

* **TLS.** The proxy speaks plaintext, and it deliberately does not advertise
  `CLIENT_SSL` so a client cannot believe it can upgrade mid-handshake. In
  production, terminate TLS in front of port 4406 (the nginx `stream` block in
  `docs/arch-07-mysql-proxy.md` is the intended shape). The relay itself is safe
  over plaintext — a challenge/response pair carries no secret — but the queries
  and rows are not.
* **Credentials never leave the process.** Storage credentials are injected into
  `FILES()` for the engine and redacted everywhere else: the audit row, the
  result set, and engine error messages are all run through
  `redact_sql_credentials` before they reach the client.
* **No password is stored, logged or transmitted by Nova.** See the
  authentication section above.

## Layout

| File | Responsibility |
|---|---|
| `protocol.py` | Wire codec: framing, handshake, OK/ERR/EOF, result sets. No I/O. |
| `auth.py` | The StarRocks challenge relay and upstream handshake. |
| `session.py` | Per-connection `SET`/`USE`/database context, statement splitting. |
| `executor.py` | Bridges statements to `QueryService` and results back to the wire. |
| `connection.py` | One client connection, handshake to `COM_QUIT`. |
| `server.py` | Accept loop, connection limit, lifecycle. |
| `__main__.py` | Standalone process entry point. |

## Limitations

* **`LIST` is not implemented.** Nova parses `LIST [FILES] @stage` and resolves
  the stage reference — so the statement is not silently passed through with the
  reference intact — but nothing executes it, and **StarRocks has no `LIST`
  statement** (measured: every form is a syntax error at the engine). The
  statement therefore fails with the engine's `LIST` syntax error. Browse-stage
  is a Nova-side feature that needs its own implementation; until then the
  documented syntax is recognised and refused rather than misinterpreted.

  One known wart inside that dead end: `translate_stage_query` substitutes only
  the `@stage1` text, so the written-out `LIST FILES @stage1` becomes
  `LIST FILES FILES(...)` — the literal `FILES` keyword is left in place. It
  costs nothing today because the statement cannot execute either way, and it is
  the translator's business rather than the parser's. It is recorded here so the
  `LIST` implementation finds it instead of rediscovering it.
* **`@stage1/folder/x.csv` (slash paths) and globs (`@stage1.data/*.csv`) are
  not detected.** Both are listed as open defects in `README.md` (SQL dialect),
  and both predate the proxy.
* **Prepared statements (`COM_STMT_PREPARE`) are not supported.** Clients that
  use them get `ER_NOT_SUPPORTED_YET` (1235), which is the code drivers
  interpret as "fall back to the text protocol". The `mysql` CLI, JDBC's
  `useServerPrepStmts=false`, and pymysql all work today.
* **Sequential per connection.** MySQL's text protocol carries one in-flight
  command per connection; concurrency comes from more connections, bounded by
  `PROXY_MAX_CONNECTIONS`.
* **No connection pooling to StarRocks.** Each client connection holds one
  upstream session. Full pooling and richer session tracking are separate
  roadmap items.
* **`SET @@global.x` is refused** rather than accepted and ignored — a client
  that believes it changed a server-wide setting and did not is worse off than
  one that gets an error.
* **A multi-statement script returns the last statement's result.** The text
  protocol cannot express several result sets without
  `CLIENT_MULTI_RESULTS` bookkeeping that the proxy does not implement; the
  first error still wins.
* **Integer columns are reported as `BIGINT` regardless of their real width.**
  `_column_definition` infers the type from the Python value, and
  `QueryResult` carries column names and values but not the engine's declared
  type. A `TINYINT` column therefore arrives with type code `0x08` where the
  engine reports `0x01`. Both decode to `int` in every client, so this affects
  metadata fidelity rather than values; reporting the true width needs the type
  code carried through `QueryResult`.
* **An unset user variable is passed through, not rejected.** `SELECT @never_set`
  reaches the engine and returns `NULL`, which is what StarRocks itself does.

## Tests

```bash
cd backend
uv run pytest tests/unit/test_mysql_proxy_protocol.py \
              tests/unit/test_mysql_proxy_session.py \
              tests/unit/test_mysql_proxy_executor.py \
              tests/unit/test_mysql_proxy_auth.py
```

The unit tests need no engine, no Docker and no network — `protocol.py` is pure
bytes, and the other layers fake their collaborators.

The end-to-end suite drives a **real** `mysql` CLI against a **real** proxy,
StarRocks and MinIO. It needs Docker and a running engine, and skips cleanly
without them:

```bash
uv run pytest tests/integration/test_mysql_proxy_cli.py -v
```

Override the target with `NOVA_PROXY_E2E_HOST`, `NOVA_PROXY_E2E_USER`,
`NOVA_PROXY_E2E_PASSWORD`, `NOVA_PROXY_E2E_DATABASE` and `NOVA_PROXY_E2E_STAGE`.
