# Studio chat performance: history list, chart flicker, reopening threads (2026-11-08)

Three Studio problems were reported: the history list is slow with many threads,
charts disappear and reappear while rendering, and an old thread opens slowly
and settles its scroll position late. This report records what was measured,
what was changed, and the measured effect. Every number comes from the local
dev stack (StarRocks FE/BE, Ranger, Redis in the `docker` project) with the
`nova_admin` account and the SerbaData agent's real threads.

## Conditions

- The shared engine was saturated by another session's ontology evaluation
  during all runs (BE ~310% CPU, FE ~165%, host load average 12-17). Absolute
  times are therefore high and noisy. Every comparison interleaves before and
  after in random order so both see the same load; medians carry 95% bootstrap
  confidence intervals (4,000 resamples).
- Before: the base branch code (`origin/feat/supernova-ontology` at 84f2faf2),
  backend on port 8012 and frontend on 5175. After: this change, backend on
  8011 and frontend on 5174. Both backends run the `query` process role with
  the same environment.
- Browser measurements use headless Chromium driven by Playwright, so frames
  are rendered (a hidden desktop browser pane runs no animation frames and was
  not used for timing).

## 1. History list

### Where the time went

An in-process profile of `GET /agents/threads?limit=50` (3 distinct agents in
the page, one accessible):

| Step | Time |
|---|---|
| History query (`CONFIG_ASSISTANT_THREADS`, 51 rows) | 31-72 ms |
| Message counts (`CONFIG_ASSISTANT_MESSAGES`, one grouped query) | 23-55 ms |
| Agent access verification (`_require_agent` per distinct agent) | 2,359-3,755 ms |
| ... of which `effective_access` (21 calls) | 1,298-2,947 ms |

The list was already paged (50 per page, keyset cursor) and its payload is
small (4.6 KB for 15 visible threads), so pagination, projection and list
virtualization were not the bottleneck. Each `effective_access` call paged the
full Ranger policy set over REST, and one verification made 21 such calls in a
row. Opening a thread runs the same verification.

### Change

- `RangerClient.read_snapshot()` shares `list_policies` and
  `get_user_attributes` reads inside one verification. Reads are copied out,
  a write inside the scope clears them, failed reads are not remembered, and
  nothing outlives the request: every verification still reads Ranger's
  current state.
- `verify_access` checks an agent's independent dependencies concurrently,
  three at a time (each check opens its own StarRocks connection), and keeps
  them sequential when a delegated connection serves the caller. The first
  failing dependency in dependency order still decides.
- The thread list verifies its distinct agents concurrently (three at a time)
  under one snapshot. A missing or unverified agent still hides its threads;
  any other failure still fails the list.

The authorization outcome is unchanged: the same Ranger checks and the same
caller-identity `EXPLAIN` probes run, and one list call writes the same 13
audit rows before and after.

### Result

Interleaved API timing, n = 15 per side:

| Request | Before median (95% CI) | After median (95% CI) | Speedup (95% CI) |
|---|---|---|---|
| Thread list | 4,098 ms (3,660-5,687) | 1,841 ms (1,358-2,465) | 2.23x (1.73-3.54) |
| Open 22-message thread | 3,920 ms (2,721-6,409) | 1,717 ms (1,315-2,554) | 2.28x (1.24-4.16) |
| Open 24-message thread | 3,198 ms (2,606-4,429) | 1,555 ms (1,388-2,044) | 2.06x (1.39-2.81) |

Responses were byte-identical in size and count (list: 15 threads, 4,566
bytes; threads: 22 and 24 messages, 265,531 and 168,710 bytes).

In-process profile on the same data: list 3,810 ms to 919-1,029 ms; thread
open 2,018 ms to 693-698 ms (`effective_access` 2,947 ms to 266 ms).

In the browser (n = 5 per side) the list request fell from a median of
5,827 ms (CI 4,079-8,190) to 4,054 ms (CI 2,173-5,851); the intervals overlap
at this sample size and load.

## 2. Chart flicker

### Cause

