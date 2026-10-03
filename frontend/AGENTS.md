# Frontend Agent Guide

Inherit the [root contracts](../AGENTS.md). Paths below are repository-relative;
commands run from `frontend/`.

## Stack and ownership

- Use the existing React/Vite app and TanStack Router. Route files live in
  `frontend/src/routes/`; feature UI lives in `frontend/src/features/`.
  Route-tree generation belongs to the configured router plugin: do not
  hand-edit `frontend/src/routeTree.gen.ts` or add a parallel router.
- `frontend/package.json`, `frontend/pnpm-lock.yaml`, and
  `frontend/vite.config.ts` define dependencies, scripts, aliasing, and tests.
  Use TanStack Query for existing server-state patterns, Zustand for existing
  client-state patterns, and shared React components/hooks.
- API access goes through `frontend/src/lib/api-client.ts` and its existing
  domain helpers. Preserve authentication/reset, error, cancellation, and
  security-scoped cache behavior; do not create a competing client.
- Reuse shadcn/Radix components and current Monaco integration. Keep generated
  parser/route artifacts and backend implementation details out of user flows.
- Local development defaults to Vite on 5173 with `/api` proxied to backend
  8000. Deployment bindings are configuration, not a reason to hardcode URLs.
- User-visible file access uses stages and configured connection names. Do not
  expose vendors, physical storage endpoints, or stored credentials. Masked
  secret status is distinct from intentional write-only credential entry.

## Design and layout

Read [DESIGN.md](../DESIGN.md) first. Its Nova tokens, surfaces, icon meanings,
and lint gates take precedence over generic design-quality advice. Reuse the
existing theme; do not add a parallel palette, theme, or component system.

For route/page-shell/scroll changes, read the
[Nova page-layout skill](../skills/nova-page-layout/SKILL.md). Console pages
retain a visible header and viewport-bounded content. Use the established
`Main` modes and flex/minimum-size chain; long content scrolls inside its owning
region while headers/toolbars remain outside. Use `overflow-x-auto` for tables
or code; do not fix page sizing with hardcoded heights.

Load [antislop core](../.agents/skills/antislop/SKILL.md) with only the relevant
concern: [UI](../.agents/skills/antislop-ui/SKILL.md),
[copy](../.agents/skills/antislop-copywriting/SKILL.md),
[accessibility](../.agents/skills/antislop-human/SKILL.md), or
[responsive layout](../.agents/skills/antislop-layoutmobile/SKILL.md).
Apply guidance during clear implementation work; explicit audits use audit
mode. Do not stop to ask which mode applies. Nova design and layout contracts
remain authoritative when generic skill advice conflicts.

Verify loading, empty, error, populated, and detail states; visible actions must
work. Check keyboard/focus, both themes, short/long content, desktop and narrow
widths, and assistant-open state where supported. Shared-layout edits require
checking affected consumers, not only one page.

## Validation

Use Node/pnpm versions pinned in [CI](../.github/workflows/ci.yml) and the locked
dependency/browser setup. Required frontend code checks:

```bash
pnpm install --frozen-lockfile
pnpm exec playwright install --with-deps chromium
pnpm lint
pnpm build
pnpm test:coverage
```

Vitest uses Playwright/Chromium for component/browser tests and a separate Node
project for lint-rule tests. A mocked browser test does not prove real API or
Ranger behavior. Add focused tests for meaningful behavior changes; verify
reversible copy/style changes through applicable checks and visual inspection
without inventing tests that merely mirror markup. Documentation-only edits
follow the root documentation-validation route.
