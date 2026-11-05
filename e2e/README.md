# Nova e2e

End-to-end tests with [`e2e`](https://github.com/tester-army/e2e) against a
running governed Nova stack (patched StarRocks FE + Ranger). Design and
spike results: `docs/proposals/nova-e2e-framework.md`.

## What the suite covers

| File | What it proves | Targets |
|---|---|---|
| `tests/governance.e2e.ts` | Ranger row filter per user (alice: Jakarta, bob: Bandung), `regional_manager` sees both, `finance` is denied, phones masked for `marketing` and raw for `regional_manager`. Uses the SQL API | Chromium, Lightpanda |
| `tests/explorer.e2e.ts` | Database Explorer lists only what the active role can see (UI and API) | Chromium, Lightpanda |
| `tests/roles.e2e.ts` | Switching roles from the account menu (`layout`) | Chromium |
| `tests/worksheet.e2e.ts` | Monaco worksheet: create a file, run SQL, only permitted rows, masked phones (`layout`) | Chromium |
| `tests/studio.e2e.ts` | Studio's answer stays inside the role's row filter, read by an agent step and checked against the SQL API (`agentic`, `layout`) | Chromium, when a model is set |

Lightpanda has no layout engine, so it runs only tests without the `layout`
tag.

## Personas

The personas are the stack's own seed: `docker/init-nova.sql` creates
`nova_admin`, `alice` (marketing, finance, regional_manager), and `bob`
(marketing) with the `analytics.sales` / `analytics.customers` data, and
`docker/ranger/bootstrap.py` creates their Ranger roles, city scopes, row
filters, and masks. The suite creates no users or policies of its own.

Passwords come from the environment only:

| Variable | Value |
|---|---|
| `NOVA_E2E_ADMIN_PASSWORD` | `nova_admin`'s password on this stack (the seed value on a fresh stack) |
| `NOVA_E2E_ALICE_PASSWORD` | `alice`'s seed password |
| `NOVA_E2E_BOB_PASSWORD` | `bob`'s seed password |

## Run locally

Reuse the running `docker` compose stack. Serve the frontend on
`127.0.0.1:5182` (override with `NOVA_E2E_URL`), for example the production
build the way CI does:

```bash
cd frontend && pnpm build && pnpm exec vite preview --port 5182 --strictPort --host 127.0.0.1
```

Then, from `e2e/`:

```bash
pnpm install --frozen-lockfile
node node_modules/playwright-core/cli.js install chromium
pnpm e2e:chromium
```

Lightpanda runs in a container on the host network, on a port no other
browser uses (a local Chrome often holds 9222):

```bash
docker run -d --name nova-e2e-lightpanda --network host \
  -e LIGHTPANDA_DISABLE_TELEMETRY=true lightpanda/browser:nightly \
  /bin/lightpanda serve --host 127.0.0.1 --port 9333
pnpm e2e:lightpanda
```

Every script sets `E2E_TELEMETRY_DISABLED=1`.

## The Studio (agentic) test

It is skipped unless all of these hold:

- `NOVA_E2E_MODEL_BASE_URL`, `NOVA_E2E_MODEL_ID`, and
  `NOVA_E2E_MODEL_API_KEY` name an OpenAI-compatible endpoint, the same
  provider Nova uses. The e2e agent reads Studio's answer with it. Give the
  key through the environment; never read it back from Nova.
- Nova itself has a model provider configured (AI Providers).
- The agent worker runs (`cd backend && python -m app.agent_worker`).
  Studio's default Smart mode queues runs for it; without it the answer never
  starts.

Each run spends tokens on both Nova's and the e2e agent's model calls.

## CI

The `e2e` job in `.github/workflows/ci.yml` builds the governed stack from
`docker/docker-compose-engine.yml` with disposable credentials, serves the
built frontend with `vite preview`, and runs Chromium, then Lightpanda. It
reads the persona passwords from the seed so the two cannot drift apart.
On a fresh stack it also runs `minio-init` (nothing depends on it, but it
creates the buckets workspace files need) and completes `nova_admin`'s
first-login setup with a throwaway password.
The Studio test skips there: CI has no model provider or agent worker.
Reports are uploaded as the `e2e-reports` artifact.