- An interactive answer (`IntelligentUi`) keyed its session on the saved
  source. A streamed turn has no saved message until it completes, so at
  completion the key changed and the whole visual remounted, then showed its
  fallback text until its saved state loaded, then drew the chart again. A
  reopened thread drew every such visual through the same fallback-then-chart
  sequence. Every chat item also fades in from transparent when it mounts
  (`.nova-chat-item`), so a remount reads as the chart vanishing.
- The interactive chart's code is a lazy chunk; until it arrived the visual
  showed a one-line "Loading chart…" where a 292 px chart then appeared.
- Recharts' percentage `ResponsiveContainer` measures after mount and unmounts
  the chart whenever its box reads 0 wide.

### Change

- `IntelligentUi` keeps its session when a visual gains its saved source; a
  different program, security context or saved source still starts a new
  one. The visual draws from its defaults at once, its controls stay
  read-only until the saved state loads, and it redraws only if the saved
  state differs.
- The chart chunk starts loading while the answer streams, and its placeholder
  holds the chart's height.
- `ChartFrame` measures before paint, keeps the last real size through a
  transient 0, and passes fixed dimensions to Recharts.
- Studio turns get stable callbacks, so typing or a streamed token no longer
  re-renders every turn's charts.

### Result

- Component test (`renderer.test.tsx`): on the old code the chart is gone
  (`null`) right after the turn completes; on the new code the same SVG
  element stays mounted while saved state loads.
- Reopening the two chart threads in the browser, 8 interleaved opens per
  side: before, charts were removed and redrawn in **8 of 8** opens (16
  removals); after, **0 of 8**.
- No empty chart box was observed in either build in these runs (frames
  between the box and its chart: 0), so `ChartFrame` shows no measured gain
  on this path; it remains as protection against the 0-width unmount.
- One live streamed answer on the before build drew a viz-core chart that
  mounted once with no removal; the remount is specific to interactive
  answers.

## 3. Reopening an old thread

### Change

- The backend verification above (network).
- The transcript pins to the bottom in a layout effect, before paint, instead
  of in an animation frame after paint, and a `ResizeObserver` keeps it
  pinned while charts and tables finish laying out, as long as the reader is
  following the end.
- No fallback-then-chart swap (section 2), so content no longer grows after
  the first scroll.

### Result

Headless browser, two threads (22 and 24 messages, 3-4 charts each), 8
interleaved opens per side:

| Measure | Before | After |
|---|---|---|
| Thread request, median | 11,680 ms (CI 3,213-17,265) | 2,672 ms (CI 1,446-10,347) |
| First frame of the thread already at the bottom | 2 of 8 | 8 of 8 |
| Gap from bottom on the first frame, median | 2,260 px (max 4,063) | 0 px |
| Charts removed while opening | 16 | 0 |
| Main-thread long tasks per open, median | 1,182 ms (CI 1,014-1,303) | 872 ms (CI 823-1,156) |
| Gap from bottom once settled | 0 px | 0 px |

## Not changed, and what remains

- Rendering a long transcript is still JavaScript-bound (the longest task per
  open is ~550 ms in both builds). Taking offscreen turns out of layout with
  `content-visibility` did not measurably reduce it, so the next step would be
  windowed rendering of turns above the viewport, which needs its own scroll
  anchoring work.
- The history list waits for Studio settings before it requests the list,
  because the list's cache key carries the security identity that request
  returns. Starting both at once needs a separate decision on cache keys.
- The remaining ~0.7 s of verification is real authorization: one
  caller-identity `EXPLAIN` per Semantic View source, each audited.
- Each interactive visual still fetches its saved state with its own request
  (doubled in development by StrictMode).

## Checks

- Backend: full unit suite with coverage, 8,260 passed; eval scorecard 50/50
  scenarios, 171/171 checks; Ruff and mypy clean on the changed files; new
  `tests/unit/test_agent_access_verification_reads.py` (6 tests).
- Frontend: lint 0 errors (24 existing warnings); build passes; full browser
  suite with coverage 1,450 of 1,452 passed in one run. The two failures
  (`providers-tab.test.tsx`, `newspaper-page.test.tsx`) are timing tests that
  passed when rerun on the same code under the same load.
