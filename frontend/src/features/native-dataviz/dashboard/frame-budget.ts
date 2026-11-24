/**
 * The drag frame budget, decided from several measured drags together.
 *
 * Wall-clock frame times also count moments the operating system takes the
 * processor away. On a shared CI runner that shows up as a single slow frame
 * now and then, on any branch. A slower drag path shows up differently: the
 * typical frame gets slower, or a slow frame comes back regularly. So the
 * decision pools every frame of every drag and asks four things:
 *
 * - each drag keeps 95% of its frames within budget (a slow drag fails alone);
 * - the typical frame (median of all) stays within half the budget;
 * - at most 2% of all frames go over budget (an occasional hiccup passes, a
 *   slow frame every 50 or fewer does not);
 * - at most one frame takes longer than two 60 Hz display frames. A runner can
 *   deschedule one frame for 20–40 ms; a stall in the drag path comes back in
 *   every drag.
 */
export const FRAME_BUDGET_MS = 8
export const DRAGS = 5
const LONGEST_MS = 33
const SLOW_SHARE = 0.02

export type DragFrames = readonly number[]

const quantile = (sorted: readonly number[], share: number) => sorted[Math.min(sorted.length - 1, Math.floor(share * sorted.length))]

export type Verdict = {
  frames: number
  median: number
  p95: number
  max: number
  slow: number
  problems: string[]
}

export function judgeFrames(drags: readonly DragFrames[], budget = FRAME_BUDGET_MS): Verdict {
  const problems: string[] = []
  drags.forEach((frames, index) => {
    const p95 = quantile([...frames].sort((a, b) => a - b), 0.95)
    if (p95 > budget) problems.push(`drag ${index + 1}: p95 ${p95.toFixed(2)} ms is over ${budget} ms`)
  })
  const all = drags.flat().sort((a, b) => a - b)
  const median = quantile(all, 0.5)
  const slow = all.filter(time => time > budget).length
  const max = all[all.length - 1] ?? 0
  if (median > budget / 2) problems.push(`median ${median.toFixed(2)} ms is over ${budget / 2} ms`)
  if (slow > all.length * SLOW_SHARE) problems.push(`${slow} of ${all.length} frames are over ${budget} ms (at most ${Math.floor(all.length * SLOW_SHARE)})`)
  const stalls = all.filter(time => time > LONGEST_MS)
  if (stalls.length > 1) problems.push(`${stalls.length} frames took longer than ${LONGEST_MS} ms (at most one): ${stalls.map(time => time.toFixed(1)).join(', ')} ms`)
  return { frames: all.length, median, p95: quantile(all, 0.95), max, slow, problems }
}
