# Superset lean deployment: before and after

The baseline was measured on 2026-11-08 with `nova-superset:baseline-6.1.0`:
one image for web, worker and beat, with Playwright and Chromium in all three.
The lean deployment was measured on 2026-11-09 with the images split by
target. The web and beat services run `nova-superset:local` (`web` target, no
browser). The worker runs `nova-superset-jobs:local` (`jobs` target, with the
browser) using `--autoscale=2,1`, `--max-memory-per-child` and a broker-ping
healthcheck. Beat entries now carry `expires`. See
[superset-lean-deployment](../../operations/superset-lean-deployment.md).

Both sets ran on the same Mac, the same local stack and the same scripts
(`superset/bench/`). Raw files are in [raw/](raw/).

## Smoke test of the lean deployment

[raw/lean-smoke.json](raw/lean-smoke.json) passed 17 of 17 checks:

- All containers are healthy, and init ran once and exited 0.
- The web image has no browser; the worker image has one.
- The worker answers on its broker, and the Celery queue holds 0 messages.
  As found before the change it held 1,323 queued `reports.scheduler` ticks.
- Signed-in dashboard reads, governed chart data through Nova's proxy and
  dashboard saves all work.
- The native filter state round trip works, and the thumbnail endpoint
  answers.
- Nova's Dataviz listing and context answer.

## Images

| Image | Baseline | Lean |
| --- | --- | --- |
| Web and beat | 1,969.8 MiB | **870.4 MiB** (−56%) |
| Worker | 1,969.8 MiB | 1,969.9 MiB (still needs the browser for reports and thumbnails) |

## Idle, 10 minutes, sampled every 5 s

| Total (4 BI containers) | Baseline | Lean |
| --- | --- | --- |
| Footprint (PSS + SwapPss), mean | 803.3 MiB | **697.7 MiB** (−13%) |
| Footprint, p95 | 1,005.0 MiB | **707.8 MiB** (−30%) |
| CPU, mean | 30.0% of a core | **10.7%** (−64%) |
| CPU, p95 | 120.1% | **43.8%** |

Per container, footprint mean and CPU mean:

| Container | Baseline | Lean |
| --- | --- | --- |
| web | 201 MiB, 1.9% | 257 MiB, 3.0% |
| worker | 411 MiB, 26.7% | **257 MiB, 5.0%** |
| beat | 191 MiB, 0.1% | 183 MiB, 0.9% |

The saving is the worker. The baseline's worker healthcheck started a full
Celery client every check: 13.9 CPU-seconds and 258 MB each time. Its two
prefork children also never recycled. The web container's footprint is about
56 MiB higher. That is within the variation of one 10-minute sample on a
shared VM, and the web image itself is smaller, so I do not count it as a
regression.

## Under the load matrix (13 minutes)

| Total | Baseline | Lean |
| --- | --- | --- |
| Footprint, mean | 907.3 MiB | **724.5 MiB** (−20%) |
| Footprint, p95 | 1,071.0 MiB | **738.9 MiB** (−31%) |
| CPU, mean | 132.6% | **70.4%** (−47%) |
| Worker CPU, mean | 61.8% | **8.8%** |

## Dashboard load times

Each cell is one 65-second run, so p50 and p95 are reported without
confidence intervals.

| Charts × viewers | Baseline p50 / p95 (s) | Lean p50 / p95 (s) |
| --- | --- | --- |
| 4 × 1 | 8.3 / 23.2 | 4.9 / 17.1 |
| 4 × 5 | 8.6 / 17.1 | 10.1 / 14.2 |
| 4 × 20 | 22.3 / 28.6 | 33.1 / 48.1 |
| 12 × 1 | 6.0 / 26.5 | 8.0 / 14.3 |
| 12 × 5 | 34.9 / 46.8 | 14.3 / 35.9 |
| 12 × 20 | 106.8 / 121.7 | 91.6 / 140.8 |
| 24 × 1 | 22.7 / 32.9 | 27.3 / 39.4 |
| 24 × 5 | 65.7 / 78.1 | 44.9 / 52.9 |
| 24 × 20 | 170.8 / 187.0 | 143.0 / 153.3 |

Edit save-and-reopen p50 is 207 → 208 ms with one author and 444 → 397 ms
with five. Error rates are 0–0.7% in the baseline and 0–3.9% in the lean
run: one 12 × 5 cell had 3.9% errors, all chart-data timeouts.

**Reading:** the lean change is not a dashboard-speed change, and these
numbers do not show one either way. Cells move in both directions by more
than one run can resolve. The host's load average was about 10 during the
lean run, from an unrelated process. Dashboard time is dominated by chart
data through Nova's proxy and StarRocks (finding F4: about 1 s per chart even
when cached, from per-request dialect probes). The lean images do not touch
that path.

## Cold start (restart of `nova-superset` to the first chart's data)

| | Baseline | Lean |
| --- | --- | --- |
| Ready, median of 3 | 26.3 s | **23.1 s** |
| Ready, max | 31.6 s | 27.7 s |

## What was not changed

- **F4.** Chart data latency through the proxy is documented, not fixed.
- **F2.** Redis `allkeys-lru` eviction risk for the Celery broker is
  documented. The broker shares that Redis.
- **Worker image.** Reports and thumbnails need Chromium, so the worker image
  is the same size as before.
