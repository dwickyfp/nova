import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SearchableSelect } from './searchable-select'
import '@/styles/index.css'

function Pair() {
  const [first, setFirst] = useState('')
  const [second, setSecond] = useState('')
  return <div className='flex gap-4 p-4'>
    <SearchableSelect label='First list' value={first} options={['Sales', 'Other']} allowEmpty={false} onChange={setFirst} />
    <SearchableSelect label='Second list' value={second} options={['Revenue', 'Orders']} allowEmpty={false} disabled={!first} onChange={setSecond} />
  </div>
}

const lists = () => document.querySelectorAll('[data-slot=popover-content]')

afterEach(async () => { await cleanup() })

describe('searchable select', () => {
  it('keeps the next list open when it is opened as the previous one finishes closing', async () => {
    // A slow machine can open the second list after the first list's content is
    // removed but before focus would return to the first trigger. That late
    // focus used to land outside the second list and close it.
    const screen = await render(<Pair />)
    await screen.getByRole('button', { name: 'First list' }).click()
    const second = screen.getByRole('button', { name: 'Second list' }).element() as HTMLButtonElement
    const opened = new Promise<void>(resolve => {
      const watch = new MutationObserver(() => {
        if (lists().length || second.disabled) return
        watch.disconnect()
        second.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }))
        second.click()
        resolve()
      })
      watch.observe(document.body, { childList: true, subtree: true, attributes: true })
    })
    await screen.getByRole('button', { name: 'Sales', exact: true }).click()
    await opened
    await new Promise(resolve => setTimeout(resolve, 300))
    expect(lists()).toHaveLength(1)
    await screen.getByRole('button', { name: 'Revenue', exact: true }).click()
    await expect.element(screen.getByRole('button', { name: 'Second list' })).toHaveTextContent('Revenue')
  })

  it('returns focus to its trigger after a choice made from the keyboard', async () => {
    const screen = await render(<Pair />)
    await screen.getByRole('button', { name: 'First list' }).click()
    await expect.element(screen.getByRole('textbox', { name: 'Search first list' })).toHaveFocus()
    await userEvent.keyboard('{Tab}{Enter}')
    await expect.element(screen.getByRole('button', { name: 'First list' })).toHaveTextContent('Sales')
    await expect.element(screen.getByRole('button', { name: 'First list' })).toHaveFocus()
  })
})
