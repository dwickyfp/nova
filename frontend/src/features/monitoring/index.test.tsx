import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import '@/styles/index.css'
import { MonitoringPageScroller } from './index'

describe('MonitoringPageScroller', () => {
  it('owns vertical overflow for monitoring pages', async () => {
    const { container } = await render(
      <div className='flex h-48 min-h-0 flex-col'>
        <MonitoringPageScroller>
          <div className='h-[900px]'>Tall monitoring content</div>
        </MonitoringPageScroller>
      </div>,
    )

    const scroller = container.querySelector('.overflow-y-auto')
    expect(scroller).not.toBeNull()
    expect(getComputedStyle(scroller as Element).overflowY).toBe('auto')
    expect((scroller as HTMLElement).scrollHeight).toBeGreaterThan(
      (scroller as HTMLElement).clientHeight,
    )
  })
})
