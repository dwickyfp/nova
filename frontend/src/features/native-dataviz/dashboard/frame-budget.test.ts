import { describe, expect, it } from 'vitest'
import { DRAGS, judgeFrames } from './frame-budget'

// Five drags of 89 timed frames, like the canvas drag test, from a seeded generator.
function drags(frame: (drag: number, index: number) => number) {
  return Array.from({ length: DRAGS }, (_, drag) => Array.from({ length: 89 }, (_, index) => frame(drag, index)))
}
let seed = 7
const noise = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 }
const typical = () => 1.5 + noise() * 1.5

describe('drag frame budget', () => {
  it('passes a healthy drag with the runner hiccups seen in CI (single frames of 8.4 to 11.9 ms)', () => {
    const spikes = new Map([[0, 8.4], [2, 11.9], [3, 9.9], [4, 9.7]])
    const verdict = judgeFrames(drags((drag, index) => index === 40 && spikes.has(drag) ? spikes.get(drag)! : typical()))
    expect(verdict.problems).toEqual([])
    expect(verdict.slow).toBe(4)
  })

  it('fails when a slow frame comes back regularly, even if most frames are fast', () => {
    const verdict = judgeFrames(drags((_, index) => index % 30 === 0 ? 12 : typical()))
    expect(verdict.problems.join('\n')).toMatch(/frames are over 8 ms/)
  })

  it('fails when the typical frame gets slower, before the budget itself is reached', () => {
    const verdict = judgeFrames(drags(() => 4.5 + noise()))
    expect(verdict.problems.join('\n')).toMatch(/median .* is over 4 ms/)
  })

  it('fails a drag that is slow on its own', () => {
    const verdict = judgeFrames(drags((drag, index) => drag === 2 && index % 10 < 2 ? 10 : typical()))
    expect(verdict.problems.join('\n')).toMatch(/drag 3: p95/)
  })

  it('passes one descheduled frame (38 ms was seen once under a full local suite)', () => {
    const verdict = judgeFrames(drags((drag, index) => drag === 1 && index === 10 ? 38 : typical()))
    expect(verdict.problems).toEqual([])
  })

  it('fails a stall that comes back in every drag', () => {
    const verdict = judgeFrames(drags((_, index) => index === 10 ? 48 : typical()))
    expect(verdict.problems.join('\n')).toMatch(/5 frames took longer than 33 ms \(at most one\)/)
  })
})
