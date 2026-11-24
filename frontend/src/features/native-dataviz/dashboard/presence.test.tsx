import { afterEach, describe, expect, it, vi } from 'vitest'
import { page } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { TooltipProvider } from '@/components/ui/tooltip'
import { colourFor, initials, ROW_PX, usePresence, type Collaborator } from './presence'
import { PresenceAvatars, PresenceCursors } from './presence-ui'
import '@/styles/index.css'

const person = (viewer: string, name: string, extra: Partial<Collaborator> = {}): Collaborator =>
  ({ viewer, name, page_id: 'p1', cursor: null, editing: false, seconds_ago: 0, ...extra })

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('presence helpers', () => {
  it('gives a person the same colour on every screen', () => {
    expect(colourFor('abc')).toBe(colourFor('abc'))
    expect(colourFor('abc')).toMatch(/^var\(--chart-\d\)$/)
  })

  it('makes initials from user names', () => {
    expect(initials('alice')).toBe('AL')
    expect(initials('budi.santoso')).toBe('BS')
    expect(initials('  ')).toBe('?')
  })
})

describe('presence avatars', () => {
  it('shows one avatar per person and counts the rest', async () => {
    const others = [person('a1', 'alice', { editing: true }), person('a2', 'alice'), person('b', 'bob'),
      person('c', 'carol'), person('d', 'dewi'), person('e', 'eko')]
    render(<TooltipProvider><PresenceAvatars others={others} more={2} /></TooltipProvider>)
    const group = page.getByRole('group', { name: 'Also here: alice (editing), bob, carol, dewi, eko' })
    await expect.element(group).toBeVisible()
    await expect.element(page.getByText('+3')).toBeVisible()
    await expect.element(page.getByText('EK')).not.toBeInTheDocument()
  })

  it('renders nothing when no one else is here', async () => {
    const { container } = await render(<PresenceAvatars others={[]} more={0} />)
    expect(container.textContent).toBe('')
  })
})

describe('presence cursors', () => {
  it('places pointers of people on this page only', async () => {
    const others = [person('a', 'alice', { cursor: { x: 0.5, y: 2 } }),
      person('b', 'bob', { page_id: 'p2', cursor: { x: 0.1, y: 1 } }), person('c', 'carol')]
    const { container } = await render(<div style={{ position: 'relative', width: 400, height: 400 }}>
      <PresenceCursors others={others} pageId='p1' />
    </div>)
    expect(container.textContent).toBe('alice')
    const pointer = container.querySelector<HTMLElement>('[style*="left"]')!
    expect(pointer.style.left).toBe('50%')
    expect(pointer.style.top).toBe(`${2 * ROW_PX}px`)
  })

  it('scales pointers with the page, so they land on the same spot at any width', async () => {
    const { container } = await render(<div style={{ position: 'relative', width: 600, height: 400 }}>
      <PresenceCursors others={[person('a', 'alice', { cursor: { x: 0.25, y: 3 } })]} pageId='p1' scale={0.5} />
    </div>)
    const pointer = container.querySelector<HTMLElement>('[style*="left"]')!
    expect(pointer.style.left).toBe('25%')
    expect(pointer.style.top).toBe(`${3 * ROW_PX * 0.5}px`)
  })
})

describe('presence after access is revoked', () => {
  function Others() {
    const { others } = usePresence({ dashboardId: 'dddddddd-0000-4000-8000-000000000001', pageId: null, editing: false })
    return <ul aria-label='Others here'>{others.map(item => <li key={item.viewer}>{item.name}</li>)}</ul>
  }

  it('stops showing who else is here once a heartbeat is refused', async () => {
    let allowed = true
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit = {}) => {
      if (init.method === 'DELETE') return new Response(null, { status: 204 })
      return allowed
        ? new Response(JSON.stringify({ viewer: 'me', more: 0, others: [person('v2', 'bob')] }),
          { status: 200, headers: { 'Content-Type': 'application/json' } })
        : new Response(JSON.stringify({ detail: 'Dashboard not found' }),
          { status: 404, headers: { 'Content-Type': 'application/json' } })
    }))
    const screen = await render(<Others />)
    await expect.element(screen.getByText('bob')).toBeVisible()
    allowed = false
    await vi.waitFor(() => expect(screen.container.textContent).not.toContain('bob'), { timeout: 4000 })
  })
})
