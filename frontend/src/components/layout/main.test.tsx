import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import '@/styles/index.css'
import { Main } from './main'

describe('bounded page content', () => {
  it('keeps long content inside the main scroll area', async () => {
    const screen = await render(
      <div data-testid='viewport' className='flex h-60 min-h-0 flex-col overflow-hidden'>
        <header className='shrink-0 p-2'>Page header</header>
        <Main scroll>
          <div>
            {Array.from({ length: 50 }, (_, index) => <p key={index}>Content row {index + 1}</p>)}
          </div>
        </Main>
      </div>,
    )

    const viewport = screen.getByTestId('viewport').element()
    const main = screen.getByRole('main').element()
    expect(main.dataset.layout).toBe('fixed')
    expect(main.scrollHeight).toBeGreaterThan(main.clientHeight)
    expect(main.getBoundingClientRect().bottom).toBeLessThanOrEqual(viewport.getBoundingClientRect().bottom)
    expect(viewport.scrollHeight).toBeLessThanOrEqual(viewport.clientHeight)
    const header = viewport.querySelector('header')!
    const headerTop = header.getBoundingClientRect().top
    main.scrollTop = 100
    expect(main.scrollTop).toBe(100)
    expect(header.getBoundingClientRect().top).toBe(headerTop)
  })
})
