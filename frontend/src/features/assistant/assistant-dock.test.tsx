import { page } from 'vitest/browser'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { WorkspaceTreeResponse } from '@/features/workspaces/types'
import { AssistantProvider } from './assistant-provider'
import { AssistantDock } from './assistant-dock'

function makeTree(overrides: Partial<WorkspaceTreeResponse> = {}): WorkspaceTreeResponse {
  return {
    root_name: 'workspace',
    entries: [],
    open_tabs: [],
    active_tab: null,
    sidebar_collapsed: false,
    assistant_collapsed: false,
    defaults: { database: 'analytics', schema: 'public', role: 'ACCOUNTADMIN' },
    ...overrides,
  }
}

function mockTree(tree: WorkspaceTreeResponse) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    if (String(input).includes('/workspaces/tree')) {
      return new Response(JSON.stringify(tree), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response(null, { status: 204 })
  })
}

/**
 * Mirrors the layout's structure: the provider wraps the route slot, so a route
 * change swaps the slot contents without remounting the assistant. This is the
 * production shape with no WorkspacesPage in the tree.
 */
function LayoutHarness({ route }: { route: string }) {
  return (
    <div className='flex h-svh'>
      <div data-testid='route'>{route}</div>
      <AssistantDock />
    </div>
  )
}

function renderLayout(tree: WorkspaceTreeResponse, route = 'dashboard') {
  mockTree(tree)
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AssistantProvider>
        <LayoutHarness route={route} />
      </AssistantProvider>
    </QueryClientProvider>
  )
}

describe('AssistantDock', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('restores an open panel from the tree without WorkspacesPage mounted', async () => {
    await page.viewport(1440, 900)
    try {
      const { getByRole, container } = await renderLayout(makeTree({ assistant_collapsed: false }))

      await expect.element(getByRole('button', { name: 'Hide assistant' })).toBeInTheDocument()
      expect(container.querySelector('#assistant-panel')?.hasAttribute('inert')).toBe(false)
    } finally {
      await page.viewport(375, 800)
    }
  })

  it('renders a collapsed panel when the tree says it is collapsed', async () => {
    await page.viewport(1440, 900)
    try {
      const { getByRole, container } = await renderLayout(makeTree({ assistant_collapsed: true }))

      await expect.element(getByRole('button', { name: 'Show assistant' })).toBeInTheDocument()
      expect(container.querySelector('#assistant-panel')?.hasAttribute('inert')).toBe(true)
    } finally {
      await page.viewport(375, 800)
    }
  })

  it('opens and closes the panel through the persistent toggle', async () => {
    await page.viewport(1440, 900)
    try {
      const { getByRole, container } = await renderLayout(makeTree({ assistant_collapsed: true }))

      await getByRole('button', { name: 'Show assistant' }).click()
      await expect.element(getByRole('button', { name: 'Hide assistant' })).toBeInTheDocument()
      expect(container.querySelector('#assistant-panel')?.hasAttribute('inert')).toBe(false)

      await getByRole('button', { name: 'Hide assistant' }).click()
      await expect.element(getByRole('button', { name: 'Show assistant' })).toBeInTheDocument()
      expect(container.querySelector('#assistant-panel')?.hasAttribute('inert')).toBe(true)
    } finally {
      await page.viewport(375, 800)
    }
  })

  it('keeps the open state across a route change', async () => {
    await page.viewport(1440, 900)
    try {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      mockTree(makeTree({ assistant_collapsed: true }))
      const { getByRole, rerender } = await render(
        <QueryClientProvider client={client}>
          <AssistantProvider>
            <LayoutHarness route='dashboard' />
          </AssistantProvider>
        </QueryClientProvider>
      )

      await getByRole('button', { name: 'Show assistant' }).click()
      await expect.element(getByRole('button', { name: 'Hide assistant' })).toBeInTheDocument()

      // A route change swaps the slot but keeps the provider, so the assistant
      // (and its open state) is not remounted.
      await rerender(
        <QueryClientProvider client={client}>
          <AssistantProvider>
            <LayoutHarness route='database-explorer' />
          </AssistantProvider>
        </QueryClientProvider>
      )

      await expect.element(getByRole('button', { name: 'Hide assistant' })).toBeInTheDocument()
    } finally {
      await page.viewport(375, 800)
    }
  })

  it('guards the panel transition against reduced motion', async () => {
    await page.viewport(1440, 900)
    try {
      const { container } = await renderLayout(makeTree({ assistant_collapsed: false }))

      const aside = container.querySelector('#assistant-panel')
      expect(aside?.parentElement?.className).toContain('motion-reduce:transition-none')
      expect(aside?.parentElement?.className).toContain('transition-[width]')
    } finally {
      await page.viewport(375, 800)
    }
  })

  it('moves the FAB out of the composer corner when open', async () => {
    await page.viewport(1440, 900)
    try {
      // The browser test runner does not load the Tailwind stylesheet, so the
      // measured geometry is the UA default. Assert the position contract here;
      // the live click-through measures the real boxes.
      const { getByRole } = await renderLayout(makeTree({ assistant_collapsed: true }))

      const closed = getByRole('button', { name: 'Show assistant' }).element()
      expect(closed.className).toContain('bottom-4')
      expect(closed.className).not.toContain('top-4')

      await getByRole('button', { name: 'Show assistant' }).click()
      const open = getByRole('button', { name: 'Hide assistant' }).element()
      expect(open.className).toContain('top-4')
      expect(open.className).not.toContain('bottom-4')
    } finally {
      await page.viewport(375, 800)
    }
  })

  it('ladders the panel inline at 768px and as a Sheet just below it', async () => {
    try {
      await page.viewport(768, 900)
      const wide = await renderLayout(makeTree({ assistant_collapsed: false }), 'database-explorer')
      await expect.element(wide.getByRole('complementary', { name: 'Assistant' })).toBeInTheDocument()
      await expect.element(wide.getByRole('button', { name: 'Hide assistant' })).toBeInTheDocument()
      wide.unmount()

      await page.viewport(767, 900)
      const narrow = await renderLayout(makeTree({ assistant_collapsed: false }), 'database-explorer')
      // Below md the panel is a Sheet overlay with its own close control; the
      // FAB is the trigger that opened it and is inert behind the overlay.
      const dialog = narrow.getByRole('dialog')
      await expect.element(dialog).toBeInTheDocument()
      await expect.element(narrow.getByRole('button', { name: 'Close assistant' })).toBeInTheDocument()
      narrow.unmount()
    } finally {
      await page.viewport(375, 800)
    }
  })
})
