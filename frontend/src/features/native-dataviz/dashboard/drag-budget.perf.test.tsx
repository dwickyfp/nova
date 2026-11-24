import { describe, expect, it, vi } from 'vitest'
import { cdp } from 'vitest/browser'
import { text, shape, icon, mouse, centre, frameOrigin, nextFrame, arranged, selections, mount, installCanvasHarness } from './canvas-harness'
import { DRAGS, judgeFrames } from './frame-budget'
import type { Element } from './layout'

// The drag frame budget, a P2 exit gate: dragging with 60 items on the page
// stays within 8 ms per frame. It is timed by the wall clock, so it runs in its
// own Vitest project ("perf") with no other test file alongside it: run it
// with `pnpm test:perf`. CI runs that as its own step after the full suite.

installCanvasHarness()

describe('drag performance', () => {
  // 60 items: notes, shapes and icons on a 10 by 6 grid.
  const sixty = () => Array.from({ length: 60 }, (_, n) => {
    const x = 6 + (n % 10) * 118
    const y = 6 + Math.floor(n / 10) * 110
    return n % 3 === 0 ? text(n, x, y, 100, 60) : n % 3 === 1 ? shape(n, x, y) : icon(n, x, y)
  })


  /**
   * Drag `target` through 90 pointer moves, timing every frame the canvas draws
   * with the style and layout work it causes. `dy` is 1 to drag down, -1 up.
   */
  async function measuredDrag(target: Element, dy: 1 | -1): Promise<number[]> {
    arranged.length = 0
    // Benchmark hygiene: a collection beforehand keeps a pause for garbage left by earlier tests out of the frames.
    await (cdp() as unknown as { send(method: string): Promise<unknown> }).send('HeapProfiler.collectGarbage')
    const durations: number[] = []
    const real = window.requestAnimationFrame.bind(window)
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => real(time => {
      const begin = performance.now()
      callback(time)
      void document.body.getBoundingClientRect()
      void document.querySelector(`[data-element-id="${target.element_id}"]`)!.getBoundingClientRect()
      durations.push(performance.now() - begin)
    }))
    const point = centre(target)
    await mouse('down', point)
    for (let step = 1; step <= 90; step++) {
      await mouse('move', { x: point.x + (step % 30) * 3, y: point.y + dy * step })
      // Wait with the real scheduler so only the canvas's own frames are timed.
      await new Promise(resolve => real(() => real(resolve)))
    }
    await mouse('up', { x: point.x, y: point.y + dy * 90 })
    vi.stubGlobal('requestAnimationFrame', real)
    await expect.poll(() => arranged.length).toBe(1)
    // The first frame also mounts the guides layer.
    return durations.slice(1)
  }

  /**
   * The 8 ms budget per frame, judged over five measured drags together (see
   * `frame-budget.ts`): a shared runner's occasional slow frame passes, a
   * slower drag path does not. Drags alternate between `dy` and back, each
   * starting where the item now is, so `dy` must not leave it under a later
   * (higher) item.
   */
  async function expectWithinBudget(target: Element, annotate: (message: string) => Promise<unknown>, dy: 1 | -1 = 1) {
    // One unmeasured drag warms the code up.
    const warm = centre(target)
    await mouse('down', warm)
    for (let step = 1; step <= 10; step++) {
      await mouse('move', { x: warm.x + step, y: warm.y + step })
      await nextFrame()
    }
    await mouse('move', warm)
    await nextFrame()
    await mouse('up', warm)
    const drags: number[][] = []
    for (let drag = 1; drag <= DRAGS; drag++) {
      // Back and forth, so five drags never run the item into the page edge.
      const frames = await measuredDrag(target, drag % 2 ? dy : (-dy as 1 | -1))
      drags.push(frames)
      // Every drag moved the item it pressed, not one it landed on.
      expect(arranged[0].map(item => item.element_id)).toContain(target.element_id)
      // One frame per move; a slow runner may merge a few.
      expect(frames.length).toBeGreaterThan(60)
    }
    const verdict = judgeFrames(drags)
    await annotate(`${DRAGS} drags, ${verdict.frames} frames: median ${verdict.median.toFixed(2)} ms, p95 ${verdict.p95.toFixed(2)} ms, `
      + `max ${verdict.max.toFixed(2)} ms, ${verdict.slow} over budget`)
    expect(verdict.problems).toEqual([])
  }

  it('keeps each frame within 8 ms dragging all 60 items together', { timeout: 90_000 }, async ({ annotate }) => {
    const elements = sixty()
    await mount(elements)
    // Select all 60 with a marquee, then drag them.
    const origin = frameOrigin()
    await mouse('down', { x: origin.left + 1199, y: origin.top + 710 })
    await mouse('move', { x: origin.left + 600, y: origin.top + 400 })
    await mouse('move', { x: origin.left + 1, y: origin.top + 1 })
    await mouse('up', { x: origin.left + 1, y: origin.top + 1 })
    await expect.poll(() => selections[selections.length - 1]?.length).toBe(60)
    await expectWithinBudget(elements[0], annotate)
    expect(arranged[0]).toHaveLength(60)
  })

  it('keeps each frame within 8 ms dragging one item that snaps to the other 59', { timeout: 90_000 }, async ({ annotate }) => {
    const elements = sixty()
    await mount(elements)
    // Upward, onto items drawn beneath it: dragged down it would land under item 43,
    // and a second attempt would press that item instead.
    await expectWithinBudget(elements[33], annotate, -1)
    expect(arranged[0].map(item => item.element_id)).toEqual([elements[33].element_id])
  })
})
