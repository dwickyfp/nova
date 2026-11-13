# Sign-in phoenix transition

Date: 2026-11-13. Branch: `feat/qa-test`. Base: `bf861e75`.

## Delivered behavior

Successful sign-in displays a full-viewport phoenix before navigating to the
existing safe redirect destination. The same transition follows first-time
password setup and an administrator-required password change, after the second
login authenticates. Errors keep the form available. Existing sessions and
ordinary page navigation do not replay the splash.

The existing `Logo` asset is composed into a body and two clipped wings. Two
800 ms wingbeats play before navigation; reduced motion uses a static phoenix
and a 200 ms transition. Unmounting cancels the pending navigation timer.
The status receives programmatic focus, and the form controls unmount during
the transition. Authentication, role selection, and session persistence retain
their existing owners.

## Design decisions

Reading: a brief sign-in transition for Nova users, in the existing calm Nova
visual language. ENERGY 1 / RHYTHM 1 / MOTION 2.

- The existing phoenix connects the transition to the sign-in screen.
- The solid theme background gives the bird one uninterrupted focal area.
- Inter and semantic text colors retain the application's typography and themes.
- Centering and whitespace isolate the short transition from the login form.
- Wing movement communicates the transition requested by the user; two cycles
  limit motion to 1.6 seconds, including when navigation takes longer.
- Viewport-relative image limits and safe centering preserve content at 200%
  text size and short viewport heights.

## Validation evidence

No container, backend, authentication policy, or deployment configuration changed.
Browser flow checks intercepted only the login response with a fixture. These
checks prove UI behavior, not live StarRocks or Ranger authentication.
The existing Vite process serves this checkout on port 5173.

Commands run from `frontend/`, unless stated otherwise:

| Check | Result |
| --- | --- |
| `npx --yes pnpm@11.8.0 install --frozen-lockfile` | Initial attempt failed: host Node 22.12.0 is older than pnpm's Node 22.13 minimum. |
| `npx --yes --package=node@22 --package=pnpm@11.8.0 --call 'node --version && pnpm install --frozen-lockfile && pnpm exec playwright install --with-deps chromium'` | Passed using Node 22.23.5 and pnpm 11.8.0; lockfile unchanged. |
| `pnpm exec vitest run --browser.headless src/features/auth/sign-in src/features/docs/content.test.ts` | Initial focused run: 4 files, 26 tests passed. |
| `npx --yes --package=node@22 --package=pnpm@11.8.0 --call 'pnpm ui:check'` | Passed. |
| `npx --yes --package=node@22 --package=pnpm@11.8.0 --call 'pnpm test:coverage'` | Superseded, not a pass: one AI Providers browser case failed while the concurrent build and default test workers created memory pressure. Stopped the owned runner with SIGINT (exit 130); log retained at `/tmp/nova-splash-coverage.log`. |
| `npx --yes --package=node@22 --package=pnpm@11.8.0 --call 'pnpm test:coverage --maxWorkers=2'` | Passed: 197 files, 1,652 tests, no skips, exit 0. Includes the vertical-bounds regression and AI Providers. Log: `/tmp/nova-splash-coverage-retry.log`. |
| `npx --yes --package=node@22 --package=pnpm@11.8.0 --call 'pnpm lint && pnpm build'` | Final source passed, exit 0. Lint: 0 errors, 31 warnings in unchanged files. TypeScript and production Vite build passed. Log: `/tmp/nova-splash-final-checks.log`. |
| `git diff --check` | Passed. |

The passing coverage run reports 58.13% statements, 56.13% branches, 52.77%
functions, and 59% lines over the application's configured source scope.
BI-query and overlapping-act console diagnostics appeared in
the suite; the isolated sign-in browser flows produced no page errors.

The first 200% text-size screenshot exposed vertical clipping despite passing
the horizontal overflow check. Image sizing and safe alignment were corrected;
the regression test now checks the vertical bounds of both content groups.

Browser inspection covered 1440 x 900 light/dark, 320 x 480 light,
768 x 600 dark, and 390 x 844 dark with reduced motion. All five cases loaded
the phoenix, covered the viewport without horizontal overflow, moved focus to
the status, navigated to the requested docs page, rendered the new documentation,
and produced no page errors. Reduced motion had zero splash animations.

Keyboard Enter submission, 200% text size, and forced colors were also inspected.
Measured contrast against the splash background: title 14.63:1 light / 18.09:1
dark; status 5.20:1 light / 7.97:1 dark. No Web Vitals claim is made.
Local screenshots and browser measurements are in `/tmp/nova-sign-in-splash/`.

## Delivery

The original checkout and `feat/qa-test` branch remain the delivery target.
Remote `main` was fetched and is already included; no integration was needed.
No worktree or supporting stack was created. The existing Vite process loads
the changed source directly. The user documentation was viewed at
`/docs/getting-started/sign-in-and-roles`; no SQL or Python example changed.

## UI delivery gate

Scope: the added splash and modified sign-in transition, not an audit of all Nova.

| Gate | Status and evidence |
| --- | --- |
| R-02, R-15, R-16 | PASS: new copy names the action and contains no marketing claims or decorative punctuation. |
| R-03 | PASS: narrow, tablet, desktop, and corrected 200% text-size layouts inspected; bounds checked. |
| R-17, R-18, R-36, R-38 | PASS: no metrics, testimonials, invented people, or security/performance claims added. |
| R-23, R-30, R-37 | PASS: user's requested Nova bird and full-screen transition; existing asset and DESIGN.md determine the design. |
| R-24, R-26, R-27 | PASS: Sign in validates empty fields, authentication failures retain the form, success opens the splash then the requested page; password actions also enter the transition. No new controls or links. |
| R-25 | PASS: computed contrast ratios above exceed AA. |
| R-28 | PASS: no FAQ added. |
| R-32, R-39 | PASS: keyboard Enter sign-in and focused status verified; no hidden form controls remain; reduced motion is static and shorter; animation stops after two cycles. |
| R-33 | PASS: component and CSS implemented directly in source with patches; no runtime script injection. |
| R-34 | PASS: light and dark screenshots inspected, with shared semantic tokens. |
| R-35 | PASS: built app and recorded sign-in click-through, redirect, docs, password flows, validation, and error behavior. |
| R-40 | PASS: existing sized logo images with high fetch priority; no new dependencies or third-party scripts. |
| R-01, R-04, R-06, R-07, R-08, R-09, R-10, R-12, R-13, R-14, R-19, R-22 | PASS: design reasons above; existing bird is the only accent, no extra ornament or controls, bounded wing movement. |
| Liveliness | PASS: declared 1/1/2 dials, one centered focal point, structural whitespace, Nova phoenix motif and accent. |
| C-1 through C-5; R-05, R-11, R-20, R-21, R-29, R-31 | PASS: intentional single-status layout, working transition, responsive states and both themes, no invented content or parallel styling system. |
