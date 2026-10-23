import { describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import fixture from './fixtures/basic.json'
import { reportSpecSchema, viewStateSchema, type RenderResult } from './contracts'
import { SmartReportRenderer } from './renderer'
import { parseAssistantEvent } from '@/features/assistant/events'
import { replayThread } from '@/features/studio/studio-transcript'

const spec = reportSpecSchema.parse(fixture.spec)
const state = viewStateSchema.parse(fixture.state)
const result: RenderResult = {
  report_id: state.report_id, spec_revision: 1, state_revision: 1,
  state_fingerprint: 'same-scope', as_of: '2026-10-23T05:00:00Z', status: 'partial_success',
  widgets: [
    { widget_id: 'revenue', status: 'success', columns: ['total_revenue'], rows: [['9007199254740993.12']], totals: {}, completeness: 'complete', has_more: false, not_applied: [], query_fingerprint: 'q1', query_id: 'engine-1', semantic_view_id: 'sales-view', semantic_version: 1, evaluated_at: '2026-10-23T05:00:00Z', message: null },
    { widget_id: 'cities', status: 'denied', columns: [], rows: [], totals: {}, completeness: 'error', has_more: false, not_applied: [], query_fingerprint: null, query_id: null, semantic_view_id: null, semantic_version: null, evaluated_at: null, message: 'Access unavailable' },
  ],
}

describe('Smart Report contracts and renderer', () => {
  it('rejects server-owned fields and preserves precision', () => {
    expect(reportSpecSchema.safeParse({ ...fixture.spec, owner: 'forged' }).success).toBe(false)
    expect(viewStateSchema.parse(fixture.state).filters.city.values).toEqual(['Jakarta'])
  })

  it('keeps available widgets visible while reporting a denied widget', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const screen = await render(<QueryClientProvider client={client}><SmartReportRenderer spec={{ ...spec, filters: [] }} state={state} result={result} /></QueryClientProvider>)
    await expect.element(screen.getByText('Some widgets are unavailable. Available results are shown below.')).toBeVisible()
    await expect.element(screen.getByText('9.007.199.254.740.993,12')).toBeVisible()
    expect(screen.container.querySelector('[title="9007199254740993.12"]')).not.toBeNull()
    await expect.element(screen.getByText('Access unavailable').first()).toBeVisible()
  })

  it('emits state operations for keyboard selection without editing the definition', async () => {
    const onEvent = vi.fn()
    const screen = await render(<SmartReportRenderer spec={{ ...spec, filters: [] }} state={state} result={result} onEvent={onEvent} />)
    await screen.getByRole('button', { name: 'Select', exact: true }).first().click()
    expect(onEvent).toHaveBeenCalledWith({ type: 'WIDGET_FOCUS', target: 'revenue' })
    expect(spec.filters[0].default.values).toEqual(['Jakarta'])
  })

  it('parses only safe server-issued references and replays the same block', () => {
    const block = { report_id: 'report-a', draft_id: 'draft-a', draft_revision: 1, title: 'Revenue', fallback: 'Open report' }
    const event = parseAssistantEvent('smart_report', JSON.stringify({ payload: block, content_id: 'content-0', content_index: 0 }))
    expect(event?.type).toBe('smart_report')
    expect(parseAssistantEvent('smart_report', JSON.stringify({ payload: { ...block, rows: [['secret']] }, content_id: 'content-0', content_index: 0 }))).toBeNull()
    const turns = replayThread([
      { message_id: 'u', role: 'user', content: 'Make a report', created_at: '2026-10-23T05:00:00Z', steps: [] },
      { message_id: 'a', role: 'assistant', content: '', created_at: '2026-10-23T05:00:01Z', steps: [{ kind: 'smart_report', content_id: 'content-0', content_index: 0, ...block }] },
    ])
    expect(turns[0].content[0]).toMatchObject({ type: 'smart_report', complete: true, block })
  })
})
