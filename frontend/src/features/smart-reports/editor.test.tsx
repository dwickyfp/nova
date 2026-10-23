import { afterEach, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import fixture from './fixtures/basic.json'
import { reportSpecSchema, viewStateSchema } from './contracts'
import { ReportEditor } from './editor'

afterEach(() => vi.restoreAllMocks())

const spec = reportSpecSchema.parse(fixture.spec)
const state = viewStateSchema.parse({ ...fixture.state, draft_id: 'draft-a', spec_revision: 3 })
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })

it('previews undo without mutating the draft, then applies its expected revision', async () => {
  const mutations: { path: string; body: Record<string, unknown> }[] = []
  vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
    const path = new URL(String(input), location.origin).pathname
    if (path.endsWith('/sources')) return json({ sources: [] })
    const body = JSON.parse(String(init?.body))
    mutations.push({ path, body })
    if (path.endsWith('/preview-render')) return json({ spec: { ...spec, title: 'Candidate data' }, state, result: {
      report_id: state.report_id, spec_revision: 3, state_revision: 1, as_of: '2026-10-23T05:00:00Z', status: 'success',
      widgets: spec.pages[0].sections[0].widgets.map(widget => ({ widget_id: widget.id, status: 'empty', columns: [], rows: [], totals: {}, completeness: 'complete', not_applied: [] })),
    } })
    if (path.endsWith('/validate-patch')) return json({ before: spec, after: { ...spec, title: 'Previous title' } })
    if (init?.method === 'PATCH') return json({ revision: 4 })
    throw new Error(`Unexpected request ${path}`)
  })
  const applied = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const screen = await render(<QueryClientProvider client={client}><ReportEditor spec={spec} state={state} onApplied={applied} publishedRevision={1} canUndo canRedo={false} canPublish /></QueryClientProvider>)
  await expect.element(screen.getByRole('button', { name: 'Redo', exact: true })).toBeDisabled()
  await screen.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect.element(screen.getByText('Previous title', { exact: true })).toBeVisible()
  expect(applied).not.toHaveBeenCalled()
  expect(mutations).toHaveLength(1)
  await screen.getByRole('button', { name: 'Preview data', exact: true }).click()
  await expect.element(screen.getByRole('heading', { name: 'Candidate data', exact: true })).toBeVisible()
  await expect.element(screen.getByText('Jakarta', { exact: true })).toBeVisible()
  expect(applied).not.toHaveBeenCalled()
  await screen.getByRole('button', { name: 'Close', exact: true }).click()
  await screen.getByRole('button', { name: 'Apply to draft', exact: true }).click()
  await expect.poll(() => applied.mock.calls).toEqual([[4]])
  expect(mutations[0].body).toMatchObject({ expected_revision: 3, operations: [{ op: 'UNDO_DRAFT' }] })
  expect(mutations[1].body).toMatchObject({ patch: mutations[0].body })
  expect(mutations[2].body).toEqual(mutations[0].body)
  expect(mutations.some(request => request.path.endsWith('/publish'))).toBe(false)
  await screen.unmount()
  client.clear()
})

it('reviews the submitted version and copies it to a private draft without publishing', async () => {
  const submitted = { review_id: 'review-a', editor: 'bob', draft_revision: 2, title: 'Submitted report', submitted_at: '2026-10-23T05:00:00Z' }
  const copies: Record<string, unknown>[] = []
  const submissions: Record<string, unknown>[] = []
  vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
    const path = new URL(String(input), location.origin).pathname
    if (path.endsWith('/sources')) return json({ sources: [] })
    if (path.endsWith('/history')) return json({ revisions: [] })
    if (path.endsWith('/revisions')) return json({ revisions: [] })
    if (path.endsWith('/rebase')) { copies.push(JSON.parse(String(init?.body))); return json({ revision: 4 }) }
    if (path.endsWith('/drafts/draft-a/reviews')) { submissions.push(JSON.parse(String(init?.body))); return json({ review_id: 'own-review' }) }
    if (path.endsWith('/reviews/review-a')) return json({ ...submitted, spec: { ...spec, title: submitted.title } })
    if (path.endsWith('/reviews')) return json({ reviews: [submitted], next_cursor: null })
    if (path.endsWith(`/${state.report_id}`)) return json({ published_revision: 1 })
    throw new Error(`Unexpected request ${path}`)
  })
  const applied = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const screen = await render(<QueryClientProvider client={client}><ReportEditor spec={spec} state={state} onApplied={applied} publishedRevision={1} canUndo canRedo={false} canPublish /></QueryClientProvider>)
  await screen.getByRole('button', { name: 'History', exact: true }).click()
  await screen.getByRole('button', { name: 'Submit for review', exact: true }).click()
  await expect.element(screen.getByRole('button', { name: 'Revision submitted', exact: true })).toBeDisabled()
  expect(submissions).toHaveLength(1)
  expect(submissions[0]).toMatchObject({ expected_revision: 3 })
  await screen.getByRole('button', { name: 'Review submitted draft', exact: true }).click()
  await expect.element(screen.getByText('Copy the submitted revision into your private draft, replacing its current definition.', { exact: false })).toBeVisible()
  expect(copies).toHaveLength(0)
  await screen.getByRole('button', { name: 'Apply to draft', exact: true }).click()
  await expect.poll(() => applied.mock.calls).toEqual([[4]])
  expect(copies).toHaveLength(1)
  expect(copies[0]).toMatchObject({ expected_revision: 3, expected_published_revision: 1, review_id: 'review-a' })
  await screen.unmount()
  client.clear()
})
