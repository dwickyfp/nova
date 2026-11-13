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

## Adding a surface or app

- Add ordinary product screens through the existing router and feature owners.
  If the user explicitly requests a separate app, define its navigation and
  deployment boundary while reusing Nova's authenticated backend contracts,
  authorization and domain services. A new shell does not create a new source
  of permission or a second metadata write path.
- Resolve API ownership and request/response contracts before wiring UI actions.
  Preserve opaque IDs and compatibility; do not guess whether a prepared PG
  service is active from its filename or migration revision.
- Represent conflict (409), maintenance refusal (503), queued work, cancellation
  and recovery states when the API exposes them. Keep edits recoverable after a
  conflict and do not display success before the owning operation confirms it.
  Keep infrastructure details out of user-facing errors.
- Use the existing security-context cache/reset path when principal, role or
  context version changes. Discard stale in-flight results and prevent previous
  user/role data from reappearing after navigation, reconnect or role switching.
- Ship the route, real actions, loading/empty/error states, authorization behavior,
  browser checks and matching docs together. A screenshot, mock or hidden route
  is not evidence of a complete feature.

## User documentation

- `/docs` is a standalone surface (its own layout, like Studio) rendered from
  Markdown in `src/features/docs/content/`; sections are registered in
  `src/features/docs/nav.ts`. Do not add a docs framework or a second app.
- A UI change that alters labels, steps, or behavior a docs page describes
  updates that page in the same change, following the
  [root documentation contract](../AGENTS.md#product-behavior-and-secrets).
  `src/features/docs/content.test.ts` checks front matter, internal links,
  code-block languages, and forbidden storage/API terms for every page.

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
