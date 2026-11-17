import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { page, userEvent } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import type { DashboardDefinition } from '../contracts'
import { CommentsPanel } from './comments-panel'
import type { DashboardComment } from './comments'
import '@/styles/index.css'

const DASHBOARD = 'dddddddd-0000-4000-8000-000000000001'
const PAGE = 'eeeeeeee-0000-4000-8000-000000000001'
const WIDGET = 'ffffffff-0000-4000-8000-000000000001'
const definition = {
  dashboard_id: DASHBOARD, title: 'Sales', pages: [{ page_id: PAGE, title: 'Overview', widgets: [
    { kind: 'chart', widget_id: WIDGET, title: 'Revenue by region', layout: { x: 0, y: 0, w: 6, h: 4 } }] }],
} as unknown as DashboardDefinition

let stored: DashboardComment[] = []
const requests: { method: string; url: string; body: unknown }[] = []
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  stored = [
    { comment_id: 'c1', page_id: PAGE, widget_id: WIDGET, author: 'dan', body: '<b>Why</b> the dip?', created_at: '2026-11-16T03:00:00Z',
      resolved_at: null, resolved_by: null, mine: false },
    { comment_id: 'c2', page_id: null, widget_id: null, author: 'carol', body: 'Old note', created_at: '2026-11-15T03:00:00Z',
      resolved_at: '2026-11-15T04:00:00Z', resolved_by: 'carol', mine: true },
  ]
  requests.length = 0
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? 'GET'
    const body = init.body ? JSON.parse(String(init.body)) : undefined
    requests.push({ method, url: String(url), body })
    if (method === 'POST') {
      const saved = { ...body, author: 'carol', created_at: '2026-11-16T05:00:00Z', page_id: body.page_id ?? null,
        widget_id: body.widget_id ?? null, resolved_at: null, resolved_by: null, mine: true }
      stored.push(saved)
      return json(saved, 201)
    }
    if (method === 'PATCH') return json({})
    if (method === 'DELETE') return new Response(null, { status: 204 })
    return json(stored)
  }))
})
afterEach(async () => { await cleanup(); vi.unstubAllGlobals() })

async function show(props: Partial<Parameters<typeof CommentsPanel>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>
    <CommentsPanel open onOpenChange={() => undefined} dashboardId={DASHBOARD} definition={definition} pageId={PAGE}
      selectedWidgetId={WIDGET} canComment canManage={false} {...props} />
  </QueryClientProvider>)
}

describe('dashboard comments', () => {
  it('lists open comments with their place, shows text literally and hides resolved ones until asked', async () => {
    await show()
    await expect.element(page.getByText('<b>Why</b> the dip?')).toBeVisible()
    await expect.element(page.getByText('Overview · Revenue by region')).toBeVisible()
    expect(document.querySelector('[role="dialog"] b')).toBeNull()
    await expect.element(page.getByText('Old note')).not.toBeInTheDocument()
    await page.getByRole('button', { name: 'Show resolved (1)' }).click()
    await expect.element(page.getByText('Resolved by carol')).toBeVisible()
  })

  it('posts about the selected widget, resolves, and only offers to remove your own', async () => {
    await show()
    await expect.element(page.getByText('<b>Why</b> the dip?')).toBeVisible()
    await expect.element(page.getByRole('button', { name: 'Remove the comment by dan' })).not.toBeInTheDocument()
    await page.getByRole('textbox', { name: 'Comment' }).fill('Bandung missed its target')
    await page.getByRole('button', { name: 'Post' }).click()
    await expect.poll(() => requests.find(item => item.method === 'POST')?.body).toMatchObject({
      body: 'Bandung missed its target', page_id: PAGE, widget_id: WIDGET })
    await expect.element(page.getByRole('button', { name: 'Remove the comment by carol' })).toBeVisible()
    await page.getByRole('button', { name: 'Resolve', exact: true }).first().click()
    await expect.poll(() => requests.find(item => item.method === 'PATCH')?.body).toEqual({ resolved: true })
    await userEvent.keyboard('{Escape}')
  })

  it('lets someone without Comment access read but not write', async () => {
    await show({ canComment: false })
    await expect.element(page.getByText('<b>Why</b> the dip?')).toBeVisible()
    await expect.element(page.getByText('Your access lets you read comments but not write them.')).toBeVisible()
    await expect.element(page.getByRole('textbox', { name: 'Comment' })).not.toBeInTheDocument()
    await expect.element(page.getByRole('button', { name: 'Resolve', exact: true })).not.toBeInTheDocument()
  })
})
