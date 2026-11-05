# Nova e2e

End-to-end tests with [`e2e`](https://github.com/tester-army/e2e) against a
running Nova stack. See `docs/proposals/nova-e2e-framework.md` for the design
and the Phase 0 spike results.

## Run locally

Reuse the running `docker` compose stack and a frontend dev server reachable on
`127.0.0.1` (default `http://127.0.0.1:5182`, override with `NOVA_E2E_URL`).

Passwords come from the environment only:

- `NOVA_E2E_ADMIN_PASSWORD`: the `nova_admin` test password.
- `NOVA_E2E_ALICE_PASSWORD`: the `alice` fixture password.

```bash
pnpm install --frozen-lockfile
pnpm e2e:chromium
```

Lightpanda has no layout engine, so it runs only tests without the `layout`
tag. Start it on the host network, on a port no other browser uses:

```bash
docker run -d --name nova-e2e-lightpanda --network host \
  -e LIGHTPANDA_DISABLE_TELEMETRY=true lightpanda/browser:nightly \
  /bin/lightpanda serve --host 127.0.0.1 --port 9333
pnpm e2e:lightpanda
```

Every script sets `E2E_TELEMETRY_DISABLED=1`.
